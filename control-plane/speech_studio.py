"""Local Speech Studio jobs backed by Ariadne's bundled audio.cpp runtime."""
from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid
import wave
from typing import Any, Callable


JOB_ID = re.compile(r"^[a-f0-9]{32}$")
VOICE_ID = re.compile(r"^[a-f0-9]{32}$")
SUPPORTED_INPUTS = {".mp3", ".wav", ".m4a", ".mp4", ".webm", ".ogg", ".mov", ".mkv"}
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
MIN_FREE_GPU_BYTES = 9 * 1024**3


def assess_gpu_handoff(
    gpu: dict[str, Any],
    gpu_owner: dict[str, Any],
    in_flight: dict[str, Any],
    catalog: dict[str, Any],
) -> dict[str, Any]:
    """Check current admission safety and any required idle-model handoff."""
    def blocked(detail: str) -> dict[str, Any]:
        return {"ready": False, "safe_to_submit": False, "detail": detail}

    if gpu.get("available") is not True:
        return blocked(str(gpu.get("detail") or "GPU WDDM telemetry is unavailable; Speech handoff is blocked."))
    try:
        free_bytes = int(gpu.get("free_bytes") or 0)
    except (TypeError, ValueError, OverflowError):
        free_bytes = 0
    if free_bytes <= 0:
        return blocked("GPU WDDM telemetry has no valid free-memory measurement; Speech handoff is blocked.")
    if str(gpu_owner.get("current_gpu_owner") or "UNKNOWN").upper() not in {"AI", "NONE"}:
        return blocked(str(gpu_owner.get("detail") or "Another GPU workload owns the GPU."))
    if gpu_owner.get("transition_state") != "IDLE":
        return blocked(str(gpu_owner.get("detail") or "A GPU ownership transition is in progress."))
    if gpu_owner.get("delegation_state") != "ARIADNE_ACTIVE":
        return blocked("Ariadne has delegated GPU ownership; Speech admission is paused.")
    try:
        admissions = int(in_flight.get("admissions"))
    except (TypeError, ValueError, OverflowError):
        admissions = -1
    try:
        session_model_leases = int(in_flight.get("session_model_leases", 0))
    except (TypeError, ValueError, OverflowError):
        session_model_leases = -1
    if (in_flight.get("busy") is not False or admissions != 0 or session_model_leases != 0
            or bool(in_flight.get("local_models")) or bool(in_flight.get("vault_jobs"))):
        return blocked("An active Ariadne GPU or model lease prevents Speech handoff.")
    if free_bytes >= MIN_FREE_GPU_BYTES:
        return {
            "ready": False,
            "safe_to_submit": True,
            "detail": "Measured WDDM free memory meets Speech's admission requirement.",
        }
    if catalog.get("available") is not True:
        return blocked("Rust Host could not verify idle Ariadne model residency; Speech handoff is blocked.")
    loaded = catalog.get("loaded_details")
    if not isinstance(loaded, list) or not loaded:
        return blocked("No verified idle Ariadne model allocation can be reclaimed; measured free VRAM is insufficient.")

    reclaimable_bytes = 0
    for item in loaded:
        if not isinstance(item, dict):
            return blocked("Rust Host returned an incomplete resident-model snapshot; Speech handoff is blocked.")
        size_vram = item.get("size_vram")
        if isinstance(size_vram, bool) or not isinstance(size_vram, (int, float)):
            return blocked("Rust Host could not quantify resident model VRAM; Speech handoff is blocked.")
        try:
            allocation = int(size_vram)
        except (ValueError, OverflowError):
            return blocked("Rust Host returned an invalid resident-model VRAM allocation; Speech handoff is blocked.")
        if allocation < 0 or allocation != size_vram:
            return blocked("Rust Host returned an invalid resident-model VRAM allocation; Speech handoff is blocked.")
        reclaimable_bytes += allocation

    projected_free = free_bytes + reclaimable_bytes
    if reclaimable_bytes <= 0 or projected_free < MIN_FREE_GPU_BYTES:
        return {
            "ready": False,
            "safe_to_submit": False,
            "projected_free_bytes": projected_free,
            "reclaimable_vram_bytes": reclaimable_bytes,
            "detail": f"Speech requires {MIN_FREE_GPU_BYTES / 1024**3:.0f} GiB free; measured free plus idle Ariadne allocations is only {projected_free / 1024**3:.1f} GiB.",
        }
    return {
        "ready": True,
        "safe_to_submit": True,
        "projected_free_bytes": projected_free,
        "reclaimable_vram_bytes": reclaimable_bytes,
        "reclaimable_model_count": len(loaded),
        "detail": f"Ready after GPU handoff. Rust Host can reclaim {len(loaded)} idle Ariadne model(s), then will verify actual WDDM capacity before Speech starts.",
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class SpeechStudio:
    """Durable job and voice-reference store with one managed inference job at a time."""

    def __init__(
        self,
        runtime_root: Path,
        ffmpeg: Path | None,
        output_root: Path | Callable[[], Path],
        private_root: Path,
        acquire_gpu: Callable[[str, int], AbstractContextManager[str]],
        verify_gpu: Callable[[str, int], object],
        gpu_status: Callable[[], dict[str, Any]],
        gpu_owner_status: Callable[[], dict[str, Any]],
        model_lock_path: Path | None = None,
        handoff_status: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.runtime_root = runtime_root
        self.ffmpeg = ffmpeg
        self._output_root = output_root
        self.private_root = private_root
        self.jobs_root = private_root / "jobs"
        self.voice_root = private_root / "voices"
        self.input_root = private_root / "inputs"
        self.acquire_gpu = acquire_gpu
        self.verify_gpu = verify_gpu
        self.gpu_status = gpu_status
        self.gpu_owner_status = gpu_owner_status
        self.handoff_status = handoff_status
        self.model_lock_path = model_lock_path or Path(__file__).with_name("speech-models.lock.json")
        try:
            self.model_lock = json.loads(self.model_lock_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            self.model_lock = {}
        self.integrity_cache: dict[tuple[str, int, int], str] = {}
        self.lock = threading.RLock()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.processes: dict[str, subprocess.Popen[str]] = {}
        self.cancel_events: dict[str, threading.Event] = {}
        self._load_jobs()

    @property
    def executable(self) -> Path:
        return self.runtime_root / "app" / "audiocpp_cli.exe"

    @property
    def output_root(self) -> Path:
        return self._output_root() if callable(self._output_root) else self._output_root

    @property
    def asr_model(self) -> Path:
        return self.runtime_root / "models" / "Qwen3-ASR-0.6B-GGUF" / "qwen3-asr-0.6b-q8_0.gguf"

    @property
    def tts_model(self) -> Path:
        return self.runtime_root / "models" / "VoxCPM2-GGUF" / "voxcpm2-q8_0.gguf"

    def _load_jobs(self) -> None:
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        for path in sorted(self.jobs_root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:100]:
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(job, dict) or not JOB_ID.fullmatch(str(job.get("id", ""))):
                    continue
                if job.get("state") in {"queued", "running", "stopping"}:
                    job.update(state="error", stage="Interrupted", message="Ariadne restarted before this speech job completed.", updated_at=_now())
                    _atomic_json(path, job)
                self.jobs[str(job["id"])] = job
            except (OSError, ValueError, TypeError):
                continue

    def _model_status(self, package: str, path: Path, license_name: str) -> dict[str, Any]:
        present = path.is_file()
        record = (self.model_lock.get("models") or {}).get(package, {}) if isinstance(self.model_lock, dict) else {}
        integrity = "missing"
        actual_bytes = 0
        if present:
            stat_result = path.stat()
            actual_bytes = stat_result.st_size
            expected_size = int(record.get("size_bytes") or 0)
            expected_hash = str(record.get("sha256") or "").lower()
            if not expected_hash or not expected_size:
                integrity = "unlocked"
            elif actual_bytes != expected_size:
                integrity = "size_mismatch"
            else:
                cache_key = (str(path.resolve()), stat_result.st_size, stat_result.st_mtime_ns)
                integrity = self.integrity_cache.get(cache_key, "")
                if not integrity:
                    digest = hashlib.sha256()
                    with path.open("rb") as model_file:
                        for block in iter(lambda: model_file.read(8 * 1024 * 1024), b""):
                            digest.update(block)
                    final_stat = path.stat()
                    integrity = "verified" if (
                        final_stat.st_size == stat_result.st_size
                        and final_stat.st_mtime_ns == stat_result.st_mtime_ns
                        and digest.hexdigest() == expected_hash
                    ) else "checksum_mismatch"
                    self.integrity_cache[cache_key] = integrity
        installed = present and integrity == "verified"
        metadata: dict[str, Any] = {}
        try:
            manifest = next((path.parent.glob(f".audiocpp-package-{package}.json")), None)
            if manifest and manifest.is_file():
                metadata = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            metadata = {}
        return {
            "package": package,
            "installed": installed,
            "present": present,
            "integrity": integrity,
            "path": str(path),
            "bytes": actual_bytes,
            "license": str(record.get("license") or license_name),
            "locked_sha256": str(record.get("sha256") or ""),
            "revision": metadata.get("revision") or metadata.get("resolved_revision") or "unknown",
        }

    def status(self) -> dict[str, Any]:
        runtime_ok = self.executable.is_file()
        models = [
            self._model_status("qwen3_asr_0_6b_q8_0", self.asr_model, "Apache-2.0"),
            self._model_status("voxcpm2_q8_0", self.tts_model, "Apache-2.0"),
        ]
        with self.lock:
            active = next((item for item in self.jobs.values() if item.get("state") in {"queued", "running", "stopping"}), None)
        gpu = self.gpu_status()
        gpu_owner = self.gpu_owner_status()
        exclusive_owner = str(gpu_owner.get("current_gpu_owner") or "UNKNOWN").upper()
        free_bytes = int(gpu.get("free_bytes") or 0)
        handoff = {"ready": False, "detail": ""}
        telemetry_available = bool(gpu.get("available")) and free_bytes > 0
        owner_available = (
            exclusive_owner in {"AI", "NONE"}
            and gpu_owner.get("transition_state") == "IDLE"
            and gpu_owner.get("delegation_state", "ARIADNE_ACTIVE") == "ARIADNE_ACTIVE"
        )
        if (runtime_ok and all(model["installed"] for model in models)
                and telemetry_available and owner_available and not active and self.handoff_status):
            try:
                candidate = self.handoff_status(gpu, gpu_owner)
                if isinstance(candidate, dict):
                    handoff = candidate
            except Exception as exc:
                handoff = {
                    "ready": False,
                    "safe_to_submit": False,
                    "detail": f"Could not safely assess GPU admission: {exc}",
                }
        if not runtime_ok:
            state = "offline"
            detail = "The bundled Windows audio.cpp runtime is not installed."
        elif any(not model["installed"] for model in models):
            state = "provider_required"
            detail = "Install or restore the locked local speech models before opening Speech Studio."
        elif active:
            state = "busy"
            detail = f"{active.get('operation', 'Speech job')} is {active.get('stage', 'running').lower()}."
        elif not telemetry_available:
            state = "blocked"
            detail = str(gpu.get("detail") or "GPU WDDM telemetry is unavailable; inference is blocked.")
        elif not owner_available:
            state = "blocked"
            detail = str(gpu_owner.get("detail") or "GPU ownership or an active lease prevents Speech admission.")
        elif self.handoff_status and handoff.get("safe_to_submit") is not True:
            state = "blocked"
            detail = str(handoff.get("detail") or "An active Ariadne GPU or model lease prevents Speech admission.")
        elif free_bytes < MIN_FREE_GPU_BYTES and handoff.get("ready") is True:
            state = "ready_after_handoff"
            detail = str(handoff.get("detail") or "Ready after GPU handoff. Rust Host will verify capacity before starting Speech.")
        elif free_bytes < MIN_FREE_GPU_BYTES:
            state = "blocked"
            detail = str(handoff.get("detail") or f"Speech inference requires {MIN_FREE_GPU_BYTES / 1024**3:.0f} GiB free WDDM memory; {gpu.get('free_gb', 0)} GiB is currently available.")
        else:
            state = "ready"
            detail = "Local audio.cpp ASR and VoxCPM2 speech synthesis are installed."
        return {
            "state": state,
            "lifecycle_state": state.upper(),
            "detail": detail,
            "runtime": "audio.cpp 0.8.0 · native Windows · Vulkan/CPU",
            "backend": "vulkan",
            "minimum_free_gpu_bytes": MIN_FREE_GPU_BYTES,
            "executable": str(self.executable),
            "models": models,
            "gpu": gpu,
            "gpu_owner": gpu_owner,
            "gpu_handoff": handoff,
            "output_root": str(self.output_root),
            "voice_root": str(self.voice_root),
            "job": self.snapshot(str(active["id"])) if active else None,
            "capabilities": {
                "asr": bool(runtime_ok and models[0]["installed"]),
                "tts": bool(runtime_ok and models[1]["installed"]),
                "voice_cloning": bool(runtime_ok and models[1]["installed"]),
                "voice_design": bool(runtime_ok and models[1]["installed"]),
                "microphone": True,
            },
        }

    def _save_job(self, job: dict[str, Any]) -> None:
        job["updated_at"] = _now()
        _atomic_json(self.jobs_root / f"{job['id']}.json", job)

    def snapshot(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            job = self.jobs.get(job_id)
            return json.loads(json.dumps(job)) if job else None

    def list_jobs(self) -> list[dict[str, Any]]:
        with self.lock:
            jobs = sorted(self.jobs.values(), key=lambda item: item.get("updated_at", ""), reverse=True)
            return [json.loads(json.dumps(job)) for job in jobs[:30]]

    def delete_job(self, job_id: str) -> tuple[dict[str, Any], int]:
        if not JOB_ID.fullmatch(job_id):
            return {"ok": False, "message": "Invalid speech job ID."}, 400
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return {"ok": False, "message": "Speech job not found."}, 404
            if job.get("state") in {"queued", "running", "stopping"}:
                return {"ok": False, "message": "Stop the active speech job before deleting its files."}, 409
            job = json.loads(json.dumps(job))
        output_root = Path(str(job.get("output_root") or self.output_root)).resolve()
        for folder in (self.input_root, self.jobs_root):
            for path in folder.glob(f"{job_id}.*"):
                if path.is_file():
                    path.unlink(missing_ok=True)
        for suffix in (".txt", ".segments.json", ".srt", ".wav", ".mp3"):
            candidate = output_root / f"{job_id}{suffix}"
            if candidate.is_file():
                candidate.unlink(missing_ok=True)
        with self.lock:
            self.jobs.pop(job_id, None)
            (self.jobs_root / f"{job_id}.json").unlink(missing_ok=True)
        return {"ok": True, "message": "Speech job history and its local input/output files were deleted."}, 200

    def list_voices(self) -> list[dict[str, Any]]:
        self.voice_root.mkdir(parents=True, exist_ok=True)
        voices: list[dict[str, Any]] = []
        for path in self.voice_root.glob("*.json"):
            try:
                voice = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(voice, dict) and VOICE_ID.fullmatch(str(voice.get("id", ""))):
                    voice["available"] = (self.voice_root / f"{voice['id']}.wav").is_file()
                    voices.append(voice)
            except (OSError, ValueError):
                continue
        return sorted(voices, key=lambda item: item.get("created_at", ""), reverse=True)

    def _start_job(self, operation: str, input_path: Path | None, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        with self.lock:
            active = next((item for item in self.jobs.values() if item.get("state") in {"queued", "running", "stopping"}), None)
            if active:
                return {"ok": False, "message": "A speech job is already using the shared GPU.", "job": self.snapshot(str(active["id"]))}, 409
            if not self.executable.is_file():
                return {"ok": False, "message": "The bundled audio.cpp Windows runtime is missing."}, 503
            if operation == "transcription" and not self.asr_model.is_file():
                return {"ok": False, "message": "The local Qwen3 ASR model is not installed."}, 503
            if operation == "synthesis" and not self.tts_model.is_file():
                return {"ok": False, "message": "The local VoxCPM2 model is not installed."}, 503
            if not self.ffmpeg or not self.ffmpeg.is_file():
                return {"ok": False, "message": "FFmpeg is required for local audio conversion and MP3 export."}, 503
            admission = self.status()
            if admission.get("state") not in {"ready", "ready_after_handoff"}:
                message = str(admission.get("detail") or "GPU admission is currently blocked.")
                return {"ok": False, "message": message, "status": admission}, 409
            job_id = uuid.uuid4().hex
            job = {
                "id": job_id,
                "operation": operation,
                "state": "queued",
                "stage": "Waiting for shared GPU admission",
                "message": "Ariadne is checking measured GPU capacity and Rust Host ownership.",
                "input_path": str(input_path) if input_path else None,
                "output_root": str(self.output_root),
                "created_at": _now(),
                "updated_at": _now(),
                "payload": payload,
            }
            self.jobs[job_id] = job
            cancel_event = threading.Event()
            self.cancel_events[job_id] = cancel_event
            try:
                self._save_job(job)
            except OSError as exc:
                self.jobs.pop(job_id, None)
                self.cancel_events.pop(job_id, None)
                return {"ok": False, "message": f"Could not save speech job history: {exc}"}, 500
            thread = threading.Thread(target=self._run_job, args=(job_id,), daemon=True, name=f"speech-{operation}-{job_id[:8]}")
            try:
                thread.start()
            except RuntimeError as exc:
                self.jobs.pop(job_id, None)
                self.cancel_events.pop(job_id, None)
                return {"ok": False, "message": f"Could not start the local speech worker: {exc}"}, 500
            return {"ok": True, "job": json.loads(json.dumps(job))}, 202

    def submit_transcription(self, filename: str, raw: bytes, language: str = "auto") -> tuple[dict[str, Any], int]:
        name = Path(filename or "recording.wav").name
        suffix = Path(name).suffix.casefold()
        if suffix not in SUPPORTED_INPUTS:
            return {"ok": False, "message": "Choose MP3, WAV, M4A, MP4, WebM, OGG, MOV, or MKV audio/video."}, 415
        if not raw or len(raw) > MAX_UPLOAD_BYTES:
            return {"ok": False, "message": "The uploaded recording is empty or exceeds the 512 MiB limit."}, 413
        job_id = uuid.uuid4().hex
        self.input_root.mkdir(parents=True, exist_ok=True)
        input_path = self.input_root / f"{job_id}{suffix}"
        input_path.write_bytes(raw)
        result, status = self._start_job("transcription", input_path, {"filename": name, "language": language[:20]})
        if status != 202:
            input_path.unlink(missing_ok=True)
        return result, status

    def create_voice(self, filename: str, raw: bytes, name: str, transcript: str, description: str, consent: bool) -> tuple[dict[str, Any], int]:
        voice_name = " ".join(str(name or "").split())[:80]
        if not consent:
            return {"ok": False, "message": "Confirm that you own or have permission to use this recording before saving it as a voice reference."}, 400
        if not voice_name:
            return {"ok": False, "message": "Enter a name for this voice reference."}, 400
        suffix = Path(filename or "voice.wav").suffix.casefold()
        if suffix not in SUPPORTED_INPUTS or not raw or len(raw) > MAX_UPLOAD_BYTES:
            return {"ok": False, "message": "Provide a supported audio file no larger than 512 MiB."}, 415
        if not self.ffmpeg or not self.ffmpeg.is_file():
            return {"ok": False, "message": "FFmpeg is required to prepare a local WAV voice reference."}, 503
        voice_id = uuid.uuid4().hex
        self.voice_root.mkdir(parents=True, exist_ok=True)
        source_path = self.voice_root / f"{voice_id}.source{suffix}"
        wav_path = self.voice_root / f"{voice_id}.wav"
        source_path.write_bytes(raw)
        try:
            completed = subprocess.run([str(self.ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source_path), "-vn", "-ac", "1", "-ar", "16000", str(wav_path)], capture_output=True, text=True, timeout=180, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if completed.returncode or not wav_path.is_file() or wav_path.stat().st_size < 64:
                raise RuntimeError((completed.stderr or "Audio conversion failed.")[-1200:])
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            source_path.unlink(missing_ok=True)
            wav_path.unlink(missing_ok=True)
            return {"ok": False, "message": f"Could not prepare the voice recording: {exc}"}, 422
        try:
            with wave.open(str(wav_path), "rb") as clip:
                channels = clip.getnchannels()
                sample_rate = clip.getframerate()
                frame_count = clip.getnframes()
                sample_width = clip.getsampwidth()
        except (OSError, wave.Error) as exc:
            source_path.unlink(missing_ok=True)
            wav_path.unlink(missing_ok=True)
            return {"ok": False, "message": f"Could not inspect the prepared voice recording: {exc}"}, 422
        voice = {
            "id": voice_id,
            "name": voice_name,
            "reference_audio": str(wav_path),
            "reference_transcript": str(transcript or "").strip()[:20_000],
            "description": " ".join(str(description or "").split())[:300],
            "clip_analysis": {
                "duration_seconds": round(frame_count / sample_rate, 2) if sample_rate else 0,
                "sample_rate_hz": sample_rate,
                "channels": channels,
                "sample_width_bits": sample_width * 8,
                "normalized_format": "mono PCM WAV",
            },
            "representation": "Local reference WAV plus optional transcript; no persistent speaker embedding is created.",
            "consent_confirmed": True,
            "created_at": _now(),
            "source_filename": Path(filename or "voice recording").name,
            "license": "User-provided recording; rights confirmed by user.",
        }
        _atomic_json(self.voice_root / f"{voice_id}.json", voice)
        return {"ok": True, "voice": voice}, 201

    def delete_voice(self, voice_id: str) -> tuple[dict[str, Any], int]:
        if not VOICE_ID.fullmatch(voice_id):
            return {"ok": False, "message": "Invalid voice reference ID."}, 400
        meta_path = self.voice_root / f"{voice_id}.json"
        wav_path = self.voice_root / f"{voice_id}.wav"
        if not meta_path.is_file() and not wav_path.is_file():
            return {"ok": False, "message": "Voice reference not found."}, 404
        meta_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)
        for original in self.voice_root.glob(f"{voice_id}.source.*"):
            original.unlink(missing_ok=True)
        return {"ok": True, "message": "Local reference audio and profile metadata deleted."}, 200

    def submit_synthesis(self, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        text = str(body.get("text") or "").strip()
        voice_id = str(body.get("voice_id") or "")
        design = str(body.get("voice_design") or "").strip()[:500]
        if not text:
            return {"ok": False, "message": "Enter the speech text to synthesize."}, 400
        if len(text) > 20_000:
            return {"ok": False, "message": "Split longer text into passages under 20,000 characters."}, 413
        voice: dict[str, Any] | None = None
        if voice_id:
            voice = next((item for item in self.list_voices() if item.get("id") == voice_id and item.get("available")), None)
            if not voice:
                return {"ok": False, "message": "Select an available local voice reference or choose voice design."}, 400
        elif not design:
            return {"ok": False, "message": "Describe a synthetic voice or select a saved local voice reference."}, 400
        settings = {
            "text": text,
            "voice_id": voice_id or None,
            "voice_design": design or None,
            "reference_text": str(voice.get("reference_transcript") or "") if voice else "",
            "steps": max(1, min(40, int(body.get("steps") or 10))),
        }
        return self._start_job("synthesis", None, settings)

    def cancel(self, job_id: str) -> tuple[dict[str, Any], int]:
        if not JOB_ID.fullmatch(job_id):
            return {"ok": False, "message": "Invalid speech job ID."}, 400
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return {"ok": False, "message": "Speech job not found."}, 404
            if job.get("state") not in {"queued", "running", "stopping"}:
                return {"ok": True, "job": json.loads(json.dumps(job)), "message": "Speech job has already finished."}, 200
            job.update(state="stopping", stage="Stopping", message="Cancellation requested; the worker is being terminated.")
            self._save_job(job)
            cancel_event = self.cancel_events.get(job_id)
            if cancel_event:
                cancel_event.set()
            process = self.processes.get(job_id)
        if process and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        return {"ok": True, "job": self.snapshot(job_id)}, 202

    def _update(self, job_id: str, **values: Any) -> None:
        with self.lock:
            job = self.jobs[job_id]
            job.update(values)
            self._save_job(job)

    def _run_cli(self, job_id: str, command: list[str], log_path: Path, cancel_event: threading.Event, timeout: int = 7200) -> tuple[int, str]:
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            creationflags = subprocess.CREATE_NO_WINDOW
        log_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(command, cwd=self.runtime_root / "app", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", startupinfo=startupinfo, creationflags=creationflags)
            with self.lock:
                self.processes[job_id] = process
            try:
                while True:
                    if cancel_event.is_set():
                        process.terminate()
                        try:
                            process.wait(timeout=8)
                        except subprocess.TimeoutExpired:
                            process.kill()
                        raise InterruptedError("Speech job cancelled by user.")
                    if time.monotonic() - started > timeout:
                        process.kill()
                        raise TimeoutError("Speech inference exceeded its 2 hour deadline.")
                    try:
                        output, _ = process.communicate(timeout=0.5)
                        break
                    except subprocess.TimeoutExpired:
                        continue
                log.write(output or "")
                log.flush()
                return int(process.returncode or 0), (output or "")[-12_000:]
            finally:
                with self.lock:
                    if self.processes.get(job_id) is process:
                        self.processes.pop(job_id, None)

    @staticmethod
    def _srt_time(seconds: float) -> str:
        milliseconds = max(0, round(seconds * 1000))
        hours, milliseconds = divmod(milliseconds, 3_600_000)
        minutes, milliseconds = divmod(milliseconds, 60_000)
        secs, milliseconds = divmod(milliseconds, 1000)
        return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"

    def _write_srt(self, segments_path: Path, destination: Path) -> bool:
        try:
            value = json.loads(segments_path.read_text(encoding="utf-8"))
            rows = value.get("segments", value) if isinstance(value, dict) else value
            if not isinstance(rows, list):
                return False
            entries = []
            for index, row in enumerate(rows, 1):
                if not isinstance(row, dict):
                    continue
                start = row.get("start", row.get("start_time"))
                end = row.get("end", row.get("end_time"))
                text = row.get("text", row.get("content", ""))
                try:
                    begin = float(start)
                    finish = float(end)
                except (TypeError, ValueError):
                    continue
                if text:
                    entries.append(f"{index}\n{self._srt_time(begin)} --> {self._srt_time(finish)}\n{str(text).strip()}\n")
            if not entries:
                return False
            destination.write_text("\n".join(entries), encoding="utf-8")
            return True
        except (OSError, ValueError, TypeError):
            return False

    def _run_job(self, job_id: str) -> None:
        with self.lock:
            cancel_event = self.cancel_events.get(job_id)
        if cancel_event is None:
            return
        job = self.snapshot(job_id) or {}
        payload = dict(job.get("payload") or {})
        output_id = job_id
        output_root = Path(str(job.get("output_root") or self.output_root))
        log_path = self.jobs_root / f"{job_id}.log"
        wav_path = output_root / f"{output_id}.wav"
        mp3_path = output_root / f"{output_id}.mp3"
        try:
            if cancel_event.is_set():
                raise InterruptedError("Speech job cancelled before GPU admission.")
            output_root.mkdir(parents=True, exist_ok=True)
            if job.get("operation") == "transcription":
                self._update(job_id, state="running", stage="Preparing local audio", message="Extracting mono 16 kHz audio locally for ASR.")
                input_path = Path(str(job["input_path"]))
                prepared = self.jobs_root / f"{job_id}.input.wav"
                converted = subprocess.run([str(self.ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(input_path), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(prepared)], capture_output=True, text=True, timeout=300, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if converted.returncode or not prepared.is_file() or prepared.stat().st_size < 64:
                    raise RuntimeError(f"Local audio extraction failed: {(converted.stderr or 'no audio stream was produced')[-1200:]}")
                text_path = self.jobs_root / f"{job_id}.txt"
                segments_path = self.jobs_root / f"{job_id}.segments.json"
                command = [str(self.executable), "--task", "asr", "--family", "qwen3_asr", "--model", str(self.asr_model), "--backend", "vulkan", "--device", "0", "--audio", str(prepared), "--text-out", str(text_path), "--segments-out", str(segments_path), "--log-file", str(log_path), "--metrics"]
                language = str(payload.get("language") or "auto").lower()
                if language not in {"", "auto"}:
                    command.extend(["--language", language])
                with self.acquire_gpu("speech", MIN_FREE_GPU_BYTES) as lease_id:
                    self.verify_gpu(lease_id, MIN_FREE_GPU_BYTES)
                    if cancel_event.is_set():
                        raise InterruptedError("Speech job cancelled before inference started.")
                    self._update(job_id, stage="Transcribing locally", message="Qwen3 ASR is running under the Rust Host speech lease.")
                    code, output = self._run_cli(job_id, command, log_path, cancel_event)
                if cancel_event.is_set():
                    raise InterruptedError("Speech job cancelled by user.")
                if code or not text_path.is_file():
                    raise RuntimeError(f"ASR failed (exit {code}): {output[-1600:]}")
                transcript = text_path.read_text(encoding="utf-8", errors="replace").strip()
                if not transcript:
                    raise RuntimeError("The ASR runtime returned an empty transcript.")
                saved_text = output_root / f"{output_id}.txt"
                saved_text.write_text(transcript, encoding="utf-8")
                saved_segments = None
                saved_srt = None
                if segments_path.is_file():
                    try:
                        saved_segments = output_root / f"{output_id}.segments.json"
                        shutil.copy2(segments_path, saved_segments)
                        srt_path = output_root / f"{output_id}.srt"
                        if self._write_srt(segments_path, srt_path):
                            saved_srt = srt_path
                    except OSError:
                        saved_segments = None
                result = {
                    "transcript": transcript,
                    "text_path": str(saved_text),
                    "segments_path": str(saved_segments) if saved_segments else None,
                    "srt_path": str(saved_srt) if saved_srt else None,
                    "source_path": str(input_path),
                    "model": "Qwen3-ASR 0.6B Q8_0 · audio.cpp 0.8.0 · Vulkan",
                    "log_path": str(log_path),
                    "runtime_output": output[-2500:],
                }
            else:
                self._update(job_id, state="running", stage="Preparing voice generation", message="VoxCPM2 is waiting for Rust Host speech GPU admission.")
                text = str(payload["text"])
                design = str(payload.get("voice_design") or "").strip()
                command_text = f"({design}){text}" if design else text
                # VoxCPM2 0.8.0 accepts --task tts for all synthesis modes.
                # Voice design is encoded in the parenthesized text prefix;
                # cloning uses --voice-ref and an optional reference transcript.
                command = [str(self.executable), "--task", "tts", "--family", "voxcpm2", "--model", str(self.tts_model), "--backend", "vulkan", "--device", "0", "--num-inference-steps", str(payload.get("steps", 10)), "--text", command_text, "--out", str(wav_path), "--log-file", str(log_path), "--metrics"]
                voice_id = str(payload.get("voice_id") or "")
                if voice_id:
                    voice_path = self.voice_root / f"{voice_id}.wav"
                    if not voice_path.is_file():
                        raise RuntimeError("Selected voice reference was deleted before synthesis started.")
                    command.extend(["--voice-ref", str(voice_path)])
                    if payload.get("reference_text"):
                        command.extend(["--reference-text", str(payload["reference_text"])])
                with self.acquire_gpu("speech", MIN_FREE_GPU_BYTES) as lease_id:
                    self.verify_gpu(lease_id, MIN_FREE_GPU_BYTES)
                    if cancel_event.is_set():
                        raise InterruptedError("Speech job cancelled before inference started.")
                    self._update(job_id, stage="Synthesizing speech", message="VoxCPM2 is running under the Rust Host speech lease.")
                    code, output = self._run_cli(job_id, command, log_path, cancel_event)
                if cancel_event.is_set():
                    raise InterruptedError("Speech job cancelled by user.")
                if code or not wav_path.is_file() or wav_path.stat().st_size < 64:
                    raise RuntimeError(f"VoxCPM2 failed (exit {code}): {output[-1600:]}")
                self._update(job_id, stage="Encoding MP3", message="VoxCPM2 released the GPU lease; FFmpeg is encoding MP3 on the CPU.")
                converted = subprocess.run([str(self.ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav_path), "-vn", "-codec:a", "libmp3lame", "-b:a", "192k", str(mp3_path)], capture_output=True, text=True, timeout=300, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if converted.returncode or not mp3_path.is_file() or mp3_path.stat().st_size < 64:
                    raise RuntimeError(f"MP3 export failed: {(converted.stderr or 'no MP3 file was produced')[-1200:]}")
                result = {
                    "audio_path": str(wav_path),
                    "audio_url": f"/api/speech/audio/{output_id}",
                    "mp3_path": str(mp3_path),
                    "mp3_url": f"/api/speech/audio/{output_id}?format=mp3",
                    "model": "VoxCPM2 Q8_0 · audio.cpp 0.8.0 · Vulkan",
                    "voice_id": payload.get("voice_id"),
                    "voice_design": design or None,
                    "parameters": {"steps": int(payload.get("steps", 10)), "license": "Apache-2.0"},
                    "log_path": str(log_path),
                    "runtime_output": output[-2500:],
                }
            self._update(job_id, state="completed", stage="Complete", message="Output saved locally; the Rust Host speech GPU lease has been released.", result=result, finished_at=_now())
        except InterruptedError as exc:
            self._update(job_id, state="cancelled", stage="Cancelled", message=str(exc), finished_at=_now())
        except Exception as exc:
            self._update(job_id, state="error", stage="Failed", message=str(exc), finished_at=_now())
        finally:
            with self.lock:
                self.processes.pop(job_id, None)
                self.cancel_events.pop(job_id, None)

    def audio_path(self, job_id: str, format_name: str) -> Path:
        if not JOB_ID.fullmatch(job_id):
            raise FileNotFoundError("Speech output not found.")
        job = self.snapshot(job_id)
        if not job or job.get("state") != "completed" or job.get("operation") != "synthesis":
            raise FileNotFoundError("Speech output not found.")
        output_root = Path(str(job.get("output_root") or self.output_root))
        path = output_root / f"{job_id}.{ 'mp3' if format_name == 'mp3' else 'wav' }"
        if not path.is_file():
            raise FileNotFoundError("Speech output not found.")
        return path

    def export_path(self, job_id: str, kind: str) -> Path:
        if not JOB_ID.fullmatch(job_id):
            raise FileNotFoundError("Speech export not found.")
        job = self.snapshot(job_id)
        if not job or job.get("state") != "completed" or job.get("operation") != "transcription":
            raise FileNotFoundError("Speech export not found.")
        path = Path(str((job.get("result") or {}).get(f"{kind}_path") or ""))
        if kind not in {"text", "srt", "segments"} or not path.is_file():
            raise FileNotFoundError("Speech export not found.")
        return path

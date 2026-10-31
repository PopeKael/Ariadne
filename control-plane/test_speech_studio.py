from __future__ import annotations

import json
import hashlib
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
import wave
from contextlib import nullcontext
from unittest.mock import patch

from speech_studio import SpeechStudio, assess_gpu_handoff


class SpeechStudioTests(unittest.TestCase):
    def make_studio(self, root: Path, *, installed: bool = False) -> SpeechStudio:
        runtime = root / "runtime"
        (runtime / "app").mkdir(parents=True, exist_ok=True)
        (runtime / "app" / "audiocpp_cli.exe").write_bytes(b"runtime")
        model_files = {
            "qwen3_asr_0_6b_q8_0": ("Qwen3-ASR-0.6B-GGUF/qwen3-asr-0.6b-q8_0.gguf", b"asr"),
            "voxcpm2_q8_0": ("VoxCPM2-GGUF/voxcpm2-q8_0.gguf", b"tts"),
        }
        model_lock = root / "speech-models.lock.json"
        model_lock.write_text(json.dumps({"models": {
            package: {"source_file": filename, "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(), "license": "Apache-2.0"}
            for package, (filename, content) in model_files.items()
        }}), encoding="utf-8")
        if installed:
            for _package, (filename, content) in model_files.items():
                model_path = runtime / "models" / Path(filename)
                model_path.parent.mkdir(parents=True, exist_ok=True)
                model_path.write_bytes(content)
        ffmpeg = root / "ffmpeg.exe"
        ffmpeg.write_bytes(b"ffmpeg")
        return SpeechStudio(
            runtime,
            ffmpeg,
            root / "outputs",
            root / "private",
            lambda workload, reserve: nullcontext("test-lease"),
            lambda lease_id, reserve: {"ok": True},
            lambda: {"available": True, "free_gb": 12.0, "free_bytes": 12 * 1024**3, "total_gb": 16.0},
            lambda: {"current_gpu_owner": "AI", "transition_state": "IDLE"},
            model_lock,
        )

    def test_status_does_not_call_missing_models_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            status = self.make_studio(Path(directory)).status()
            self.assertEqual(status["state"], "provider_required")
            self.assertFalse(status["capabilities"]["asr"])
            self.assertFalse(status["capabilities"]["tts"])

    def test_status_blocks_when_gpu_measurement_or_ownership_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            studio = self.make_studio(Path(directory), installed=True)
            studio.gpu_status = lambda: {"available": False, "detail": "WDDM missing"}
            self.assertEqual(studio.status()["state"], "blocked")
            studio.gpu_status = lambda: {"available": True, "free_gb": 12, "free_bytes": 12 * 1024**3}
            studio.gpu_owner_status = lambda: {"current_gpu_owner": "IMAGE", "transition_state": "IDLE", "detail": "Image owns GPU"}
            self.assertEqual(studio.status()["state"], "blocked")
            studio.gpu_owner_status = lambda: {"current_gpu_owner": "AI", "transition_state": "IDLE"}
            studio.gpu_status = lambda: {"available": True, "free_gb": 8.5, "free_bytes": int(8.5 * 1024**3)}
            self.assertEqual(studio.status()["state"], "blocked")

    def test_status_allows_submit_when_idle_model_handoff_can_meet_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            studio = self.make_studio(Path(directory), installed=True)
            studio.gpu_status = lambda: {"available": True, "free_gb": 6.4, "free_bytes": int(6.4 * 1024**3), "total_gb": 15.8}
            studio.handoff_status = lambda _gpu, _owner: {
                "ready": True,
                "safe_to_submit": True,
                "reclaimable_model_count": 1,
                "reclaimable_vram_bytes": int(6.6 * 1024**3),
                "detail": "Ready after GPU handoff. Rust Host will verify actual WDDM capacity.",
            }
            status = studio.status()
            self.assertEqual(status["state"], "ready_after_handoff")
            self.assertTrue(status["gpu_handoff"]["ready"])
            self.assertIn("Ready after GPU handoff", status["detail"])

    def test_status_stays_blocked_when_idle_model_handoff_cannot_meet_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            studio = self.make_studio(Path(directory), installed=True)
            studio.gpu_status = lambda: {"available": True, "free_gb": 2.0, "free_bytes": 2 * 1024**3, "total_gb": 16.0}
            studio.handoff_status = lambda _gpu, _owner: {
                "ready": False,
                "safe_to_submit": False,
                "detail": "Speech requires 9 GiB free; measured free plus idle Ariadne allocations is only 7.0 GiB.",
            }
            status = studio.status()
            self.assertEqual(status["state"], "blocked")
            self.assertFalse(status["gpu_handoff"]["ready"])
            self.assertIn("only 7.0 GiB", status["detail"])

    def test_handoff_assessment_fails_closed_for_unsafe_owner_leases_or_capacity(self):
        gpu = {"available": True, "free_bytes": int(6.4 * 1024**3)}
        owner = {"current_gpu_owner": "AI", "transition_state": "IDLE", "delegation_state": "ARIADNE_ACTIVE"}
        in_flight = {"busy": False, "admissions": 0, "local_models": {}, "vault_jobs": []}
        catalog = {"available": True, "loaded_details": [{"name": "home", "size_vram": int(6.6 * 1024**3)}]}

        ready = assess_gpu_handoff(gpu, owner, in_flight, catalog)
        self.assertTrue(ready["ready"])
        self.assertEqual(ready["reclaimable_model_count"], 1)
        self.assertIn("Rust Host", ready["detail"])

        unsafe = [
            ({**gpu, "available": False}, owner, in_flight, catalog),
            (gpu, {**owner, "current_gpu_owner": "IMAGE"}, in_flight, catalog),
            (gpu, {**owner, "transition_state": "ACQUIRING"}, in_flight, catalog),
            (gpu, {**owner, "delegation_state": "EXTERNAL"}, in_flight, catalog),
            (gpu, owner, {**in_flight, "busy": True, "admissions": 1}, catalog),
            ({"available": True, "free_bytes": 12 * 1024**3}, owner,
             {**in_flight, "session_model_leases": 1}, {}),
            (gpu, owner, in_flight, {"available": False, "loaded_details": []}),
            (gpu, owner, in_flight, {"available": True, "loaded_details": [{"name": "home", "size_vram": None}]}),
            ({**gpu, "free_bytes": 2 * 1024**3}, owner, in_flight, catalog),
        ]
        for values in unsafe:
            with self.subTest(values=values):
                self.assertFalse(assess_gpu_handoff(*values)["safe_to_submit"])

    def test_status_blocks_active_model_lease_even_when_free_memory_is_sufficient(self):
        with tempfile.TemporaryDirectory() as directory:
            studio = self.make_studio(Path(directory), installed=True)
            studio.handoff_status = lambda _gpu, _owner: {
                "ready": False,
                "safe_to_submit": False,
                "detail": "An active Ariadne GPU or model lease prevents Speech handoff.",
            }
            status = studio.status()
            self.assertEqual(status["state"], "blocked")
            self.assertIn("active Ariadne GPU or model lease", status["detail"])

    def test_submit_refuses_active_model_lease_even_when_free_memory_is_sufficient(self):
        with tempfile.TemporaryDirectory() as directory:
            studio = self.make_studio(Path(directory), installed=True)
            studio.handoff_status = lambda _gpu, _owner: {
                "ready": False,
                "safe_to_submit": False,
                "detail": "An active Ariadne GPU or model lease prevents Speech handoff.",
            }
            result, status = studio.submit_transcription("recording.wav", b"test audio")
            self.assertEqual(status, 409)
            self.assertFalse(result["ok"])
            self.assertEqual(studio.list_jobs(), [])

    def test_submit_refuses_when_gpu_handoff_is_not_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            studio = self.make_studio(Path(directory), installed=True)
            studio.gpu_status = lambda: {"available": True, "free_gb": 6.0, "free_bytes": 6 * 1024**3, "total_gb": 16.0}
            studio.handoff_status = lambda _gpu, _owner: {
                "ready": False,
                "detail": "An active Ariadne GPU or model lease prevents Speech handoff.",
            }
            result, status = studio.submit_transcription("recording.wav", b"test audio")
            self.assertEqual(status, 409)
            self.assertFalse(result["ok"])
            self.assertIn("active Ariadne GPU or model lease", result["message"])
            self.assertEqual(studio.list_jobs(), [])

    def test_model_hash_mismatch_is_not_reported_as_installed(self):
        with tempfile.TemporaryDirectory() as directory:
            studio = self.make_studio(Path(directory), installed=True)
            studio.tts_model.write_bytes(b"bad")
            status = studio.status()
            tts = next(model for model in status["models"] if model["package"] == "voxcpm2_q8_0")
            self.assertFalse(tts["installed"])
            self.assertEqual(tts["integrity"], "checksum_mismatch")
            self.assertEqual(status["state"], "provider_required")

    def test_voice_library_requires_consent_and_deletes_reference_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            studio = self.make_studio(Path(directory))
            rejected, status = studio.create_voice("voice.wav", b"audio", "My voice", "words", "", False)
            self.assertEqual(status, 400)
            self.assertFalse(rejected["ok"])

            def convert(command, **_kwargs):
                with wave.open(command[-1], "wb") as clip:
                    clip.setnchannels(1)
                    clip.setsampwidth(2)
                    clip.setframerate(16000)
                    clip.writeframes(b"\x00\x00" * 16000)
                return type("Completed", (), {"returncode": 0, "stderr": ""})()

            with patch("speech_studio.subprocess.run", side_effect=convert):
                created, status = studio.create_voice("reference.mp3", b"audio", "My voice", "Hello there.", "Clear and steady", True)
            self.assertEqual(status, 201)
            voice = created["voice"]
            self.assertTrue(Path(voice["reference_audio"]).is_file())
            self.assertEqual(studio.list_voices()[0]["reference_transcript"], "Hello there.")
            self.assertEqual(voice["clip_analysis"]["duration_seconds"], 1.0)
            self.assertEqual(voice["description"], "Clear and steady")
            removed, status = studio.delete_voice(voice["id"])
            self.assertEqual(status, 200)
            self.assertFalse(Path(voice["reference_audio"]).exists())
            self.assertEqual(studio.list_voices(), [])

    def test_transcription_upload_validation_and_missing_model_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            studio = self.make_studio(Path(directory))
            _, status = studio.submit_transcription("recording.exe", b"data")
            self.assertEqual(status, 415)
            result, status = studio.submit_transcription("recording.wav", b"wav bytes")
            self.assertEqual(status, 503)
            self.assertFalse(result["ok"])
            self.assertEqual(list((Path(directory) / "private" / "inputs").glob("*")), [])

    def test_completed_timestamp_segments_export_as_srt(self):
        with tempfile.TemporaryDirectory() as directory:
            studio = self.make_studio(Path(directory))
            source = Path(directory) / "segments.json"
            target = Path(directory) / "transcript.srt"
            source.write_text(json.dumps({"segments": [{"start": 1.25, "end": 3.5, "text": "Hello there."}]}), encoding="utf-8")
            self.assertTrue(studio._write_srt(source, target))
            self.assertIn("00:00:01,250 --> 00:00:03,500", target.read_text(encoding="utf-8"))

    def test_voxcpm2_design_and_clone_use_tts_task_with_mode_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            studio = self.make_studio(Path(directory), installed=True)
            voice_id = "d" * 32
            studio.voice_root.mkdir(parents=True, exist_ok=True)
            (studio.voice_root / f"{voice_id}.wav").write_bytes(b"reference")
            (studio.voice_root / f"{voice_id}.json").write_text(json.dumps({
                "id": voice_id,
                "name": "Test voice",
                "reference_transcript": "Words from the voice reference.",
            }), encoding="utf-8")
            commands = []

            def run_cli(_job_id, command, _log_path, _cancel_event):
                commands.append(command)
                Path(command[command.index("--out") + 1]).write_bytes(b"wav-output" * 12)
                return 0, "VoxCPM2 test output"

            def run_ffmpeg(command, **_kwargs):
                Path(command[-1]).write_bytes(b"mp3-output" * 12)
                return subprocess.CompletedProcess(command, 0, "", "")

            def wait_for(job_id):
                for _ in range(200):
                    job = studio.snapshot(job_id)
                    if job["state"] in {"completed", "error", "cancelled"}:
                        return job
                    time.sleep(0.01)
                self.fail("Speech synthesis did not finish in the test window.")

            with patch.object(studio, "_run_cli", side_effect=run_cli), patch("speech_studio.subprocess.run", side_effect=run_ffmpeg):
                designed, status = studio.submit_synthesis({
                    "text": "A designed voice sample.",
                    "voice_design": "Clear and relaxed",
                    "steps": 6,
                })
                self.assertEqual(status, 202)
                designed_job = wait_for(designed["job"]["id"])
                self.assertEqual(designed_job["state"], "completed", designed_job["message"])

                cloned, status = studio.submit_synthesis({
                    "text": "A cloned voice sample.",
                    "voice_id": voice_id,
                    "reference_text": "Words from the voice reference.",
                    "steps": 6,
                })
                self.assertEqual(status, 202)
                cloned_job = wait_for(cloned["job"]["id"])
                self.assertEqual(cloned_job["state"], "completed", cloned_job["message"])

            self.assertEqual([command[command.index("--task") + 1] for command in commands], ["tts", "tts"])
            design_text = commands[0][commands[0].index("--text") + 1]
            self.assertEqual(design_text, "(Clear and relaxed)A designed voice sample.")
            self.assertIn("--voice-ref", commands[1])
            self.assertIn("--reference-text", commands[1])
            self.assertEqual(commands[1][commands[1].index("--reference-text") + 1], "Words from the voice reference.")

    def test_interrupted_jobs_persist_as_interrupted_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            studio = self.make_studio(root)
            job = {"id": "a" * 32, "operation": "transcription", "state": "running", "stage": "ASR", "created_at": "2026-10-31T00:00:00+00:00", "updated_at": "2026-10-31T00:00:00+00:00"}
            studio._save_job(job)
            reopened = self.make_studio(root)
            snapshot = reopened.snapshot("a" * 32)
            self.assertEqual(snapshot["state"], "error")
            self.assertIn("restarted", snapshot["message"])

    def test_cancelled_queued_job_does_not_acquire_gpu_after_worker_start(self):
        with tempfile.TemporaryDirectory() as directory:
            studio = self.make_studio(Path(directory), installed=True)
            job_id = "b" * 32
            studio.jobs[job_id] = {
                "id": job_id,
                "operation": "synthesis",
                "state": "queued",
                "stage": "Waiting for shared GPU admission",
                "message": "queued",
                "payload": {"text": "Hello", "voice_design": "gentle voice"},
                "created_at": "2026-10-31T00:00:00+00:00",
                "updated_at": "2026-10-31T00:00:00+00:00",
            }
            studio.cancel_events[job_id] = threading.Event()
            acquired = []
            studio.acquire_gpu = lambda workload, reserve: (acquired.append((workload, reserve)) or nullcontext("test-lease"))
            result, status = studio.cancel(job_id)
            self.assertEqual(status, 202)
            studio._run_job(job_id)
            self.assertEqual(acquired, [])
            self.assertEqual(studio.snapshot(job_id)["state"], "cancelled")

    def test_delete_job_removes_only_its_local_inputs_and_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            studio = self.make_studio(root)
            job_id = "c" * 32
            output_root = root / "outputs"
            input_root = root / "private" / "inputs"
            jobs_root = root / "private" / "jobs"
            for folder in (output_root, input_root, jobs_root):
                folder.mkdir(parents=True, exist_ok=True)
            expected = [output_root / f"{job_id}.txt", input_root / f"{job_id}.wav", jobs_root / f"{job_id}.log"]
            unrelated = output_root / "keep.txt"
            for path in expected + [unrelated]:
                path.write_text("data", encoding="utf-8")
            studio.jobs[job_id] = {
                "id": job_id,
                "operation": "transcription",
                "state": "completed",
                "output_root": str(output_root),
                "updated_at": "2026-10-31T00:00:00+00:00",
            }
            studio._save_job(studio.jobs[job_id])
            result, status = studio.delete_job(job_id)
            self.assertEqual(status, 200)
            self.assertTrue(result["ok"])
            self.assertTrue(all(not path.exists() for path in expected))
            self.assertTrue(unrelated.exists())
            self.assertIsNone(studio.snapshot(job_id))


if __name__ == "__main__":
    unittest.main()

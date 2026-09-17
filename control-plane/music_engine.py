"""Small, replaceable local song-generation adapter for Ariadne.

This module deliberately knows how to invoke one proven backend, but it owns no
web state, projects, or acceptance policy.  A future backend only needs the
same ``status`` and ``generate`` surface.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any


@dataclass(frozen=True)
class MusicRequest:
    lyrics: str
    style: str
    duration_seconds: float
    seed: int
    inference_steps: int


class AudioCppMiniMaxEngine:
    """Run the installed MiniMax Music 3 Q4 audio.cpp package on demand."""

    provider_id = "ariadne-local"
    display_name = "Ariadne Local · MiniMax Music 3 Q4"
    family = "minimax_music3"
    backend = "vulkan"
    mp3_bitrate = "192k"

    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = runtime_root

    @property
    def executable(self) -> Path:
        return self.runtime_root / "app" / "audiocpp_cli.exe"

    @property
    def model_root(self) -> Path:
        return self.runtime_root / "models" / "MiniMax-Music3-GGUF"

    @property
    def ffmpeg(self) -> Path | None:
        configured = os.environ.get("ARIADNE_FFMPEG_PATH", "").strip()
        candidates = [Path(configured)] if configured else []
        located = shutil.which("ffmpeg")
        if located:
            candidates.append(Path(located))
        candidates.append(Path(r"C:\Program Files\AMD\AI_Bundle\Amuse\ffmpeg.exe"))
        return next((candidate for candidate in candidates if candidate.is_file()), None)

    def status(self) -> dict[str, Any]:
        required = (
            self.executable,
            self.model_root / "language_model_q4_0.gguf",
            self.model_root / "rvq_depth_decoder_q8_0.gguf",
            self.model_root / "condition_encoder.gguf",
            self.model_root / "transformer_q4_0.gguf",
            self.model_root / "vocoder.gguf",
        )
        missing: list[str] = []
        for path in required:
            try:
                present = path.is_file()
            except OSError:
                present = False
            if not present:
                missing.append(str(path))
        return {
            "available": not missing,
            "runtime": "audio.cpp 0.8.0 · MiniMax Music 3 Q4 · Vulkan",
            "backend": self.backend,
            "model_root": str(self.model_root),
            "missing": missing,
            "bench_peak_gb": 10.43,
            "mp3_encoder": str(self.ffmpeg) if self.ffmpeg else None,
            "mp3_bitrate": self.mp3_bitrate,
        }

    def command(self, request: MusicRequest, output: Path, log_path: Path) -> list[str]:
        """Return an explicit, portable command line for audit/provenance."""
        return [
            str(self.executable),
            "--task", "gen",
            "--family", self.family,
            "--model", str(self.model_root),
            "--backend", self.backend,
            "--device", "0",
            "--text", request.style,
            "--lyrics", request.lyrics,
            "--request-option", f"duration_sec={request.duration_seconds:g}",
            "--request-option", f"num_inference_steps={request.inference_steps}",
            "--request-option", f"seed={request.seed}",
            "--session-option", "mem_saver=true",
            "--session-option", "rvq_depth_decoder_gguf=rvq_depth_decoder_q8_0.gguf",
            "--out", str(output),
            "--log-file", str(log_path),
            "--metrics",
        ]

    def generate(self, request: MusicRequest, output: Path, log_path: Path, timeout_seconds: float) -> subprocess.CompletedProcess[str]:
        output.parent.mkdir(parents=True, exist_ok=True)
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            # audio.cpp is a console executable. Keep its captured output in
            # Ariadne's log, not in a distracting black terminal window.
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            creationflags = subprocess.CREATE_NO_WINDOW
        return subprocess.run(
            self.command(request, output, log_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )

    def convert_to_mp3(self, wav: Path, mp3: Path, timeout_seconds: float) -> subprocess.CompletedProcess[str]:
        """Create the share copy without changing the lossless WAV master."""
        encoder = self.ffmpeg
        if encoder is None:
            raise FileNotFoundError("FFmpeg is not installed; cannot create the MP3 share copy.")
        mp3.parent.mkdir(parents=True, exist_ok=True)
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            creationflags = subprocess.CREATE_NO_WINDOW
        return subprocess.run(
            [str(encoder), "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav), "-vn", "-codec:a", "libmp3lame", "-b:a", self.mp3_bitrate, str(mp3)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

import server
from music_engine import AudioCppMiniMaxEngine, MusicRequest
from production_projects import ProductionProjectStore


class MusicEngineTests(unittest.TestCase):
    def test_progress_estimate_counts_flow_chunks_and_reports_remaining_time(self):
        log = "\n".join([
            "[TIMING] minimax_music3.ar.total_ms 97847.6",
            "[TIMING] minimax_music3.flow.total_ms 11250.0",
            "[TIMING] minimax_music3.flow.total_ms 11300.0",
            "[TIMING] minimax_music3.flow.total_ms 11275.0",
        ])
        estimate = server._music_progress_from_log(log, 104, 140)
        self.assertEqual(estimate["flow_count"], 3)
        self.assertEqual(estimate["flow_total"], 25)
        self.assertGreater(estimate["progress"], 25)
        self.assertLess(estimate["progress"], 95)
        self.assertGreater(estimate["remaining_seconds"], 0)

    def test_http_style_music_job_reports_completion_without_blocking_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Music"
            (root / "Candidates").mkdir(parents=True)
            fake_engine = Mock()
            fake_engine.status.return_value = {"available": True, "runtime": "test", "bench_peak_gb": 10.43}
            fake_engine.family = "minimax_music3"
            fake_engine.backend = "vulkan"

            def generate(_request, output, _log, _timeout):
                output.write_bytes(b"RIFF" + b"\x00" * 40)
                return subprocess.CompletedProcess([], 0, "", "")

            def convert(_wav, mp3, _timeout):
                mp3.write_bytes(b"ID3" + b"\x00" * 20)
                return subprocess.CompletedProcess([], 0, "", "")

            fake_engine.generate.side_effect = generate
            fake_engine.convert_to_mp3.side_effect = convert
            with (
                patch.object(server, "LOCAL_MUSIC_ENGINE", fake_engine),
                patch.object(server, "configuration_snapshot", return_value={"storage": {"music": str(root)}}),
                patch.object(server, "wan2gp_status", return_value={"state": "offline"}),
                patch.object(server, "gpu_status", return_value={"available": True, "free_gb": 12}),
                patch.object(server, "ai_gpu_admission", return_value=nullcontext()),
                patch.object(server, "announce_media_lifecycle"),
            ):
                with server.MUSIC_JOBS_LOCK:
                    server.MUSIC_JOBS.clear()
                queued, status = server.start_music_job({"style": "Test, 120 BPM", "enhanced_caption": "Global Metadata\nTest.\nVocal Details\nLead.\nArrangement\n[Verse] sparse.", "caption_preflight": {"model": "test", "completed": True}, "lyrics": "[Verse]\nA short lyric", "normalized_lyrics": "[Verse]\nA short lyric"})
                self.assertEqual(status, 202)
                self.assertEqual(queued["state"], "queued")
                deadline = time.monotonic() + 5
                snapshot = None
                while time.monotonic() < deadline:
                    snapshot = server._music_job_snapshot(queued["job_id"])
                    if snapshot and snapshot["state"] in {"succeeded", "failed"}:
                        break
                    time.sleep(0.02)
            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot["state"], "succeeded")
            self.assertEqual(snapshot["result"]["asset"]["status"], "candidate")

    def test_auto_duration_uses_bpm_structure_and_generation_headroom(self):
        plan = server.lyric_duration_plan("[Verse]\nOne two three four five six", "electropop, 128–132 BPM")
        self.assertEqual(plan["mode"], "auto")
        self.assertEqual(plan["bpm"], 130)
        self.assertEqual(plan["word_count"], 6)
        self.assertEqual(plan["target_seconds"], 30)
        self.assertGreater(plan["generation_seconds"], plan["target_seconds"])

    def test_lyrics_normalization_preserves_words_and_places_tags(self):
        result = server.normalize_minimax_lyrics("[verse] One two\n[pre chorus] Three four")
        self.assertTrue(result["valid"])
        self.assertEqual(result["normalized_lyrics"], "[Verse]\nOne two\n[Pre-Chorus]\nThree four")
        self.assertEqual(result["tag_count"], 2)

    def test_unknown_lyric_tag_is_reported_without_rewriting_words(self):
        result = server.normalize_minimax_lyrics("[Verse] One two\n[Whisper] Three four")
        self.assertFalse(result["valid"])
        self.assertIn("One two", result["normalized_lyrics"])
        self.assertIn("Three four", result["normalized_lyrics"])

    def test_common_song_section_aliases_are_normalized(self):
        result = server.normalize_minimax_lyrics("[Verse 1] One two\n[Guitar Solo]\n[Final Chorus] Three four")
        self.assertTrue(result["valid"])
        self.assertEqual(result["normalized_lyrics"], "[Verse]\nOne two\n[Solo]\n[Chorus]\nThree four")
        self.assertEqual(result["tag_count"], 3)

    def test_caption_enhancement_uses_local_llm_and_returns_editable_caption(self):
        fake_mcp = Mock()
        fake_mcp.ollama_chat.return_value = "```text\nGlobal Metadata\nWarm pop\nVocal Details\nLow lead\nArrangement\n[Verse] sparse\n```"
        with patch.object(server, "_home_mcp", return_value=fake_mcp), patch.object(server, "ai_gpu_admission", return_value=nullcontext()), patch.object(server, "model_activity", return_value=nullcontext()):
            result, status = server.enhance_music_caption({"style": "Warm pop, 100 BPM", "lyrics": "[verse]\nOne two"})
        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])
        self.assertTrue(result["enhanced_caption"].startswith("Global Metadata"))
        self.assertEqual(result["lyrics"]["normalized_lyrics"], "[Verse]\nOne two")
        fake_mcp.ollama_chat.assert_called_once()

    def test_explicit_duration_keeps_requested_target_separate_from_budget(self):
        plan = server.lyric_duration_plan("[Verse]\nOne two", "pop", "300")
        self.assertEqual(plan["target_seconds"], 300)
        self.assertEqual(plan["generation_seconds"], 330)

    def test_command_keeps_lyrics_style_and_memory_saver_explicit(self):
        engine = AudioCppMiniMaxEngine(Path("C:/audio.cpp"))
        request = MusicRequest("[Verse] Test", "Warm pop-rock", 20, 42, 16)
        command = engine.command(request, Path("C:/output.wav"), Path("C:/output.log"))
        self.assertIn("Warm pop-rock", command)
        self.assertIn("[Verse] Test", command)
        self.assertIn("mem_saver=true", command)
        self.assertIn("rvq_depth_decoder_gguf=rvq_depth_decoder_q8_0.gguf", command)

    def test_project_music_candidate_requires_manual_acceptance(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = ProductionProjectStore(Path(temporary) / "projects")
            project = store.create("Music Candidate")
            candidate = store.music_candidate_directory(project["project_id"]) / "song.wav"
            companion = candidate.with_suffix(".mp3")
            candidate.write_bytes(b"RIFF" + b"\x00" * 40)
            companion.write_bytes(b"ID3" + b"\x00" * 20)
            asset = store.record_music_candidate(project["project_id"], candidate, {"lyrics": "Exact lyric", "style": "Exact style"}, companion)
            self.assertEqual(asset["status"], "candidate")
            self.assertTrue(candidate.is_file())
            self.assertTrue(companion.is_file())
            self.assertEqual(json.loads(candidate.with_suffix(".json").read_text(encoding="utf-8"))["provenance"]["lyrics"], "Exact lyric")
            accepted = store.accept_music(project["project_id"], asset["asset_id"])
            self.assertEqual(accepted["status"], "accepted")
            self.assertFalse(candidate.exists())
            self.assertFalse(companion.exists())
            self.assertTrue((store.project_directory(project["project_id"]) / accepted["files"]["media"]).is_file())
            self.assertTrue((store.project_directory(project["project_id"]) / accepted["files"]["mp3"]).is_file())

    def test_generation_records_wav_and_exact_json_without_auto_accepting(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = ProductionProjectStore(Path(temporary) / "projects")
            project = store.create("Generated Music")
            fake_engine = Mock()
            fake_engine.status.return_value = {"available": True, "runtime": "test", "bench_peak_gb": 10.43}
            fake_engine.family = "minimax_music3"
            fake_engine.backend = "vulkan"
            def generate(_request, output, _log, _timeout):
                output.write_bytes(b"RIFF" + b"\x00" * 40)
                return subprocess.CompletedProcess([], 0, "", "")

            def convert(_wav, mp3, _timeout):
                mp3.write_bytes(b"ID3" + b"\x00" * 20)
                return subprocess.CompletedProcess([], 0, "", "")

            fake_engine.generate.side_effect = generate
            fake_engine.convert_to_mp3.side_effect = convert
            with (
                patch.object(server, "SEQUENCE_PROJECTS", store),
                patch.object(server, "LOCAL_MUSIC_ENGINE", fake_engine),
                patch.object(server, "wan2gp_status", return_value={"state": "offline"}),
                patch.object(server, "gpu_status", return_value={"available": True, "free_gb": 12}),
                patch.object(server, "release_idle_ollama_models", return_value={"available": True, "unloaded": ["qwen3.5:9b-q4_K_M"], "protected": [], "remaining": []}) as release_models,
                patch.object(server, "ai_gpu_admission", return_value=nullcontext()),
                patch.object(server, "announce_media_lifecycle"),
            ):
                result, status = server.generate_music({"project_id": project["project_id"], "title": "The First Test", "style": "Warm acoustic pop, 100 BPM", "enhanced_caption": "Global Metadata\nWarm acoustic pop at 100 BPM.\nVocal Details\nWarm lead.\nArrangement\n[Verse] sparse guitar.", "caption_preflight": {"model": "test", "completed": True}, "lyrics": "[Verse] Exact lyric", "normalized_lyrics": "[Verse]\nExact lyric", "seed": 7, "inference_steps": 16})
            self.assertEqual(status, 200)
            release_models.assert_called_once_with(force=True)
            self.assertEqual(result["asset"]["status"], "candidate")
            self.assertEqual(result["asset"]["provenance"]["lyrics"], "[Verse] Exact lyric")
            self.assertEqual(result["asset"]["provenance"]["enhanced_caption"].splitlines()[0], "Global Metadata")
            self.assertEqual(result["asset"]["provenance"]["normalized_lyrics"], "[Verse]\nExact lyric")
            self.assertEqual(result["music"]["title"], "The First Test")
            self.assertEqual(result["asset"]["provenance"]["title_source"], "user")
            self.assertIn("The First Test - ", result["music"]["filename"])
            self.assertEqual(result["duration_plan"]["mode"], "auto")
            self.assertGreaterEqual(result["duration_plan"]["target_seconds"], 30)
            self.assertGreater(result["duration_plan"]["generation_seconds"], result["duration_plan"]["target_seconds"])
            self.assertTrue((store.music_candidate_directory(project["project_id"]) / result["music"]["filename"]).is_file())
            self.assertTrue((store.music_candidate_directory(project["project_id"]) / result["music"]["mp3_filename"]).is_file())
            self.assertEqual(result["music"]["mp3_bitrate"], "192 kbps")

    def test_generation_infers_title_from_first_lyric_line_when_blank(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Music"
            (root / "Candidates").mkdir(parents=True)
            fake_engine = Mock()
            fake_engine.status.return_value = {"available": True, "runtime": "test", "bench_peak_gb": 10.43}
            fake_engine.family = "minimax_music3"
            fake_engine.backend = "vulkan"
            fake_engine.generate.side_effect = lambda _request, output, _log, _timeout: (output.write_bytes(b"RIFF" + b"\x00" * 40), subprocess.CompletedProcess([], 0, "", ""))[1]
            fake_engine.convert_to_mp3.side_effect = lambda _wav, mp3, _timeout: (mp3.write_bytes(b"ID3" + b"\x00" * 20), subprocess.CompletedProcess([], 0, "", ""))[1]
            with (
                patch.object(server, "LOCAL_MUSIC_ENGINE", fake_engine),
                patch.object(server, "configuration_snapshot", return_value={"storage": {"music": str(root)}}),
                patch.object(server, "wan2gp_status", return_value={"state": "offline"}),
                patch.object(server, "gpu_status", return_value={"available": True, "free_gb": 12}),
                patch.object(server, "ai_gpu_admission", return_value=nullcontext()),
                patch.object(server, "announce_media_lifecycle"),
            ):
                result, status = server.generate_music({"style": "Warm acoustic pop", "enhanced_caption": "Global Metadata\nWarm acoustic pop.\nVocal Details\nWarm lead.\nArrangement\n[Verse] sparse guitar.", "caption_preflight": {"model": "test", "completed": True}, "lyrics": "[Verse]\nDancing through the midnight rain", "normalized_lyrics": "[Verse]\nDancing through the midnight rain"})
            self.assertEqual(status, 200)
            self.assertEqual(result["music"]["title"], "Dancing through the midnight rain")
            self.assertEqual(result["asset"]["provenance"]["title_source"], "lyrics")

    def test_generation_rejects_low_free_vram_before_invoking_engine(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = ProductionProjectStore(Path(temporary) / "projects")
            project = store.create("VRAM Guard")
            fake_engine = Mock()
            fake_engine.status.return_value = {"available": True, "runtime": "test"}
            with (
                patch.object(server, "SEQUENCE_PROJECTS", store),
                patch.object(server, "LOCAL_MUSIC_ENGINE", fake_engine),
                patch.object(server, "wan2gp_status", return_value={"state": "offline"}),
                patch.object(server, "gpu_status", return_value={"available": True, "free_gb": 5.9}),
                patch.object(server, "announce_media_lifecycle"),
            ):
                result, status = server.generate_music({"project_id": project["project_id"], "style": "Test", "enhanced_caption": "Global Metadata\nTest.\nVocal Details\nLead.\nArrangement\n[Verse] sparse.", "caption_preflight": {"model": "test", "completed": True}, "lyrics": "[Verse]\nTest", "normalized_lyrics": "[Verse]\nTest"})
            self.assertEqual(status, 409)
            self.assertIn("at least", result["message"])
            fake_engine.generate.assert_not_called()

    def test_standalone_candidate_accepts_into_the_configured_music_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Music"
            candidate_root = root / "Candidates"
            candidate_root.mkdir(parents=True)
            candidate = candidate_root / "standalone.wav"
            companion = candidate_root / "standalone.mp3"
            candidate.write_bytes(b"RIFF" + b"\x00" * 40)
            companion.write_bytes(b"ID3" + b"\x00" * 20)
            with patch.object(server, "configuration_snapshot", return_value={"storage": {"music": str(root)}}):
                asset = server.record_standalone_music_candidate(candidate, {"lyrics": "Exact standalone lyric"}, companion)
                accepted = server.accept_standalone_music(asset["asset_id"])
                resolved, media, metadata, mp3 = server.standalone_music_asset(asset["asset_id"], include_accepted=True)
            self.assertEqual(accepted["status"], "accepted")
            self.assertEqual(resolved["status"], "accepted")
            self.assertEqual(media, root / "standalone.wav")
            self.assertEqual(metadata, root / "standalone.json")
            self.assertEqual(mp3, root / "standalone.mp3")
            self.assertTrue((root / "standalone.wav").is_file())
            self.assertTrue((root / "standalone.mp3").is_file())
            self.assertFalse(candidate.exists())
            self.assertFalse(companion.exists())

    def test_candidate_audio_endpoint_supports_byte_ranges_for_browser_preview(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Music"
            candidate_root = root / "Candidates"
            candidate_root.mkdir(parents=True)
            candidate = candidate_root / "preview.wav"
            companion = candidate_root / "preview.mp3"
            candidate.write_bytes(b"RIFF" + bytes(range(64)))
            companion.write_bytes(b"ID3" + bytes(range(20)))
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.AriadneHandler)
            try:
                threading.Thread(target=httpd.serve_forever, daemon=True).start()
                with patch.object(server, "configuration_snapshot", return_value={"storage": {"music": str(root)}}):
                    asset = server.record_standalone_music_candidate(candidate, {"lyrics": "Preview lyric"}, companion)
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{httpd.server_port}/api/music/candidates/{asset['asset_id']}/content?format=mp3&t=123",
                        headers={"Range": "bytes=4-11"},
                    )
                    with urllib.request.urlopen(request, timeout=5) as response:
                        self.assertEqual(response.status, 206)
                        self.assertEqual(response.headers["Content-Type"], "audio/mpeg")
                        self.assertEqual(response.headers["Accept-Ranges"], "bytes")
                        self.assertEqual(response.headers["Content-Range"], "bytes 4-11/23")
                        self.assertEqual(response.read(), bytes(range(20))[1:9])
                    server.accept_standalone_music(asset["asset_id"])
                    with urllib.request.urlopen(request, timeout=5) as response:
                        self.assertEqual(response.status, 206)
                        self.assertEqual(response.read(), bytes(range(20))[1:9])
                    download_request = urllib.request.Request(
                        f"http://127.0.0.1:{httpd.server_port}/api/music/candidates/{asset['asset_id']}/content?format=mp3&download=1",
                        headers={"Range": "bytes=0-3"},
                    )
                    with urllib.request.urlopen(download_request, timeout=5) as response:
                        self.assertEqual(response.headers["Content-Disposition"], 'attachment; filename="preview.mp3"')
            finally:
                httpd.shutdown()
                httpd.server_close()


if __name__ == "__main__":
    unittest.main()

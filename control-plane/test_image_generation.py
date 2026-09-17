from __future__ import annotations

import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import server
from production_projects import ProductionProjectStore


class ImageGenerationTests(unittest.TestCase):
    def test_image_size_catalog_contains_the_six_initial_targets(self):
        sizes = server.image_sizes_payload()
        self.assertEqual(
            [item["id"] for item in sizes],
            ["768x768", "1024x1024", "720x1280", "1080x1920", "1280x720", "1920x1080"],
        )
        self.assertEqual(sizes[2]["orientation"], "portrait")
        self.assertEqual(sizes[-1]["orientation"], "landscape")

    def test_prompt_workflow_preserves_portrait_dimensions(self):
        workflow = server.image_prompt_workflow("sd_xl_base_1.0.safetensors", "a test image", "", 720, 1280, 42)
        self.assertEqual(workflow["5"]["inputs"]["width"], 720)
        self.assertEqual(workflow["5"]["inputs"]["height"], 1280)

    def test_generation_rejects_a_dimension_pair_outside_the_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sd_xl_base_1.0.safetensors").write_bytes(b"model")
            with patch.object(server, "IMAGE_MODEL_ROOT", root):
                result, status = server.generate_image({"prompt": "a test image", "model": "sdxl-base-1.0", "width": 832, "height": 832})
        self.assertEqual(status, 400)
        self.assertIn("six supported image sizes", result["message"])

    def test_media_lifecycle_is_forwarded_to_the_existing_rust_host_channel(self):
        with patch.object(server, "_send_avatar_event_async") as sender:
            server.announce_media_lifecycle("Image", "STARTING_BACKEND", "ComfyUI image engine is starting.")
        self.assertEqual(sender.call_count, 2)

    def test_model_catalog_is_provider_independent_and_reports_installation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sd_xl_base_1.0.safetensors").write_bytes(b"model")
            with patch.object(server, "IMAGE_MODEL_ROOT", root):
                rows = server.image_models_payload()
        self.assertEqual(rows[0]["id"], "sdxl-base-1.0")
        self.assertTrue(rows[0]["installed"])
        self.assertFalse(rows[1]["installed"])
        self.assertEqual(rows[0]["family"], "SDXL")

    def test_prompt_workflow_keeps_model_separate_from_engine(self):
        workflow = server.image_prompt_workflow("sd_xl_base_1.0.safetensors", "a test image", "blurry", 768, 768, 42)
        self.assertEqual(workflow["4"]["class_type"], "CheckpointLoaderSimple")
        self.assertEqual(workflow["4"]["inputs"]["ckpt_name"], "sd_xl_base_1.0.safetensors")
        self.assertEqual(workflow["6"]["inputs"]["text"], "a test image")
        self.assertEqual(workflow["7"]["inputs"]["text"], "blurry")
        self.assertEqual(workflow["5"]["inputs"]["batch_size"], 1)

    def test_generation_requires_a_started_engine_and_installed_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sd_xl_base_1.0.safetensors").write_bytes(b"model")
            offline = {"state": "offline", "models": server.image_models_payload()}
            with patch.object(server, "IMAGE_MODEL_ROOT", root), patch.object(server, "image_engine_status", return_value=offline):
                result, status = server.generate_image({"prompt": "a test image", "model": "sdxl-base-1.0"})
        self.assertEqual(status, 409)
        self.assertIn("Start the image process", result["message"])

    def test_unassigned_image_sidecar_preserves_exact_prompt_and_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "candidate.png"
            image.write_bytes(b"png")
            record = server.write_unassigned_image_sidecar(image, {
                "prompt": "A neon Bangkok market",
                "negative_prompt": "blurry",
                "settings": {"seed": 99, "steps": 24},
            })
            sidecar = image.with_suffix(".json")
            self.assertTrue(sidecar.is_file())
            self.assertEqual(record["status"], "unassigned")
            self.assertEqual(record["provenance"]["prompt"], "A neon Bangkok market")

    def test_project_generation_writes_candidate_png_and_matching_json_without_promoting_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            models = root / "models"
            models.mkdir()
            (models / "sd_xl_base_1.0.safetensors").write_bytes(b"model")
            store = ProductionProjectStore(root / "projects")
            project = store.create("Project Image")
            history = {"job-42": {"outputs": {"9": {"images": [{"filename": "engine.png", "subfolder": "", "type": "output"}]}}}}
            online = {"state": "online", "lifecycle_state": "READY"}
            with (
                patch.object(server, "IMAGE_MODEL_ROOT", models),
                patch.object(server, "SEQUENCE_PROJECTS", store),
                patch.object(server, "image_engine_status", return_value=online),
                patch.object(server, "wan2gp_status", return_value={"state": "offline"}),
                patch.object(server, "post_json", return_value={"prompt_id": "job-42"}),
                patch.object(server, "json_http", return_value=history),
                patch.object(server, "_comfy_output_bytes", return_value=b"png"),
                patch.object(server, "configuration_snapshot", return_value={"storage": {"images": str(root / "images")}}),
                patch.object(server, "ai_gpu_admission", return_value=nullcontext()),
            ):
                result, status = server.generate_image({"project_id": project["project_id"], "prompt": "A rainy Bangkok alley", "model": "sdxl-base-1.0", "width": 768, "height": 768, "seed": 42})
            self.assertEqual(status, 200)
            self.assertEqual(result["asset"]["status"], "candidate")
            self.assertEqual(result["asset"]["provenance"]["prompt"], "A rainy Bangkok alley")
            self.assertTrue((store.image_candidate_directory(project["project_id"]) / "ariadne-job-42.png").is_file())
            self.assertTrue((store.image_candidate_directory(project["project_id"]) / "ariadne-job-42.json").is_file())

    def test_generation_rejects_uninstalled_model_before_engine_call(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(server, "IMAGE_MODEL_ROOT", Path(temporary)):
                result, status = server.generate_image({"prompt": "a test image", "model": "pony-v6-xl"})
        self.assertEqual(status, 409)
        self.assertIn("Pony Diffusion", result["message"])


if __name__ == "__main__":
    unittest.main()

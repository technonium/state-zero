import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "scripts"))
sys.path.insert(0, str(ROOT / "ops"))


class FlowProviderTests(unittest.TestCase):
    def test_flow_image_command_preserves_prompt_and_hard_settings(self):
        from flow_media_client import FlowMediaClient

        client = FlowMediaClient(profile="shadow")
        command = client.image_command({"scene": "cloud"}, Path("/shadow/flow_art.png"))
        self.assertEqual(command[:3], ["gflow", "image", "t2i"])
        self.assertEqual(command[3], '{"scene":"cloud"}')
        for pair in (("--model", "nano2"), ("--aspect", "3:4"), ("--count", "1"), ("--profile", "shadow")):
            self.assertEqual(command[command.index(pair[0]) + 1], pair[1])
        self.assertIn("--json", command)

    def test_flow_video_command_uses_local_start_frame_and_one_fast_clip(self):
        from flow_media_client import FlowMediaClient

        client = FlowMediaClient(profile="shadow")
        command = client.video_command("move slowly", Path("/shadow/start.png"), Path("/shadow/raw.mp4"))
        self.assertEqual(command[:3], ["gflow", "video", "i2v"])
        self.assertEqual(command[command.index("--initial-frame") + 1], "/shadow/start.png")
        self.assertEqual(command[command.index("--model") + 1], "veo-fast")
        self.assertEqual(command[command.index("--aspect") + 1], "9:16")
        self.assertEqual(command[command.index("--count") + 1], "1")
        self.assertEqual(command[command.index("--duration") + 1], "8")
        self.assertNotIn("t2v", command)

    def test_flow_validation_does_not_require_google_api_key(self):
        import validate

        env = {
            "MEDIA_GENERATION_PROVIDER": "flow",
            "FLOW_API_FALLBACK_ENABLED": "false",
            "PIPELINE_MODE": "automatic",
            "PIPELINE_POST_TO_INSTAGRAM": "false",
            "PIPELINE_MEDIA_MODE": "local_test",
            "OPENROUTER_API_KEY": "test",
            "WHOOP_CLIENT_ID": "test",
            "WHOOP_CLIENT_SECRET": "test",
        }
        with patch.dict(os.environ, env, clear=True):
            validate.validate_environment(rescue_only=False)

    def test_flow_image_error_never_creates_mock_art(self):
        from image_gen import ImageGenerator

        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "art.png"
            with patch.dict(os.environ, {"MEDIA_GENERATION_PROVIDER": "flow", "FLOW_API_FALLBACK_ENABLED": "false"}, clear=True):
                with patch("flow_media_client.FlowMediaClient.generate_image", side_effect=RuntimeError("Flow unavailable")):
                    with self.assertRaisesRegex(RuntimeError, "Flow unavailable"):
                        ImageGenerator().generate({"scene": "cloud"}, str(target))
            self.assertFalse(target.exists())

    def test_flow_keeps_jpeg_original_and_converts_canonical_art(self):
        from flow_media_client import FlowMediaClient

        with tempfile.TemporaryDirectory() as tmpdir:
            original = Path(tmpdir) / "flow_art.jpg"
            Image.new("RGB", (768, 1024), "white").save(original, "JPEG")
            payload = {"status": "ok", "count": 1, "model": "nano2", "project_id": "project", "images": [{"local_path": str(original), "media_name": "media", "model_name_type": "NARWHAL"}]}
            with patch.dict(os.environ, {"FLOW_IMAGE_UPSCALE_2K": "false"}):
                with patch.object(FlowMediaClient, "_run_json", return_value=payload):
                    target = Path(tmpdir) / "generated_art.png"
                    FlowMediaClient().generate_image({"scene": "cloud"}, target)
            self.assertTrue(original.exists())
            with Image.open(target) as image:
                self.assertEqual((image.format, image.size), ("PNG", (768, 1024)))
            diagnostics = (Path(tmpdir) / "flow_image_diagnostics.json").read_text()
            self.assertIn('"original_file": "flow_art.jpg"', diagnostics)

    def test_flow_api_fallback_is_stage_specific_when_enabled(self):
        from image_gen import ImageGenerator
        from pipeline import WHOOPPipeline

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            art = root / "generated_art.png"
            prompt = root / "video_prompt.txt"
            prompt.write_text("Move slowly")
            env = {"MEDIA_GENERATION_PROVIDER": "flow", "FLOW_API_FALLBACK_ENABLED": "true", "GOOGLE_API_KEY_PRIMARY": "test"}
            with patch.dict(os.environ, env, clear=True):
                with patch("flow_media_client.FlowMediaClient.generate_image", side_effect=RuntimeError("Flow image unavailable")):
                    with patch("google_image_client.GoogleImageClient.generate_from_json", side_effect=lambda _prompt, path: path.write_bytes(b"image")):
                        ImageGenerator().generate({"scene": "cloud"}, str(art))
                self.assertEqual(art.read_bytes(), b"image")
                pipeline = object.__new__(WHOOPPipeline)
                pipeline.output_dir = root
                pipeline._current_generation_status = lambda: "STARTING"
                pipeline._set_heartbeat_context = lambda **_kwargs: None
                with patch("flow_media_client.FlowMediaClient.generate_video", side_effect=RuntimeError("Flow video unavailable")):
                    with patch("google_video_client.GoogleVideoClient.generate_from_image", side_effect=lambda _prompt, _image, path: path.write_bytes(b"video")):
                        self.assertEqual(pipeline.step_9_generate_video(art, prompt).read_bytes(), b"video")


class ShadowGuardTests(unittest.TestCase):
    def test_shadow_rejects_publish_credentials_and_missing_isolation(self):
        from flow_shadow_run import validate_shadow_environment

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env = {
                "STATE_ZERO_PRIVATE_ROOT": str(root),
                "GFLOW_CLI_HOME": str(root.parent / "flow-profile"),
                "PIPELINE_MODE": "automatic",
                "PIPELINE_POST_TO_INSTAGRAM": "false",
                "MEDIA_GENERATION_PROVIDER": "flow",
                "FLOW_API_FALLBACK_ENABLED": "false",
                "GOOGLE_API_FALLBACK_ENABLED": "false",
                "PORTFOLIO_MEDIA_ENABLED": "true",
                "PIPELINE_MEDIA_MODE": "local_test",
            }
            with patch.dict(os.environ, env, clear=True):
                validate_shadow_environment()
            env["INSTAGRAM_ACCESS_TOKEN"] = "accidental"
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaises(ValueError):
                    validate_shadow_environment()
            env.pop("INSTAGRAM_ACCESS_TOKEN")
            env["STATE_ZERO_PRIVATE_ROOT"] = "/opt/state-zero-private"
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaises(ValueError):
                    validate_shadow_environment()
            env["STATE_ZERO_PRIVATE_ROOT"] = str(root)
            env["GFLOW_CLI_HOME"] = str(root / "gflow")
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaises(ValueError):
                    validate_shadow_environment()

    def test_failed_date_cannot_run_again_automatically(self):
        import flow_shadow_run

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "shadow"
            env = {
                "STATE_ZERO_PRIVATE_ROOT": str(root),
                "GFLOW_CLI_HOME": str(Path(tmpdir) / "profile"),
                "PIPELINE_MODE": "automatic",
                "PIPELINE_POST_TO_INSTAGRAM": "false",
                "MEDIA_GENERATION_PROVIDER": "flow",
                "FLOW_API_FALLBACK_ENABLED": "false",
                "GOOGLE_API_FALLBACK_ENABLED": "false",
                "PORTFOLIO_MEDIA_ENABLED": "true",
                "PIPELINE_MEDIA_MODE": "local_test",
                "PIPELINE_DATE": "2026-09-27",
            }
            with patch.dict(os.environ, env, clear=True), patch.object(flow_shadow_run, "_flow_credits", return_value=None):
                with patch.object(flow_shadow_run, "_run_logged", side_effect=RuntimeError("private failure")) as called:
                    self.assertEqual(flow_shadow_run.main(), 1)
                    self.assertEqual(flow_shadow_run.main(), 2)
                    self.assertEqual(called.call_count, 1)
            self.assertTrue((root / "runtime/state/flow_shadow/2026-09-27.json").exists())


if __name__ == "__main__":
    unittest.main()

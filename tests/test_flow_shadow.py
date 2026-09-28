import json
import os
import subprocess
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
        self.assertEqual(command[command.index("--out-dir") + 1], "/shadow")
        self.assertNotIn("--duration", command)
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

    def test_cli_timeout_does_not_expose_prompt(self):
        from flow_media_client import FlowMediaClient

        command = ["gflow", "image", "t2i", "private WHOOP prompt"]
        with patch("flow_media_client.subprocess.run", side_effect=subprocess.TimeoutExpired(command, 1)):
            with self.assertRaises(RuntimeError) as failure:
                FlowMediaClient._run_json(command, 1)
        self.assertNotIn("private WHOOP prompt", str(failure.exception))

    def test_auth_failure_after_submit_marker_still_requests_sign_in(self):
        from flow_media_client import FlowCommandError, FlowMediaClient

        with tempfile.TemporaryDirectory() as tmpdir:
            marker = Path(tmpdir) / "submit"
            marker.touch()
            payload = {"status": "fail", "error": {"class": "AuthExpiredError"}}
            result = subprocess.CompletedProcess([], 1, stdout=json.dumps(payload), stderr="")
            with patch("flow_media_client.subprocess.run", return_value=result):
                with self.assertRaises(FlowCommandError) as failure:
                    FlowMediaClient._run_json(["gflow", "video", "i2v"], 1, marker)
            self.assertEqual(failure.exception.category, "auth_required")

    def test_flow_keeps_jpeg_original_and_converts_canonical_art(self):
        from flow_media_client import FlowMediaClient

        with tempfile.TemporaryDirectory() as tmpdir:
            original = Path(tmpdir) / "flow_art.jpg"
            Image.new("RGB", (768, 1024), "white").save(original, "JPEG")
            payload = {"status": "ok", "count": 1, "model": "NARWHAL", "project_id": "project", "images": [{"local_path": str(original), "media_name": "media", "model_name_type": "NARWHAL"}]}
            with patch.dict(os.environ, {"FLOW_IMAGE_UPSCALE_2K": "false"}):
                with patch.object(FlowMediaClient, "_run_json", return_value=payload):
                    target = Path(tmpdir) / "generated_art.png"
                    FlowMediaClient().generate_image({"scene": "cloud"}, target)
            self.assertTrue(original.exists())
            with Image.open(target) as image:
                self.assertEqual((image.format, image.size), ("PNG", (768, 1024)))
            diagnostics = (Path(tmpdir) / "flow_image_diagnostics.json").read_text()
            self.assertIn('"original_file": "flow_art.jpg"', diagnostics)

    def test_migrated_flow_can_omit_image_model_attribution(self):
        from flow_media_client import FlowMediaClient

        with tempfile.TemporaryDirectory() as tmpdir:
            original = Path(tmpdir) / "flow_art.jpg"
            Image.new("RGB", (768, 1024), "white").save(original, "JPEG")
            payload = {"status": "ok", "count": 1, "model": "NARWHAL", "images": [{"local_path": str(original), "model_name_type": None}]}
            with patch.dict(os.environ, {"FLOW_IMAGE_UPSCALE_2K": "false"}):
                with patch.object(FlowMediaClient, "_run_json", return_value=payload):
                    FlowMediaClient().generate_image({"scene": "cloud"}, Path(tmpdir) / "generated_art.png")
            diagnostics = json.loads((Path(tmpdir) / "flow_image_diagnostics.json").read_text())
            self.assertIsNone(diagnostics["wire_model"])
            self.assertFalse(diagnostics["model_attribution_confirmed"])

    def test_explicit_failed_video_submits_at_most_twice(self):
        from flow_media_client import FlowCommandError, FlowMediaClient

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            art = root / "art.png"
            Image.new("RGB", (768, 1024), "white").save(art)
            calls = []

            def fail(_command, _timeout, marker):
                marker.touch()
                calls.append(marker)
                raise FlowCommandError("video", "generation_failed", media_id=f"media-{len(calls)}", retryable=True)

            with patch.object(FlowMediaClient, "_run_json", side_effect=fail), patch.object(
                FlowMediaClient, "_catalog_position", return_value=0
            ), patch.object(FlowMediaClient, "_catalog_video_since", return_value={}), patch(
                "flow_media_client.time.sleep"
            ):
                with self.assertRaises(FlowCommandError):
                    FlowMediaClient().generate_video("move", art, root / "generated_video.mp4")
            self.assertEqual(len(calls), 2)
            report = json.loads((root / "flow_video_diagnostics.json").read_text())
            self.assertEqual(report["submissions_estimated"], 2)
            self.assertEqual([attempt["media_id"] for attempt in report["attempts"]], ["media-1", "media-2"])

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
    def test_shadow_image_runs_chrome_as_non_root(self):
        dockerfile = (ROOT / "Dockerfile.flow-shadow").read_text(encoding="utf-8")
        self.assertIn("USER shadow", dockerfile)
        self.assertIn("useradd", dockerfile)

    def test_failed_preflight_claims_date_without_running_pipeline(self):
        import flow_shadow_run

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "shadow"
            profile = Path(tmpdir) / "profile"
            root.mkdir()
            profile.mkdir()
            (root / ".state-zero-flow-shadow-private").touch()
            (profile / ".state-zero-flow-shadow-profile").touch()
            env = {
                "STATE_ZERO_PRIVATE_ROOT": str(root), "GFLOW_CLI_HOME": str(profile),
                "FLOW_PREFLIGHT_PROJECT_ID": "id-existing", "PIPELINE_DATE": "2026-09-27",
                "PIPELINE_MODE": "automatic", "PIPELINE_POST_TO_INSTAGRAM": "false",
                "MEDIA_GENERATION_PROVIDER": "flow", "FLOW_API_FALLBACK_ENABLED": "false",
                "GOOGLE_API_FALLBACK_ENABLED": "false", "PORTFOLIO_MEDIA_ENABLED": "true",
                "PIPELINE_MEDIA_MODE": "local_test",
                "OPENROUTER_CALL_DEADLINE_SECONDS": "200", "PROMPT_GOOGLE_API_KEY": "test",
                "SHADOW_TRIAL_START_DATE": "2026-09-27",
                "SHADOW_ALERT_BOT_TOKEN": "test", "SHADOW_ALERT_CHAT_ID": "test",
            }
            with patch.dict(os.environ, env, clear=True), patch.object(flow_shadow_run, "_flow_credits", return_value=None), patch.object(flow_shadow_run, "_alert"):
                with patch.object(flow_shadow_run, "_preflight", return_value=False):
                    with patch.object(flow_shadow_run, "_run_logged") as pipeline:
                        self.assertEqual(flow_shadow_run.main(), 1)
                        pipeline.assert_not_called()
            self.assertTrue((root / "runtime/state/flow_shadow/2026-09-27.json").exists())

    def test_preflight_classifies_editor_without_generating(self):
        from flow_shadow_preflight import classify_editor

        self.assertEqual(classify_editor("https://flow.google.com/about", False, False, False, None, False), "reauth_required")
        self.assertEqual(classify_editor("https://flow.google.com/project/id-good", True, True, True, False, False), "ready")
        self.assertEqual(classify_editor("https://flow.google.com/project/id-good", True, False, True, True, True), "agent_toggle_on")
        self.assertEqual(classify_editor("https://flow.google.com/project/id-good", True, False, False, None, True), "agent_panel_only")
        self.assertEqual(classify_editor("https://flow.google.com/project/id-good", True, False, False, None, False), "unsupported_agent_view")
        self.assertEqual(classify_editor("https://flow.google.com/project/id-good", False, False, False, None, False), "unknown_editor")

    def test_preflight_reports_missing_project_without_opening_browser(self):
        import flow_shadow_preflight

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"STATE_ZERO_PRIVATE_ROOT": tmpdir}, clear=True):
                with patch.object(flow_shadow_preflight, "inspect_editor") as browser:
                    self.assertEqual(flow_shadow_preflight.main(), 3)
                    browser.assert_not_called()
            report = json.loads((Path(tmpdir) / "runtime/state/flow_shadow/preflight.json").read_text())
            self.assertEqual(report["status"], "project_id_missing")

    def test_shadow_rejects_publish_credentials_and_missing_isolation(self):
        from flow_shadow_run import validate_shadow_environment

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "shadow"
            profile = Path(tmpdir) / "profile"
            root.mkdir()
            profile.mkdir()
            (root / ".state-zero-flow-shadow-private").touch()
            (profile / ".state-zero-flow-shadow-profile").touch()
            env = {
                "STATE_ZERO_PRIVATE_ROOT": str(root),
                "GFLOW_CLI_HOME": str(profile),
                "PIPELINE_MODE": "automatic",
                "PIPELINE_POST_TO_INSTAGRAM": "false",
                "MEDIA_GENERATION_PROVIDER": "flow",
                "FLOW_API_FALLBACK_ENABLED": "false",
                "GOOGLE_API_FALLBACK_ENABLED": "false",
                "PORTFOLIO_MEDIA_ENABLED": "true",
                "PIPELINE_MEDIA_MODE": "local_test",
                "OPENROUTER_CALL_DEADLINE_SECONDS": "200", "PROMPT_GOOGLE_API_KEY": "test",
                "SHADOW_ALERT_BOT_TOKEN": "test", "SHADOW_ALERT_CHAT_ID": "test",
            }
            with patch.dict(os.environ, env, clear=True):
                validate_shadow_environment()
            env.pop("PROMPT_GOOGLE_API_KEY")
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(ValueError, "prompt-only Gemini"):
                    validate_shadow_environment()
            env["PROMPT_GOOGLE_API_KEY"] = "test"
            env["INSTAGRAM_ACCESS_TOKEN"] = "accidental"
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaises(ValueError):
                    validate_shadow_environment()
            env.pop("INSTAGRAM_ACCESS_TOKEN")
            env["STATE_ZERO_PRIVATE_ROOT"] = "/opt/state-zero-private"
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaises(ValueError):
                    validate_shadow_environment()
            env["STATE_ZERO_PRIVATE_ROOT"] = "/opt/state-zero-private/shadow"
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaises(ValueError):
                    validate_shadow_environment()
            env["STATE_ZERO_PRIVATE_ROOT"] = str(root)
            env["GFLOW_CLI_HOME"] = str(root / "gflow")
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaises(ValueError):
                    validate_shadow_environment()
            env["GFLOW_CLI_HOME"] = "/opt/state-zero-private/flow-profile"
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaises(ValueError):
                    validate_shadow_environment()

    def test_failed_date_cannot_run_again_automatically(self):
        import flow_shadow_run

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "shadow"
            profile = Path(tmpdir) / "profile"
            root.mkdir()
            profile.mkdir()
            (root / ".state-zero-flow-shadow-private").touch()
            (profile / ".state-zero-flow-shadow-profile").touch()
            env = {
                "STATE_ZERO_PRIVATE_ROOT": str(root),
                "GFLOW_CLI_HOME": str(profile),
                "PIPELINE_MODE": "automatic",
                "PIPELINE_POST_TO_INSTAGRAM": "false",
                "MEDIA_GENERATION_PROVIDER": "flow",
                "FLOW_API_FALLBACK_ENABLED": "false",
                "GOOGLE_API_FALLBACK_ENABLED": "false",
                "PORTFOLIO_MEDIA_ENABLED": "true",
                "PIPELINE_MEDIA_MODE": "local_test",
                "OPENROUTER_CALL_DEADLINE_SECONDS": "200", "PROMPT_GOOGLE_API_KEY": "test",
                "PIPELINE_DATE": "2026-09-27",
                "SHADOW_TRIAL_START_DATE": "2026-09-27",
                "SHADOW_ALERT_BOT_TOKEN": "test", "SHADOW_ALERT_CHAT_ID": "test",
            }
            with patch.dict(os.environ, env, clear=True), patch.object(flow_shadow_run, "_flow_credits", return_value=None), patch.object(flow_shadow_run, "_preflight", return_value=True), patch.object(flow_shadow_run, "_alert"):
                with patch.object(flow_shadow_run, "_run_logged", side_effect=RuntimeError("private failure")) as called:
                    self.assertEqual(flow_shadow_run.main(), 1)
                    self.assertEqual(flow_shadow_run.main(), 2)
                    self.assertEqual(called.call_count, 1)
            self.assertTrue((root / "runtime/state/flow_shadow/2026-09-27.json").exists())

    def test_eighth_date_cannot_generate(self):
        import flow_shadow_run

        with tempfile.TemporaryDirectory() as tmpdir:
            root, profile = Path(tmpdir) / "shadow", Path(tmpdir) / "profile"
            root.mkdir()
            profile.mkdir()
            (root / ".state-zero-flow-shadow-private").touch()
            (profile / ".state-zero-flow-shadow-profile").touch()
            env = {
                "STATE_ZERO_PRIVATE_ROOT": str(root), "GFLOW_CLI_HOME": str(profile),
                "PIPELINE_MODE": "automatic", "PIPELINE_POST_TO_INSTAGRAM": "false",
                "MEDIA_GENERATION_PROVIDER": "flow", "FLOW_API_FALLBACK_ENABLED": "false",
                "GOOGLE_API_FALLBACK_ENABLED": "false", "PORTFOLIO_MEDIA_ENABLED": "true",
                "PIPELINE_MEDIA_MODE": "local_test", "SHADOW_TRIAL_START_DATE": "2026-09-27",
                "OPENROUTER_CALL_DEADLINE_SECONDS": "200", "PROMPT_GOOGLE_API_KEY": "test",
                "PIPELINE_DATE": "2026-10-04", "SHADOW_ALERT_BOT_TOKEN": "test",
                "SHADOW_ALERT_CHAT_ID": "test",
            }
            with patch.dict(os.environ, env, clear=True), patch.object(flow_shadow_run, "_preflight") as gate:
                self.assertEqual(flow_shadow_run.main(), 0)
                gate.assert_not_called()


class PromptFallbackTests(unittest.TestCase):
    def test_first_timeout_retries_openrouter_before_gemini(self):
        from openrouter_client import OpenRouterClient, OpenRouterTimeoutError

        client = OpenRouterClient(api_key="unused", fallback_api_key="unused")
        with patch.dict(os.environ, {"OPENROUTER_CALL_DEADLINE_SECONDS": "200"}):
            with patch.object(client, "_call_openrouter", side_effect=[OpenRouterTimeoutError("timeout"), "retry-ok"]) as openrouter:
                with patch.object(client, "_call_google_gemini") as fallback:
                    self.assertEqual(client.generate("check"), "retry-ok")
                    self.assertEqual(openrouter.call_count, 2)
                    fallback.assert_not_called()

    def test_two_stalled_calls_reach_prompt_only_gemini_fallback(self):
        import time
        from openrouter_client import OpenRouterClient

        client = OpenRouterClient(api_key="unused", fallback_api_key="unused")
        with patch.dict(os.environ, {"OPENROUTER_CALL_DEADLINE_SECONDS": "1"}):
            with patch.object(client, "_call_openrouter", side_effect=lambda *_: time.sleep(3)) as openrouter:
                with patch.object(client, "_call_google_gemini", return_value="fallback-ok") as fallback:
                    self.assertEqual(client.generate("check"), "fallback-ok")
                    self.assertEqual(openrouter.call_count, 2)
                    fallback.assert_called_once()


if __name__ == "__main__":
    unittest.main()

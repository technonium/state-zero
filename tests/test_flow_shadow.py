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
    def test_whoop_readiness_classifies_pending_transient_auth_and_terminal(self):
        import flow_shadow_run

        self.assertEqual(flow_shadow_run.classify_whoop_readiness(2, "score_state is PENDING"), "waiting_for_whoop")
        self.assertEqual(flow_shadow_run.classify_whoop_readiness(3, "WHOOP server error"), "retryable_whoop_error")
        self.assertEqual(flow_shadow_run.classify_whoop_readiness(3, "Auth error (401)"), "reauth_required")
        self.assertEqual(flow_shadow_run.classify_whoop_readiness(4, "astrology file missing"), "terminal_configuration_error")

    def test_schedule_windows_admit_window_and_final_checks_only(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        import flow_shadow_run

        tz = ZoneInfo("Asia/Kolkata")
        self.assertTrue(flow_shadow_run.schedule_allows_run(False, datetime(2026, 10, 3, 10, 0, tzinfo=tz)))
        self.assertTrue(flow_shadow_run.schedule_allows_run(False, datetime(2026, 10, 3, 15, 0, tzinfo=tz)))
        self.assertFalse(flow_shadow_run.schedule_allows_run(False, datetime(2026, 10, 3, 15, 30, tzinfo=tz)))
        self.assertTrue(flow_shadow_run.schedule_allows_run(True, datetime(2026, 10, 3, 15, 15, tzinfo=tz)))
        self.assertFalse(flow_shadow_run.schedule_allows_run(True, datetime(2026, 10, 3, 15, 0, tzinfo=tz)))
        utc = ZoneInfo("UTC")
        self.assertFalse(flow_shadow_run.schedule_allows_run(False, datetime(2026, 10, 3, 4, 0, tzinfo=utc)))
        self.assertTrue(flow_shadow_run.schedule_allows_run(False, datetime(2026, 10, 3, 4, 30, tzinfo=utc)))
        self.assertTrue(flow_shadow_run.schedule_allows_run(True, datetime(2026, 10, 3, 9, 45, tzinfo=utc)))

    def test_runner_lock_blocks_overlapping_cron_invocations(self):
        import flow_shadow_run

        with tempfile.TemporaryDirectory() as tmpdir:
            state = Path(tmpdir)
            owner = flow_shadow_run._acquire_runner_lock(state)
            self.assertIsNotNone(owner)
            self.assertIsNone(flow_shadow_run._acquire_runner_lock(state))
            owner.close()
            next_owner = flow_shadow_run._acquire_runner_lock(state)
            self.assertIsNotNone(next_owner)
            next_owner.close()

    def test_failed_telegram_alert_is_queued_and_retried(self):
        import flow_shadow_run

        with tempfile.TemporaryDirectory() as tmpdir:
            state = Path(tmpdir)
            with patch.object(flow_shadow_run, "_alert", side_effect=[False, True]) as alert:
                self.assertFalse(flow_shadow_run._alert_once(state, "2026-10-04", "final_missed", "WHOOP not ready"))
                queued = json.loads((state / "alerts/2026-10-04.json").read_text())
                self.assertTrue(queued["final_missed"]["pending"])
                flow_shadow_run._retry_pending_alerts(state)
                delivered = json.loads((state / "alerts/2026-10-04.json").read_text())
                self.assertIn("sent_at", delivered["final_missed"])
            self.assertEqual(alert.call_count, 2)

    def test_memory_sampler_persists_an_oom_survivable_evidence_file(self):
        import flow_shadow_run

        with tempfile.TemporaryDirectory() as tmpdir:
            evidence = Path(tmpdir) / "memory_samples.jsonl"
            sampler = flow_shadow_run.MemorySampler(evidence)
            sampler.start()
            summary = sampler.stop()
            samples = [json.loads(line) for line in evidence.read_text().splitlines()]
            self.assertGreaterEqual(len(samples), 2)
            self.assertIn("oom_events", samples[-1])
            self.assertIn("oom_events_delta", summary)

    def test_whoop_readiness_history_records_pending_retry_and_ready_checks(self):
        import flow_shadow_run

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir)
            outcomes = [
                subprocess.CompletedProcess([], 2, stdout="sleep unscored", stderr=""),
                subprocess.CompletedProcess([], 3, stdout="WHOOP server error", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="WHOOP ready", stderr=""),
            ]

            def lookup(*_args, **_kwargs):
                result = outcomes.pop(0)
                if result.returncode == 0:
                    (output / "daily_data.json").write_text(json.dumps({"date": "2026-10-04"}))
                return result

            with patch.object(flow_shadow_run.subprocess, "run", side_effect=lookup):
                statuses = [flow_shadow_run._probe_whoop("2026-10-04", output)[0] for _ in range(3)]

            self.assertEqual(statuses, ["waiting_for_whoop", "retryable_whoop_error", "ready"])
            history = [json.loads(line) for line in (output / "whoop_readiness.jsonl").read_text().splitlines()]
            self.assertEqual([entry["status"] for entry in history], statuses)

    def test_summary_is_sent_once_after_seven_dates_and_day_eight_is_unbounded(self):
        from datetime import date
        import flow_shadow_run

        with tempfile.TemporaryDirectory() as tmpdir:
            state = Path(tmpdir)
            start = date(2026, 9, 27)
            with patch.object(flow_shadow_run, "_alert", return_value=True) as alert:
                self.assertTrue(flow_shadow_run.maybe_send_trial_summary(state, start, date(2026, 10, 4)))
                self.assertFalse(flow_shadow_run.maybe_send_trial_summary(state, start, date(2026, 10, 5)))
            alert.assert_called_once()
            self.assertEqual(flow_shadow_run.trial_day_number(start, date(2026, 10, 4)), 8)

    def test_preflight_allows_both_browser_checks_to_finish(self):
        import flow_shadow_run

        result = subprocess.CompletedProcess([], 0, stdout="Flow shadow preflight: ready")
        with patch.object(flow_shadow_run.subprocess, "run", return_value=result) as command:
            self.assertTrue(flow_shadow_run._preflight())
            self.assertEqual(command.call_args.kwargs["timeout"], 240)

    def test_preflight_retries_transient_browser_failure_once(self):
        import flow_shadow_run

        responses = [
            subprocess.CompletedProcess([], 3, stdout="Flow shadow preflight: browser_error"),
            subprocess.CompletedProcess([], 0, stdout="Flow shadow preflight: ready"),
        ]
        with patch.object(flow_shadow_run.subprocess, "run", side_effect=responses) as command:
            self.assertTrue(flow_shadow_run._preflight())
            self.assertEqual(command.call_count, 2)

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
                "FLOW_PREFLIGHT_PROJECT_ID": "id-existing", "PIPELINE_DATE": "2026-09-28",
                "PIPELINE_MODE": "automatic", "PIPELINE_POST_TO_INSTAGRAM": "false",
                "MEDIA_GENERATION_PROVIDER": "flow", "FLOW_API_FALLBACK_ENABLED": "false",
                "GOOGLE_API_FALLBACK_ENABLED": "false", "PORTFOLIO_MEDIA_ENABLED": "true",
                "PIPELINE_MEDIA_MODE": "local_test",
                "OPENROUTER_CALL_DEADLINE_SECONDS": "200", "PROMPT_GOOGLE_API_KEY": "test",
                "SHADOW_TRIAL_START_DATE": "2026-09-27",
                "SHADOW_ALERT_BOT_TOKEN": "test", "SHADOW_ALERT_CHAT_ID": "test",
            }
            def failed_gate():
                (root / "runtime/state/flow_shadow/preflight.json").write_text('{"status":"reauth_required"}')
                return False

            with patch.dict(os.environ, env, clear=True), patch.object(flow_shadow_run, "schedule_allows_run", return_value=True), patch.object(flow_shadow_run, "_probe_whoop", return_value=("ready", 0, "")), patch.object(flow_shadow_run, "_flow_credits", return_value=None), patch.object(flow_shadow_run, "_alert") as alert:
                with patch.object(flow_shadow_run, "_preflight", side_effect=failed_gate):
                    with patch.object(flow_shadow_run, "_run_logged") as pipeline:
                        self.assertEqual(flow_shadow_run.main([]), 1)
                        pipeline.assert_not_called()
                        self.assertIn("Flow sign-in needed", alert.call_args.args[0])
            self.assertTrue((root / "runtime/state/flow_shadow/2026-09-28.json").exists())

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
            with patch.dict(os.environ, env, clear=True), patch.object(flow_shadow_run, "schedule_allows_run", return_value=True), patch.object(flow_shadow_run, "_probe_whoop", return_value=("ready", 0, "")), patch.object(flow_shadow_run, "_flow_credits", return_value=None), patch.object(flow_shadow_run, "_preflight", return_value=True), patch.object(flow_shadow_run, "_alert"):
                with patch.object(flow_shadow_run, "_run_logged", side_effect=RuntimeError("private failure")) as called:
                    self.assertEqual(flow_shadow_run.main([]), 1)
                    self.assertEqual(flow_shadow_run.main([]), 2)
                    self.assertEqual(called.call_count, 1)
            self.assertTrue((root / "runtime/state/flow_shadow/2026-09-27.json").exists())

    def test_eighth_date_is_not_blocked_by_trial_length(self):
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
            with patch.dict(os.environ, env, clear=True), patch.object(flow_shadow_run, "schedule_allows_run", return_value=True), patch.object(flow_shadow_run, "_probe_whoop", return_value=("ready", 0, "")), patch.object(flow_shadow_run, "_alert", return_value=True):
                with patch.object(flow_shadow_run, "_preflight", return_value=False) as gate:
                    self.assertEqual(flow_shadow_run.main([]), 1)
                    gate.assert_called_once()
            marker = json.loads((root / "runtime/state/flow_shadow/2026-10-04.json").read_text())
            self.assertEqual(marker["status"], "failed")

    def test_pending_whoop_does_not_claim_date_and_final_check_records_miss_once(self):
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
            with patch.dict(os.environ, env, clear=True), patch.object(flow_shadow_run, "schedule_allows_run", return_value=True), patch.object(flow_shadow_run, "_probe_whoop", return_value=("waiting_for_whoop", 2, "pending")) as lookup, patch.object(flow_shadow_run, "_preflight") as browser, patch.object(flow_shadow_run, "_alert", return_value=True) as alert:
                self.assertEqual(flow_shadow_run.main([]), 0)
                self.assertFalse((root / "runtime/state/flow_shadow/2026-10-04.json").exists())
                self.assertEqual(flow_shadow_run.main(["--final-check"]), 0)
                marker = json.loads((root / "runtime/state/flow_shadow/2026-10-04.json").read_text())
                self.assertEqual(marker["status"], "missed")
                self.assertEqual(flow_shadow_run.main(["--final-check"]), 2)
            self.assertEqual(lookup.call_count, 2)
            browser.assert_not_called()
            self.assertEqual(alert.call_count, 2)  # final-miss alert plus the seven-date summary

    def test_interrupted_generation_is_marked_uncertain_and_never_retried(self):
        import flow_shadow_run

        with tempfile.TemporaryDirectory() as tmpdir:
            root, profile = Path(tmpdir) / "shadow", Path(tmpdir) / "profile"
            root.mkdir()
            profile.mkdir()
            (root / ".state-zero-flow-shadow-private").touch()
            (profile / ".state-zero-flow-shadow-profile").touch()
            state = root / "runtime/state/flow_shadow"
            state.mkdir(parents=True)
            (state / "2026-09-27.json").write_text(json.dumps({"date": "2026-09-27", "status": "started"}))
            env = {
                "STATE_ZERO_PRIVATE_ROOT": str(root), "GFLOW_CLI_HOME": str(profile),
                "PIPELINE_MODE": "automatic", "PIPELINE_POST_TO_INSTAGRAM": "false",
                "MEDIA_GENERATION_PROVIDER": "flow", "FLOW_API_FALLBACK_ENABLED": "false",
                "GOOGLE_API_FALLBACK_ENABLED": "false", "PORTFOLIO_MEDIA_ENABLED": "true",
                "PIPELINE_MEDIA_MODE": "local_test", "SHADOW_TRIAL_START_DATE": "2026-09-27",
                "OPENROUTER_CALL_DEADLINE_SECONDS": "200", "PROMPT_GOOGLE_API_KEY": "test",
                "PIPELINE_DATE": "2026-09-27", "SHADOW_ALERT_BOT_TOKEN": "test",
                "SHADOW_ALERT_CHAT_ID": "test",
            }
            with patch.dict(os.environ, env, clear=True), patch.object(flow_shadow_run, "schedule_allows_run", return_value=True), patch.object(flow_shadow_run, "_alert", return_value=True) as alert, patch.object(flow_shadow_run, "_probe_whoop") as lookup:
                self.assertEqual(flow_shadow_run.main([]), 2)
                self.assertEqual(flow_shadow_run.main([]), 2)
            marker = json.loads((state / "2026-09-27.json").read_text())
            self.assertEqual(marker["status"], "uncertain")
            self.assertEqual(alert.call_count, 1)
            lookup.assert_not_called()

    def test_pipeline_uses_the_exact_whoop_snapshot_that_passed_shadow_readiness(self):
        import pipeline

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            payload = {"date": "2026-10-04", "strain": 5.0}
            (root / "daily_data.json").write_text(json.dumps(payload))
            instance = object.__new__(pipeline.WHOOPPipeline)
            instance.output_dir = root
            instance.run_date = "2026-10-04"
            with patch.dict(os.environ, {"FLOW_SHADOW_WHOOP_PREFETCHED": "true"}, clear=True):
                with patch("pipeline.subprocess.run") as lookup:
                    self.assertEqual(instance.step_2_3_lookups(), payload)
                    lookup.assert_not_called()


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

class SubmissionEvidenceTests(unittest.TestCase):
    def test_blocked_request_is_retryable_but_forwarded_request_is_not(self):
        from flow_media_client import FlowMediaClient, FlowCommandError
        with tempfile.TemporaryDirectory() as tmp:
            marker=Path(tmp)/'submit'
            result=subprocess.CompletedProcess([],7,stdout=json.dumps({'status':'fail','error':{'class':'WireFormatError','detail':'migrated host: image submit model differs from requested NARWHAL'}}),stderr='')
            for state,expected in [('blocked_before_submission','blocked_before_submission'),('forwarded','post_submit_error')]:
                marker.write_text(json.dumps({'state':state}))
                with patch('flow_media_client.subprocess.run',return_value=result):
                    with self.assertRaises(FlowCommandError) as failure:
                        FlowMediaClient._run_json(['gflow','image','t2i','private prompt'],1,marker)
                self.assertEqual(failure.exception.category,expected)
                self.assertIn('requested NARWHAL',failure.exception.detail)

    def test_private_error_detail_redacts_prompt_and_signed_url(self):
        from flow_media_client import FlowMediaClient, FlowCommandError
        prompt='private WHOOP prompt';payload={'status':'fail','error':{'class':'WireFormatError','detail':prompt+' https://example.com/?Signature=secret Bearer secret'}}
        result=subprocess.CompletedProcess([],7,stdout=json.dumps(payload),stderr='')
        with patch('flow_media_client.subprocess.run',return_value=result):
            with self.assertRaises(FlowCommandError) as failure:
                FlowMediaClient._run_json(['gflow','image','t2i',prompt],1)
        self.assertNotIn(prompt,failure.exception.detail)
        self.assertNotIn('secret',failure.exception.detail)

class SavedInputRecoveryTests(unittest.TestCase):
    def test_recovery_uses_saved_inputs_and_only_remaining_media_stages(self):
        import flow_shadow_recover
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'runtime/output/2026-10-06';out.mkdir(parents=True)
            for name,value in {'daily_data.json':{'date':'2026-10-06'},'card_metadata.json':{'title':'Test'},'image_prompt.json':{'scene':'cloud'}}.items():
                (out/name).write_text(json.dumps(value))
            (out/'video_prompt.txt').write_text('move clouds')
            from unittest.mock import MagicMock
            pipeline=MagicMock();pipeline.output_dir=out;pipeline.post_to_instagram=False
            pipeline.step_7_generate_image.return_value=out/'generated_art.png'
            pipeline.step_9_generate_video.return_value=out/'generated_video.mp4'
            flow_shadow_recover.recover_media(pipeline)
            pipeline.step_7_generate_image.assert_called_once()
            pipeline.step_9_generate_video.assert_called_once()
            pipeline.step_10a_render_image.assert_called_once()
            pipeline.step_16_render_portfolio_media.assert_called_once()
            pipeline.run.assert_not_called()

class RecoveryReconciliationTests(unittest.TestCase):
    def test_missing_media_requires_reconciliation(self):
        from flow_shadow_recover import validate_recovery_inputs
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            (out/'flow_image_attempt_1.submit').write_text('attempted')
            with self.assertRaises(ValueError):
                validate_recovery_inputs(out)
            validate_recovery_inputs(out, True)
            (out/'flow_video_attempt_1.submit').write_text('{"state":"forwarded"}')
            with self.assertRaises(ValueError):
                validate_recovery_inputs(out, True)
            (out/'generated_video.mp4').touch()
            validate_recovery_inputs(out, True)

    def test_blocked_submission_is_safe_to_retry(self):
        from flow_shadow_recover import validate_recovery_inputs
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            (out/'flow_image_attempt_1.submit').write_text('{"state":"blocked_before_submission"}')
            validate_recovery_inputs(out)

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "src/scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from emergency_fallback_manager import EmergencyFallbackManager, FallbackUnavailableError
from pipeline import PipelineStageError, WHOOPPipeline


class _FakeDailyRun:
    def __init__(self, state=None):
        self._state = state or {}

    def load_state(self):
        return self._state

    def is_owner(self):
        return False

    def current_status(self):
        status = self._state.get("status")
        return str(status).strip().upper() if status else None


class _TrackingDailyRun(_FakeDailyRun):
    def __init__(self, state=None):
        super().__init__(state=state)
        self.retryable_calls = []
        self.fatal_calls = []
        self.release_calls = 0

    def is_owner(self):
        return True

    def mark_retryable_failure(self, **kwargs):
        self.retryable_calls.append(kwargs)

    def mark_fatal_failure(self, **kwargs):
        self.fatal_calls.append(kwargs)

    def release_claim(self):
        self.release_calls += 1


class _FakeNotifier:
    def notify_emergency_fallback_activated(self, **kwargs):
        return True

    def notify_warning(self, **kwargs):
        return True

    def notify_dry_run_complete(self, *args, **kwargs):
        return True


class EmergencyFallbackHardeningTests(unittest.TestCase):
    def _build_manager_with_temp_runtime(self):
        tmpdir = tempfile.TemporaryDirectory()
        runtime_root = Path(tmpdir.name) / "runtime"
        fallback_root = runtime_root / "fallback" / "error_404_v1"
        fallback_root.mkdir(parents=True, exist_ok=True)

        with patch.dict(
            os.environ,
            {
                "STATE_ZERO_PRIVATE_ROOT": tmpdir.name,
                "EMERGENCY_FALLBACK_ENABLED": "true",
            },
            clear=False,
        ):
            manager = EmergencyFallbackManager()

        return tmpdir, manager, fallback_root

    def test_manifest_path_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "EMERGENCY_FALLBACK_ENABLED": "true",
                },
                clear=False,
            ):
                manager = EmergencyFallbackManager()
                with self.assertRaises(FallbackUnavailableError):
                    manager._resolve_runtime_relative_path("../outside/card.png")

    def test_copy_to_run_output_fails_when_staged_file_missing(self):
        tmpdir, manager, fallback_root = self._build_manager_with_temp_runtime()
        self.addCleanup(tmpdir.cleanup)

        source_png = fallback_root / "card.png"
        source_mp4 = fallback_root / "card.mp4"
        source_png.write_bytes(b"png-data")
        source_mp4.write_bytes(b"mp4-data")
        manager._resolved_png_path = source_png
        manager._resolved_mp4_path = source_mp4

        output_dir = Path(tmpdir.name) / "runtime" / "output" / "missing-copy"
        with patch("emergency_fallback_manager.shutil.copy2", return_value=None):
            with self.assertRaises(FallbackUnavailableError) as ctx:
                manager.copy_to_run_output(output_dir)

        self.assertIn("missing output file", str(ctx.exception))

    def test_copy_to_run_output_fails_when_staged_file_empty(self):
        tmpdir, manager, fallback_root = self._build_manager_with_temp_runtime()
        self.addCleanup(tmpdir.cleanup)

        source_png = fallback_root / "card.png"
        source_mp4 = fallback_root / "card.mp4"
        source_png.write_bytes(b"png-data")
        source_mp4.write_bytes(b"mp4-data")
        manager._resolved_png_path = source_png
        manager._resolved_mp4_path = source_mp4

        output_dir = Path(tmpdir.name) / "runtime" / "output" / "empty-copy"

        def fake_copy2(src, dst):
            Path(dst).parent.mkdir(parents=True, exist_ok=True)
            Path(dst).write_bytes(b"")

        with patch("emergency_fallback_manager.shutil.copy2", side_effect=fake_copy2):
            with self.assertRaises(FallbackUnavailableError) as ctx:
                manager.copy_to_run_output(output_dir)

        self.assertIn("output file is empty", str(ctx.exception))

    def test_write_emergency_log_creates_output_dir(self):
        tmpdir, manager, _fallback_root = self._build_manager_with_temp_runtime()
        self.addCleanup(tmpdir.cleanup)

        manager.manifest = {
            "version": "error_404_v1",
            "title": "ERROR 404",
            "scene_description": "fallback scene",
            "local_png_path": "fallback/error_404_v1/card.png",
            "local_mp4_path": "fallback/error_404_v1/card.mp4",
            "prehosted_video_url": "https://example.com/fallback/error_404_v1/card.mp4",
            "prehosted_thumb_url": "https://example.com/fallback/error_404_v1/card.png",
        }

        output_dir = Path(tmpdir.name) / "runtime" / "output" / "new-run-dir"
        log_path = manager.write_emergency_log(
            output_dir,
            trigger_stage="Image Generation",
            reason="failed",
            publish_mode="prehosted",
            video_url="https://example.com/fallback/error_404_v1/card.mp4",
            thumb_url="https://example.com/fallback/error_404_v1/card.png",
            instagram_post_id="123",
            instagram_permalink="https://instagram.example/p/123",
        )

        self.assertTrue(output_dir.is_dir())
        self.assertTrue(log_path.is_file())

    def test_write_emergency_log_fsyncs_before_replace_and_preserves_payload(self):
        tmpdir, manager, _fallback_root = self._build_manager_with_temp_runtime()
        self.addCleanup(tmpdir.cleanup)

        manager.manifest = {
            "version": "error_404_v1",
            "title": "ERROR 404",
            "scene_description": "fallback scene",
            "local_png_path": "fallback/error_404_v1/card.png",
            "local_mp4_path": "fallback/error_404_v1/card.mp4",
            "prehosted_video_url": "https://example.com/fallback/error_404_v1/card.mp4",
            "prehosted_thumb_url": "https://example.com/fallback/error_404_v1/card.png",
        }

        output_dir = Path(tmpdir.name) / "runtime" / "output" / "fsync-run-dir"
        with patch("emergency_fallback_manager.os.fsync") as fsync_mock:
            log_path = manager.write_emergency_log(
                output_dir,
                trigger_stage="Image Generation",
                reason="failed",
                publish_mode="prehosted",
                video_url="https://example.com/fallback/error_404_v1/card.mp4",
                thumb_url="https://example.com/fallback/error_404_v1/card.png",
                instagram_post_id="123",
                instagram_permalink="https://instagram.example/p/123",
                reused_existing_post=True,
            )

        fsync_mock.assert_called_once()
        self.assertTrue(log_path.is_file())
        self.assertEqual(
            json.loads(log_path.read_text(encoding="utf-8")),
            {
                "run_date": "fsync-run-dir",
                "asset_source": "emergency_fallback",
                "fallback_version": "error_404_v1",
                "fallback_trigger_stage": "Image Generation",
                "fallback_reason": "failed",
                "publish_mode": "prehosted",
                "reused_existing_post": True,
                "title": "ERROR 404",
                "scene_description": "fallback scene",
                "instagram_post_id": "123",
                "instagram_permalink": "https://instagram.example/p/123",
                "video_path_or_url": "https://example.com/fallback/error_404_v1/card.mp4",
                "image_path_or_url": "https://example.com/fallback/error_404_v1/card.png",
                "local_png_path": "fallback/error_404_v1/card.png",
                "local_mp4_path": "fallback/error_404_v1/card.mp4",
                "prehosted_video_url": "https://example.com/fallback/error_404_v1/card.mp4",
                "prehosted_thumb_url": "https://example.com/fallback/error_404_v1/card.png",
            },
        )

    def test_write_emergency_log_can_include_publish_failure_diagnostics(self):
        tmpdir, manager, _fallback_root = self._build_manager_with_temp_runtime()
        self.addCleanup(tmpdir.cleanup)

        manager.manifest = {
            "version": "error_404_v1",
            "title": "ERROR 404",
            "scene_description": "fallback scene",
            "local_png_path": "fallback/error_404_v1/card.png",
            "local_mp4_path": "fallback/error_404_v1/card.mp4",
            "prehosted_video_url": "https://example.com/fallback/error_404_v1/card.mp4",
            "prehosted_thumb_url": "https://example.com/fallback/error_404_v1/card.png",
        }

        output_dir = Path(tmpdir.name) / "runtime" / "output" / "failed-run"
        log_path = manager.write_emergency_log(
            output_dir,
            trigger_stage="Instagram Posting",
            reason="failed",
            publish_mode="prehosted",
            video_url="https://example.com/fallback/error_404_v1/card.mp4",
            thumb_url="https://example.com/fallback/error_404_v1/card.png",
            instagram_post_id=None,
            instagram_permalink=None,
            publish_status="failed",
            publish_diagnostics={
                "creation_id": "creation-1",
                "terminal_status_code": "ERROR",
                "response": {"headers": {"debug-link": "https://debug.example/1"}},
            },
        )

        payload = json.loads(log_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["publish_status"], "failed")
        self.assertEqual(payload["publish_diagnostics"]["creation_id"], "creation-1")
        self.assertEqual(payload["publish_diagnostics"]["response"]["headers"]["debug-link"], "https://debug.example/1")

    def test_public_url_preflight_accepts_media_and_rejects_bad_responses(self):
        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)

        def make_response(status_code, content_type, body=b"binary-data"):
            response = Mock()
            response.status_code = status_code
            response.headers = {"Content-Type": content_type}
            response.iter_content.side_effect = lambda chunk_size=1: iter([body])
            response.close = Mock()
            return response

        ok_video = make_response(200, "video/mp4", b"video-bytes")
        ok_thumb = make_response(200, "image/png", b"png-bytes")
        with patch("pipeline.requests.get", side_effect=[ok_video, ok_thumb]):
            WHOOPPipeline._ensure_public_urls_reachable(
                pipeline,
                (
                    ("video", "video", "https://example.com/video.mp4"),
                    ("image", "thumb", "https://example.com/thumb.png"),
                ),
            )

        bad_cases = [
            ("404", make_response(404, "application/octet-stream", b"not-found"), "unreachable_public_video_url"),
            ("redirect", make_response(302, "text/html", b""), "unreachable_public_video_url"),
            ("html", make_response(200, "text/html", b"<html>ok</html>"), "invalid_public_video_content_type"),
        ]
        for label, response, expected_reason in bad_cases:
            with self.subTest(label=label):
                with patch("pipeline.requests.get", return_value=response):
                    with self.assertRaises(RuntimeError) as ctx:
                        WHOOPPipeline._ensure_public_urls_reachable(
                            pipeline,
                            (("video", "video", "https://example.com/video.mp4"),),
                        )
                self.assertIn(expected_reason, str(ctx.exception))

    def test_non_retryable_lookup_failure_is_fallback_eligible(self):
        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.base_dir = PROJECT_ROOT
        pipeline.output_dir = PROJECT_ROOT / "tmp-test-output"
        pipeline._set_heartbeat_context = lambda **kwargs: None
        pipeline._build_subprocess_details_tail = lambda result: "stderr tail"
        pipeline._handle_retryable_lookup_not_ready = lambda details: self.fail("unexpected retryable lookup path")
        pipeline._handle_retryable_lookup_external_failure = lambda details: self.fail(
            "unexpected transient external retry path"
        )

        failed = subprocess.CompletedProcess(
            args=["lookups.py"],
            returncode=4,
            stdout="lookup stdout",
            stderr="lookup stderr",
        )
        with patch("pipeline.subprocess.run", return_value=failed):
            with self.assertRaises(PipelineStageError) as ctx:
                WHOOPPipeline.step_2_3_lookups(pipeline)

        self.assertEqual(ctx.exception.stage, "Data Retrieve & Dasha Lookups")
        self.assertTrue(ctx.exception.fallback_eligible)
        self.assertEqual(ctx.exception.message, "Script reported a terminal lookup failure")

    def test_emergency_fallback_writes_publish_diagnostics_when_post_fails(self):
        captured_logs = []

        fake_fallback_module = types.ModuleType("emergency_fallback_manager")

        class _FakeFallbackUnavailableError(RuntimeError):
            pass

        class _FakeManager:
            def __init__(self):
                pass

            def load_and_validate_manifest(self):
                return {
                    "version": "error_404_v1",
                    "title": "ERROR 404",
                    "scene_description": "fallback scene",
                }

            def verify_integrity(self):
                return True

            def copy_to_run_output(self, output_dir):
                mp4_path = output_dir / "card_final.mp4"
                png_path = output_dir / "card_final.png"
                mp4_path.write_bytes(b"video")
                png_path.write_bytes(b"image")
                return {"mp4_path": mp4_path, "png_path": png_path}

            def get_publish_strategy(self):
                return {
                    "mode": "prehosted",
                    "video_url": "https://prehosted.example/video.mp4",
                    "thumb_url": "https://prehosted.example/thumb.png",
                }

            def build_fallback_caption(self, run_date):
                return f"caption {run_date}"

            def write_emergency_log(self, output_dir, **kwargs):
                captured_logs.append(kwargs)
                return output_dir / "emergency_fallback_used.json"

        fake_fallback_module.EmergencyFallbackManager = _FakeManager
        fake_fallback_module.FallbackUnavailableError = _FakeFallbackUnavailableError

        fake_db_module = types.ModuleType("database_manager")

        class _FakeCardDatabase:
            def insert_fallback_post(self, payload):
                return payload

        fake_db_module.CardDatabase = _FakeCardDatabase

        failure_diagnostics = {
            "creation_id": "creation-1",
            "terminal_status_code": "ERROR",
            "response": {
                "headers": {
                    "debug-link": "https://debug.example/1",
                }
            },
        }

        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            pipeline.run_date = "2026-03-08"
            pipeline.output_dir = output_dir
            pipeline.daily_run = _FakeDailyRun()
            pipeline.asset_source = "auto_api"
            pipeline.in_emergency_fallback = False
            pipeline._last_emergency_fallback_error = None
            pipeline._last_emergency_fallback_details = None
            pipeline._last_emergency_fallback_classification = None
            pipeline._active_emergency_fallback_version = None
            pipeline._active_emergency_fallback_publish_mode = None
            pipeline._set_heartbeat_context = lambda **kwargs: None
            pipeline._ensure_owner_runtime_dirs = lambda: output_dir.mkdir(parents=True, exist_ok=True)
            pipeline._ensure_public_urls_reachable = lambda media_urls: True
            pipeline.step_12_upload_vps = lambda final_mp4, cover_image: self.fail("runtime upload should not run")
            pipeline.step_14_post_instagram = lambda *args, **kwargs: (_ for _ in ()).throw(
                PipelineStageError(
                    stage="Instagram Posting",
                    message="Media processing failed on Instagram side.",
                    details=json.dumps(failure_diagnostics),
                    details_obj=failure_diagnostics,
                    fallback_eligible=False,
                )
            )

            with patch.dict(
                sys.modules,
                {
                    "emergency_fallback_manager": fake_fallback_module,
                    "database_manager": fake_db_module,
                },
            ):
                with patch("pipeline.get_notifier", return_value=_FakeNotifier()):
                    success = WHOOPPipeline._run_emergency_fallback(
                        pipeline,
                        "Instagram Posting",
                        "rerun after crash",
                    )

        self.assertFalse(success)
        self.assertEqual(len(captured_logs), 1)
        self.assertEqual(captured_logs[0]["publish_status"], "failed")
        self.assertEqual(captured_logs[0]["publish_mode"], "prehosted")
        self.assertEqual(captured_logs[0]["publish_diagnostics"]["creation_id"], "creation-1")
        self.assertEqual(
            captured_logs[0]["publish_diagnostics"]["response"]["headers"]["debug-link"],
            "https://debug.example/1",
        )

    def test_retryable_lookup_failure_before_rescue_releases_for_retry(self):
        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.base_dir = PROJECT_ROOT
        pipeline.output_dir = PROJECT_ROOT / "tmp-test-output"
        pipeline._set_heartbeat_context = lambda **kwargs: None
        pipeline._build_subprocess_details_tail = lambda result: "stderr tail"
        pipeline._handle_retryable_lookup_not_ready = lambda details: self.fail("unexpected whoop-not-ready path")

        seen = {}

        def _record_retry(details):
            seen["details"] = details
            raise SystemExit(0)

        pipeline._handle_retryable_lookup_external_failure = _record_retry

        failed = subprocess.CompletedProcess(
            args=["lookups.py"],
            returncode=3,
            stdout="lookup stdout",
            stderr="lookup stderr",
        )
        with patch("pipeline.subprocess.run", return_value=failed):
            with self.assertRaises(SystemExit) as ctx:
                WHOOPPipeline.step_2_3_lookups(pipeline)

        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(seen["details"], "stderr tail")

    def test_terminal_rescue_run_converts_whoop_not_ready_to_fallback_eligible_error(self):
        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        stop_calls = []
        pipeline._stop_heartbeat_thread = lambda: stop_calls.append("stopped")
        pipeline.daily_run = types.SimpleNamespace(
            mark_retryable_failure=lambda **kwargs: self.fail("should not mark retryable on rescue path"),
            release_claim=lambda: self.fail("should not release claim on rescue path"),
        )

        with patch.dict(os.environ, {"PIPELINE_TERMINAL_RESCUE_RUN": "true"}, clear=False):
            with self.assertRaises(PipelineStageError) as ctx:
                WHOOPPipeline._handle_retryable_lookup_not_ready(pipeline, "whoop still missing")

        self.assertEqual(ctx.exception.stage, "WHOOP Data Unavailable")
        self.assertTrue(ctx.exception.fallback_eligible)
        self.assertIn("Terminal rescue run", ctx.exception.message)
        self.assertEqual(stop_calls, ["stopped"])

    def test_deadline_infers_terminal_rescue_for_whoop_not_ready(self):
        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        stop_calls = []
        pipeline._stop_heartbeat_thread = lambda: stop_calls.append("stopped")
        pipeline.deadline_dt = datetime(2026, 3, 8, 14, 0, 0)
        pipeline._now = lambda: datetime(2026, 3, 8, 14, 0, 3)
        pipeline.daily_run = types.SimpleNamespace(
            mark_retryable_failure=lambda **kwargs: self.fail("should not mark retryable on inferred rescue path"),
            release_claim=lambda: self.fail("should not release claim on inferred rescue path"),
        )

        with patch.dict(os.environ, {"PIPELINE_TERMINAL_RESCUE_RUN": "false"}, clear=False):
            with self.assertRaises(PipelineStageError) as ctx:
                WHOOPPipeline._handle_retryable_lookup_not_ready(pipeline, "whoop still missing")

        self.assertEqual(ctx.exception.stage, "WHOOP Data Unavailable")
        self.assertTrue(ctx.exception.fallback_eligible)
        self.assertEqual(ctx.exception.failure_classification, "lookup_not_ready")
        self.assertEqual(stop_calls, ["stopped"])

    def test_terminal_rescue_run_stops_real_heartbeat_thread_before_escalation(self):
        fail = self.fail

        class _HeartbeatDailyRun:
            def is_owner(self):
                return True

            def heartbeat(self, **kwargs):
                return kwargs

            def mark_retryable_failure(self, **kwargs):
                fail("should not mark retryable on rescue path")

            def release_claim(self):
                fail("should not release claim on rescue path")

        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.run_date = "2026-03-08"
        pipeline.daily_run = _HeartbeatDailyRun()
        pipeline._heartbeat_stop = threading.Event()
        pipeline._heartbeat_thread = None
        pipeline._heartbeat_lock = threading.Lock()
        pipeline._heartbeat_status = "STARTING"
        pipeline._heartbeat_note = "testing"

        WHOOPPipeline._start_heartbeat_thread(pipeline)
        heartbeat_thread = pipeline._heartbeat_thread
        self.assertIsNotNone(heartbeat_thread)
        self.assertTrue(heartbeat_thread.is_alive())

        with patch.dict(os.environ, {"PIPELINE_TERMINAL_RESCUE_RUN": "true"}, clear=False):
            with self.assertRaises(PipelineStageError):
                WHOOPPipeline._handle_retryable_lookup_not_ready(pipeline, "whoop still missing")

        time.sleep(0.05)
        self.assertIsNone(pipeline._heartbeat_thread)
        self.assertFalse(heartbeat_thread.is_alive())

    def test_pre_cutoff_stage_error_releases_retryable_run(self):
        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.run_date = "2026-03-08"
        pipeline.post_to_instagram = True
        pipeline.in_emergency_fallback = False
        pipeline.deadline_dt = datetime(2026, 3, 8, 14, 0, 0)
        pipeline._now = lambda: datetime(2026, 3, 8, 13, 30, 0)
        pipeline._stop_heartbeat_thread = lambda: None
        pipeline.daily_run = _TrackingDailyRun()

        notifications = []
        notifier = types.SimpleNamespace(notify_warning=lambda **kwargs: notifications.append(kwargs))
        pipeline._run_emergency_fallback = lambda *args, **kwargs: self.fail("fallback should not run before cutoff")

        with patch.dict(os.environ, {"PIPELINE_TERMINAL_RESCUE_RUN": "false"}, clear=False):
            with patch("pipeline.get_notifier", return_value=notifier):
                with self.assertRaises(SystemExit) as ctx:
                    WHOOPPipeline._handle_runtime_stage_error(
                        pipeline,
                        PipelineStageError(stage="Image Generation", message="image failed", details="boom", fallback_eligible=True),
                    )

        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(pipeline.daily_run.release_calls, 1)
        self.assertEqual(pipeline.daily_run.retryable_calls[0]["failure_classification"], "generation")
        self.assertIn("Next retry ~", notifications[0]["message"])

    def test_validation_fallback_unavailable_fails_fatally_before_cutoff(self):
        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.base_dir = PROJECT_ROOT
        pipeline.output_dir = PROJECT_ROOT / "tmp-test-output"
        pipeline._set_heartbeat_context = lambda **kwargs: None

        failed = subprocess.CompletedProcess(
            args=["validate.py"],
            returncode=1,
            stdout="❌ Emergency fallback validation failed: Manifest not found\nEMERGENCY_FALLBACK_UNAVAILABLE: Manifest not found",
            stderr="",
        )

        with patch("pipeline.subprocess.run", return_value=failed):
            with self.assertRaises(PipelineStageError) as ctx:
                WHOOPPipeline.step_1_validate(pipeline)

        self.assertFalse(ctx.exception.fallback_eligible)
        self.assertEqual(ctx.exception.stage, "Validation")
        self.assertEqual(ctx.exception.failure_classification, "fallback_unavailable")
        self.assertIn("Manifest not found", ctx.exception.message)

    def test_terminal_rescue_stage_error_attempts_emergency_fallback(self):
        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.run_date = "2026-03-08"
        pipeline.post_to_instagram = True
        pipeline.in_emergency_fallback = False
        pipeline.deadline_dt = datetime(2026, 3, 8, 14, 0, 0)
        pipeline._now = lambda: datetime(2026, 3, 8, 14, 5, 0)
        pipeline.daily_run = _TrackingDailyRun()
        pipeline._run_emergency_fallback = lambda stage, message: (stage, message) == ("Image Generation", "image failed")

        notifier_calls = []
        notifier = types.SimpleNamespace(notify_warning=lambda **kwargs: notifier_calls.append(kwargs))

        with patch.dict(os.environ, {"PIPELINE_TERMINAL_RESCUE_RUN": "false", "EMERGENCY_FALLBACK_ENABLED": "true"}, clear=False):
            with patch("pipeline.get_notifier", return_value=notifier):
                success = WHOOPPipeline._handle_runtime_stage_error(
                    pipeline,
                    PipelineStageError(stage="Image Generation", message="image failed", details="boom", fallback_eligible=True),
                )

        self.assertTrue(success)
        self.assertEqual(len(pipeline.daily_run.retryable_calls), 0)
        self.assertEqual(notifier_calls[0]["step"], "Image Generation")

    def test_caption_build_failure_is_fallback_eligible(self):
        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.step_13_build_caption = lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("caption broke"))

        with self.assertRaises(PipelineStageError) as ctx:
            WHOOPPipeline._build_caption_or_raise(
                pipeline,
                {"title": "ERROR 404"},
                {"date": "2026-03-08"},
            )

        self.assertEqual(ctx.exception.stage, "Caption Build")
        self.assertTrue(ctx.exception.fallback_eligible)
        self.assertIn("Caption building failed unexpectedly.", ctx.exception.message)

    def test_run_does_not_require_interpretation_file_for_normal_post_path(self):
        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            pipeline.run_date = "2026-03-08"
            pipeline.output_dir = output_dir
            pipeline.mode = "automatic"
            pipeline.post_to_instagram = False
            pipeline.media_mode = "live_vps"
            pipeline.run_token = "token123"
            pipeline.daily_run = _FakeDailyRun()
            pipeline._claim_daily_run_or_exit = lambda: None
            pipeline._ensure_owner_runtime_dirs = lambda: output_dir.mkdir(parents=True, exist_ok=True)
            pipeline._stop_heartbeat_thread = lambda: None
            pipeline._cleanup_non_authoritative_daily_state = lambda: None
            pipeline.step_1_validate = lambda: None
            pipeline.step_2_3_lookups = lambda: {"date": "2026-03-08", "date_display": "08 MAR 2026", "dasha": {}}
            pipeline.step_4_6_prompts = lambda: None
            pipeline._load_required_json = lambda path, label: (
                {"prompt": "x"} if label == "image_prompt.json" else {"title": "ERROR 404"}
            )
            pipeline._load_required_text_outputs = lambda: ("blend", "creature", "environment")
            art_path = output_dir / "art.png"
            video_path = output_dir / "video.mp4"
            final_png = output_dir / "card_final.png"
            final_mp4 = output_dir / "card_final.mp4"
            art_path.write_bytes(b"art")
            video_path.write_bytes(b"video")
            final_png.write_bytes(b"png")
            final_mp4.write_bytes(b"mp4")
            pipeline.step_7_generate_image = lambda image_json: art_path
            pipeline.step_9_generate_video = lambda art, prompt_path: video_path
            pipeline.step_10a_render_image = lambda art, daily_data, metadata: final_png
            pipeline.step_10b_render_video = lambda video, daily_data, metadata: final_mp4
            pipeline.step_15_archive = lambda *args, **kwargs: None
            pipeline._set_heartbeat_context = lambda **kwargs: None

            with patch("pipeline._setup_global_exception_handler", lambda instance: None):
                with patch("pipeline.get_notifier", return_value=_FakeNotifier()):
                    pipeline.run()

    def test_instagram_post_mock_mode_returns_consistent_dict(self):
        fake_module = types.ModuleType("instagram_token_manager")

        class _MockTokenManager:
            def get_valid_token(self):
                return "mock"

            def get_user_id(self):
                return "123"

        fake_module.get_instagram_token_manager = lambda: _MockTokenManager()

        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.daily_run = _FakeDailyRun()
        pipeline.post_to_instagram = True
        pipeline.in_emergency_fallback = False

        with patch.dict(sys.modules, {"instagram_token_manager": fake_module}):
            result = WHOOPPipeline.step_14_post_instagram(
                pipeline,
                "https://example.com/video.mp4",
                "https://example.com/thumb.png",
                "caption",
            )

        self.assertEqual(
            result,
            {
                "already_posted": False,
                "post_id": "mock_ig_12345",
                "permalink": None,
                "mock": True,
            },
        )

    def test_instagram_token_preflight_failure_raises_pipeline_stage_error(self):
        fake_module = types.ModuleType("instagram_token_manager")

        class _BrokenTokenManager:
            def get_valid_token(self):
                raise RuntimeError("preflight broke")

            def get_user_id(self):
                return "123"

        fake_module.get_instagram_token_manager = lambda: _BrokenTokenManager()

        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline._set_heartbeat_context = lambda **kwargs: None

        with patch.dict(sys.modules, {"instagram_token_manager": fake_module}):
            with self.assertRaises(PipelineStageError) as ctx:
                WHOOPPipeline.step_1b_validate_instagram_token(pipeline)

        self.assertEqual(ctx.exception.stage, "Instagram Token Preflight")
        self.assertEqual(ctx.exception.failure_classification, "instagram_main_post")

    def test_instagram_token_failure_raises_in_emergency_fallback(self):
        fake_module = types.ModuleType("instagram_token_manager")

        class _BrokenTokenManager:
            def get_valid_token(self):
                raise RuntimeError("token fetch broke")

            def get_user_id(self):
                return "123"

        fake_module.get_instagram_token_manager = lambda: _BrokenTokenManager()

        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.daily_run = _FakeDailyRun()
        pipeline.post_to_instagram = True
        pipeline.in_emergency_fallback = True
        pipeline.log_error = lambda *args, **kwargs: self.fail("log_error should not run in emergency fallback token failures")

        with patch.dict(sys.modules, {"instagram_token_manager": fake_module}):
            with self.assertRaises(PipelineStageError) as ctx:
                WHOOPPipeline.step_14_post_instagram(
                    pipeline,
                    "https://example.com/video.mp4",
                    "https://example.com/thumb.png",
                    "caption",
                )

        self.assertEqual(ctx.exception.stage, "Instagram Token")
        self.assertIn("emergency fallback", ctx.exception.message.lower())

    def test_instagram_token_failure_raises_pipeline_stage_error_on_main_path(self):
        fake_module = types.ModuleType("instagram_token_manager")

        class _BrokenTokenManager:
            def get_valid_token(self):
                raise RuntimeError("token fetch broke")

            def get_user_id(self):
                return "123"

        fake_module.get_instagram_token_manager = lambda: _BrokenTokenManager()

        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.daily_run = _FakeDailyRun()
        pipeline.post_to_instagram = True
        pipeline.in_emergency_fallback = False

        with patch.dict(sys.modules, {"instagram_token_manager": fake_module}):
            with self.assertRaises(PipelineStageError) as ctx:
                WHOOPPipeline.step_14_post_instagram(
                    pipeline,
                    "https://example.com/video.mp4",
                    "https://example.com/thumb.png",
                    "caption",
                )

        self.assertEqual(ctx.exception.stage, "Instagram Token")
        self.assertEqual(ctx.exception.failure_classification, "instagram_main_post")

    def test_emergency_fallback_uses_runtime_upload_when_prehosted_urls_unreachable(self):
        inserted_rows = []
        log_publish_modes = []

        fake_fallback_module = types.ModuleType("emergency_fallback_manager")

        class _FakeFallbackUnavailableError(RuntimeError):
            pass

        class _FakeManager:
            def __init__(self):
                pass

            def load_and_validate_manifest(self):
                return {
                    "version": "error_404_v1",
                    "title": "ERROR 404",
                    "scene_description": "fallback scene",
                }

            def verify_integrity(self):
                return True

            def copy_to_run_output(self, output_dir):
                mp4_path = output_dir / "card_final.mp4"
                png_path = output_dir / "card_final.png"
                mp4_path.write_bytes(b"video")
                png_path.write_bytes(b"image")
                return {"mp4_path": mp4_path, "png_path": png_path}

            def get_publish_strategy(self):
                return {
                    "mode": "prehosted",
                    "video_url": "https://prehosted.example/video.mp4",
                    "thumb_url": "https://prehosted.example/thumb.png",
                }

            def build_fallback_caption(self, run_date):
                return f"caption {run_date}"

            def write_emergency_log(self, output_dir, **kwargs):
                log_publish_modes.append(kwargs["publish_mode"])
                return output_dir / "emergency_fallback_used.json"

        fake_fallback_module.EmergencyFallbackManager = _FakeManager
        fake_fallback_module.FallbackUnavailableError = _FakeFallbackUnavailableError

        fake_db_module = types.ModuleType("database_manager")

        class _FakeCardDatabase:
            def insert_fallback_post(self, payload):
                inserted_rows.append(payload)

        fake_db_module.CardDatabase = _FakeCardDatabase

        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            pipeline.run_date = "2026-03-08"
            pipeline.output_dir = output_dir
            pipeline.daily_run = _FakeDailyRun()
            pipeline.asset_source = "auto_api"
            pipeline.in_emergency_fallback = False
            pipeline._last_emergency_fallback_error = None
            pipeline._last_emergency_fallback_details = None
            pipeline._last_emergency_fallback_classification = None
            pipeline._active_emergency_fallback_version = None
            pipeline._active_emergency_fallback_publish_mode = None
            pipeline._set_heartbeat_context = lambda **kwargs: None
            pipeline._ensure_owner_runtime_dirs = lambda: output_dir.mkdir(parents=True, exist_ok=True)
            pipeline._ensure_public_urls_reachable = lambda media_urls: (_ for _ in ()).throw(RuntimeError("prehosted down"))
            pipeline.step_12_upload_vps = lambda final_mp4, cover_image: (
                "https://runtime.example/video.mp4",
                "https://runtime.example/thumb.png",
            )
            pipeline.step_14_post_instagram = lambda *args, **kwargs: {
                "already_posted": False,
                "post_id": "123",
                "permalink": "https://instagram.example/p/123",
                "mock": False,
            }

            with patch.dict(
                sys.modules,
                {
                    "emergency_fallback_manager": fake_fallback_module,
                    "database_manager": fake_db_module,
                },
            ):
                with patch("pipeline.get_notifier", return_value=_FakeNotifier()):
                    success = WHOOPPipeline._run_emergency_fallback(
                        pipeline,
                        "Image Generation",
                        "image generation failed",
                    )

        self.assertTrue(success)
        self.assertEqual(log_publish_modes, ["runtime_vps_upload"])
        self.assertEqual(inserted_rows[0]["publish_mode"], "runtime_vps_upload")

    def test_emergency_fallback_keeps_prehosted_publish_when_video_kind_is_explicit(self):
        inserted_rows = []
        log_publish_modes = []

        fake_fallback_module = types.ModuleType("emergency_fallback_manager")

        class _FakeFallbackUnavailableError(RuntimeError):
            pass

        class _FakeManager:
            def load_and_validate_manifest(self):
                return {
                    "version": "error_404_v1",
                    "title": "ERROR 404",
                    "scene_description": "fallback scene",
                }

            def verify_integrity(self):
                return True

            def copy_to_run_output(self, output_dir):
                mp4_path = output_dir / "card_final.mp4"
                png_path = output_dir / "card_final.png"
                mp4_path.write_bytes(b"video")
                png_path.write_bytes(b"image")
                return {"mp4_path": mp4_path, "png_path": png_path}

            def get_publish_strategy(self):
                return {
                    "mode": "prehosted",
                    "video_url": "https://prehosted.example/video.mp4",
                    "thumb_url": "https://prehosted.example/thumb.png",
                }

            def build_fallback_caption(self, run_date):
                return f"caption {run_date}"

            def write_emergency_log(self, output_dir, **kwargs):
                log_publish_modes.append(kwargs["publish_mode"])
                return output_dir / "emergency_fallback_used.json"

        fake_fallback_module.EmergencyFallbackManager = _FakeManager
        fake_fallback_module.FallbackUnavailableError = _FakeFallbackUnavailableError

        fake_db_module = types.ModuleType("database_manager")

        class _FakeCardDatabase:
            def insert_fallback_post(self, payload):
                inserted_rows.append(payload)

        fake_db_module.CardDatabase = _FakeCardDatabase

        def make_response(status_code, content_type, body=b"binary-data"):
            response = Mock()
            response.status_code = status_code
            response.headers = {"Content-Type": content_type}
            response.iter_content.side_effect = lambda chunk_size=1: iter([body])
            response.close = Mock()
            return response

        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            pipeline.run_date = "2026-03-08"
            pipeline.output_dir = output_dir
            pipeline.daily_run = _FakeDailyRun()
            pipeline.asset_source = "auto_api"
            pipeline.in_emergency_fallback = False
            pipeline._last_emergency_fallback_error = None
            pipeline._last_emergency_fallback_details = None
            pipeline._last_emergency_fallback_classification = None
            pipeline._active_emergency_fallback_version = None
            pipeline._active_emergency_fallback_publish_mode = None
            pipeline._set_heartbeat_context = lambda **kwargs: None
            pipeline._ensure_owner_runtime_dirs = lambda: output_dir.mkdir(parents=True, exist_ok=True)
            pipeline.step_12_upload_vps = lambda *args, **kwargs: self.fail("runtime upload should not run")
            pipeline.step_14_post_instagram = lambda *args, **kwargs: {
                "already_posted": False,
                "post_id": "123",
                "permalink": "https://instagram.example/p/123",
                "mock": False,
            }

            with patch.dict(
                sys.modules,
                {
                    "emergency_fallback_manager": fake_fallback_module,
                    "database_manager": fake_db_module,
                },
            ):
                with patch("pipeline.requests.get", side_effect=[
                    make_response(200, "video/mp4", b"video"),
                    make_response(200, "image/png", b"image"),
                ]):
                    with patch("pipeline.get_notifier", return_value=_FakeNotifier()):
                        success = WHOOPPipeline._run_emergency_fallback(
                            pipeline,
                            "Image Generation",
                            "image generation failed",
                        )

        self.assertTrue(success)
        self.assertEqual(log_publish_modes, ["prehosted"])
        self.assertEqual(inserted_rows[0]["publish_mode"], "prehosted")

    def test_emergency_fallback_already_posted_still_writes_log_and_db(self):
        inserted_rows = []
        emergency_logs = []

        fake_fallback_module = types.ModuleType("emergency_fallback_manager")

        class _FakeFallbackUnavailableError(RuntimeError):
            pass

        class _FakeManager:
            def load_and_validate_manifest(self):
                return {
                    "version": "error_404_v1",
                    "title": "ERROR 404",
                    "scene_description": "fallback scene",
                }

            def verify_integrity(self):
                return True

            def copy_to_run_output(self, output_dir):
                mp4_path = output_dir / "card_final.mp4"
                png_path = output_dir / "card_final.png"
                mp4_path.write_bytes(b"video")
                png_path.write_bytes(b"image")
                return {"mp4_path": mp4_path, "png_path": png_path}

            def get_publish_strategy(self):
                return {
                    "mode": "prehosted",
                    "video_url": "https://prehosted.example/video.mp4",
                    "thumb_url": "https://prehosted.example/thumb.png",
                }

            def build_fallback_caption(self, run_date):
                return f"caption {run_date}"

            def write_emergency_log(self, output_dir, **kwargs):
                emergency_logs.append(kwargs)
                return output_dir / "emergency_fallback_used.json"

        fake_fallback_module.EmergencyFallbackManager = _FakeManager
        fake_fallback_module.FallbackUnavailableError = _FakeFallbackUnavailableError

        fake_db_module = types.ModuleType("database_manager")

        class _FakeCardDatabase:
            def insert_fallback_post(self, payload):
                inserted_rows.append(payload)

        fake_db_module.CardDatabase = _FakeCardDatabase

        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            pipeline.run_date = "2026-03-08"
            pipeline.output_dir = output_dir
            pipeline.daily_run = _FakeDailyRun()
            pipeline.asset_source = "auto_api"
            pipeline.in_emergency_fallback = False
            pipeline._last_emergency_fallback_error = None
            pipeline._last_emergency_fallback_details = None
            pipeline._last_emergency_fallback_classification = None
            pipeline._active_emergency_fallback_version = None
            pipeline._active_emergency_fallback_publish_mode = None
            pipeline._set_heartbeat_context = lambda **kwargs: None
            pipeline._ensure_owner_runtime_dirs = lambda: output_dir.mkdir(parents=True, exist_ok=True)
            pipeline._ensure_public_urls_reachable = lambda media_urls: True
            pipeline.step_12_upload_vps = lambda final_mp4, cover_image: self.fail("runtime upload should not run")
            pipeline.step_14_post_instagram = lambda *args, **kwargs: {
                "already_posted": True,
                "post_id": "123",
                "permalink": "https://instagram.example/p/123",
                "mock": False,
            }

            with patch.dict(
                sys.modules,
                {
                    "emergency_fallback_manager": fake_fallback_module,
                    "database_manager": fake_db_module,
                },
            ):
                with patch("pipeline.get_notifier", return_value=_FakeNotifier()):
                    success = WHOOPPipeline._run_emergency_fallback(
                        pipeline,
                        "Instagram Posting",
                        "rerun after crash",
                    )

        self.assertTrue(success)
        self.assertEqual(len(emergency_logs), 1)
        self.assertTrue(emergency_logs[0]["reused_existing_post"])
        self.assertEqual(len(inserted_rows), 1)
        self.assertEqual(inserted_rows[0]["instagram_post_id"], "123")

    def test_step_14_publish_failure_in_emergency_fallback_does_not_recurse(self):
        fake_token_module = types.ModuleType("instagram_token_manager")

        class _HealthyTokenManager:
            def get_valid_token(self):
                return "token"

            def get_user_id(self):
                return "123"

        fake_token_module.get_instagram_token_manager = lambda: _HealthyTokenManager()

        fake_poster_module = types.ModuleType("instagram_poster")

        class _BrokenPoster:
            def __init__(self, access_token, user_id):
                self.access_token = access_token
                self.user_id = user_id

            def publish_with_strategy(self, **kwargs):
                raise RuntimeError("publish broke")

        class _FakeDiagnosticsError(RuntimeError):
            pass

        fake_poster_module.InstagramPoster = _BrokenPoster
        fake_poster_module.InstagramPublishDiagnosticsError = _FakeDiagnosticsError

        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.daily_run = _FakeDailyRun()
        pipeline.post_to_instagram = True
        pipeline.in_emergency_fallback = True
        pipeline.output_dir = PROJECT_ROOT / "tmp-test-output"
        pipeline.run_date = "2026-03-08"
        pipeline._active_emergency_fallback_version = "error_404_v1"
        pipeline._set_heartbeat_context = lambda **kwargs: None
        pipeline._build_instagram_publish_context = lambda video_url, thumb_url, caption, publish_strategy="resumable_binary": {
            "asset_source": "emergency_fallback",
            "public_url_checks": [{"label": "video", "reachable": True}, {"label": "thumb", "reachable": True}],
        }
        pipeline._mark_posted_terminal_success = lambda **kwargs: self.fail("should not mark posted on publish failure")

        with patch.dict(
            sys.modules,
            {
                "instagram_token_manager": fake_token_module,
                "instagram_poster": fake_poster_module,
            },
        ):
            with self.assertRaises(PipelineStageError) as ctx:
                WHOOPPipeline.step_14_post_instagram(
                    pipeline,
                    "https://example.com/video.mp4",
                    "https://example.com/thumb.png",
                    "caption",
                    fallback_eligible_on_publish_failure=False,
                )

        self.assertEqual(ctx.exception.stage, "Instagram Posting")
        self.assertIn("publish broke", ctx.exception.message)

    def test_step_14_poll_timeout_raises_stage_error(self):
        fake_token_module = types.ModuleType("instagram_token_manager")

        class _HealthyTokenManager:
            def get_valid_token(self):
                return "token"

            def get_user_id(self):
                return "123"

        fake_token_module.get_instagram_token_manager = lambda: _HealthyTokenManager()

        fake_poster_module = types.ModuleType("instagram_poster")

        class _FakeDiagnosticsError(RuntimeError):
            def __init__(self, phase, message, diagnostics):
                self.phase = phase
                self.diagnostics = diagnostics
                super().__init__(message)

            def details_tail(self, limit=4000):
                return json.dumps(self.diagnostics)

        class _Poster:
            def __init__(self, access_token, user_id):
                self.access_token = access_token
                self.user_id = user_id
                self.diagnostics_output_dir = None
                self.run_date = None

            def publish_with_strategy(self, **kwargs):
                creation_id = "creation-1"
                raise _FakeDiagnosticsError(
                    "poll_processing",
                    f"Instagram processing timed out before FINISHED for creation_id={creation_id}",
                    {
                        "phase": "poll_processing",
                        "creation_id": creation_id,
                        "terminal_status_code": "TIMEOUT",
                    },
                )

        fake_poster_module.InstagramPoster = _Poster
        fake_poster_module.InstagramPublishDiagnosticsError = _FakeDiagnosticsError

        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.daily_run = _FakeDailyRun()
        pipeline.post_to_instagram = True
        pipeline.in_emergency_fallback = False
        pipeline.output_dir = PROJECT_ROOT / "tmp-test-output"
        pipeline.run_date = "2026-03-08"
        pipeline._set_heartbeat_context = lambda **kwargs: None
        pipeline._build_instagram_publish_context = lambda video_url, thumb_url, caption, publish_strategy="resumable_binary": {
            "asset_source": "auto_api",
            "public_url_checks": [{"label": "video", "reachable": True}, {"label": "thumb", "reachable": True}],
        }
        pipeline._mark_posted_terminal_success = lambda **kwargs: self.fail("should not mark posted on timeout")

        with patch.dict(
            sys.modules,
            {
                "instagram_token_manager": fake_token_module,
                "instagram_poster": fake_poster_module,
            },
        ):
            with self.assertRaises(PipelineStageError) as ctx:
                WHOOPPipeline.step_14_post_instagram(
                    pipeline,
                    "https://example.com/video.mp4",
                    "https://example.com/thumb.png",
                    "caption",
                )

        self.assertEqual(ctx.exception.stage, "Instagram Posting")
        self.assertEqual(ctx.exception.details_obj["terminal_status_code"], "TIMEOUT")

    def test_step_14_retries_processing_error_with_cache_busted_urls(self):
        fake_token_module = types.ModuleType("instagram_token_manager")

        class _HealthyTokenManager:
            def get_valid_token(self):
                return "token"

            def get_user_id(self):
                return "123"

        fake_token_module.get_instagram_token_manager = lambda: _HealthyTokenManager()

        fake_poster_module = types.ModuleType("instagram_poster")
        seen_urls = []
        attempts = {"count": 0}

        class _FakeDiagnosticsError(RuntimeError):
            def __init__(self, phase, message, diagnostics):
                self.phase = phase
                self.diagnostics = diagnostics
                super().__init__(message)

            def details_tail(self, limit=4000):
                return json.dumps(self.diagnostics)

        class _Poster:
            def __init__(self, access_token, user_id):
                self.access_token = access_token
                self.user_id = user_id
                self.diagnostics_output_dir = None
                self.run_date = None
                self.publish_context = None

            def set_publish_context(self, context):
                self.publish_context = context

            def publish_with_strategy(self, **kwargs):
                video_url = kwargs["video_url"]
                thumb_url = kwargs["cover_url"]
                seen_urls.append((video_url, thumb_url))
                attempts["count"] += 1
                creation_id = f"creation-{attempts['count']}"
                if creation_id == "creation-1":
                    raise _FakeDiagnosticsError(
                        "poll_processing",
                        "Instagram processing returned terminal status_code=ERROR",
                        {
                            "phase": "poll_processing",
                            "creation_id": creation_id,
                            "terminal_status_code": "ERROR",
                        },
                    )
                return types.SimpleNamespace(post_id="post-1", permalink="https://instagram.com/p/post-1")

        fake_poster_module.InstagramPoster = _Poster
        fake_poster_module.InstagramPublishDiagnosticsError = _FakeDiagnosticsError

        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.daily_run = _FakeDailyRun()
        pipeline.post_to_instagram = True
        pipeline.in_emergency_fallback = False
        pipeline.output_dir = PROJECT_ROOT / "tmp-test-output"
        pipeline.run_date = "2026-03-08"
        pipeline.run_token = "token123"
        pipeline._set_heartbeat_context = lambda **kwargs: None
        pipeline._build_instagram_publish_context = lambda video_url, thumb_url, caption, publish_strategy="resumable_binary": {
            "public_url_checks": [{"label": "video", "url": video_url, "reachable": True}],
        }
        pipeline._mark_posted_terminal_success = lambda **kwargs: None

        with patch.dict(
            os.environ,
            {
                "INSTAGRAM_PROCESSING_MAX_ATTEMPTS": "2",
                "INSTAGRAM_PROCESSING_RETRY_DELAY_SECONDS": "0",
            },
            clear=False,
        ):
            with patch.dict(
                sys.modules,
                {
                    "instagram_token_manager": fake_token_module,
                    "instagram_poster": fake_poster_module,
                },
            ):
                result = WHOOPPipeline.step_14_post_instagram(
                    pipeline,
                    "https://example.com/video.mp4",
                    "https://example.com/thumb.png",
                    "caption",
                )

        self.assertEqual(result["post_id"], "post-1")
        self.assertEqual(len(seen_urls), 2)
        self.assertEqual(seen_urls[0][0], "https://example.com/video.mp4")
        self.assertEqual(seen_urls[0][1], "https://example.com/thumb.png")
        self.assertIn("ig_retry=2026-03-08-token123-2", seen_urls[1][0])
        self.assertIn("ig_retry=2026-03-08-token123-2", seen_urls[1][1])

    def test_post_failure_during_run_does_not_archive_as_dry_run(self):
        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.base_dir = PROJECT_ROOT
        pipeline.output_dir = PROJECT_ROOT / "tmp-test-output"
        pipeline.run_date = "2026-03-08"
        pipeline.post_to_instagram = True
        pipeline.mode = "automatic"
        pipeline.media_mode = "live_vps"
        pipeline.run_token = "token123"
        pipeline.asset_source = "auto_api"
        pipeline.daily_run = _FakeDailyRun()
        pipeline._claim_daily_run_or_exit = lambda: None
        pipeline._set_heartbeat_context = lambda **kwargs: None
        pipeline._start_heartbeat_thread = lambda: None
        pipeline._stop_heartbeat_thread = lambda: None
        pipeline._ensure_owner_runtime_dirs = lambda: None
        pipeline._cleanup_non_authoritative_daily_state = lambda: None
        pipeline._handle_runtime_stage_error = lambda exc: (_ for _ in ()).throw(exc)
        pipeline.step_1_validate = lambda: None
        pipeline.step_1b_validate_instagram_token = lambda: None
        pipeline.step_2_3_lookups = lambda: {"date": "2026-03-08"}
        pipeline.step_4_6_prompts = lambda: None
        metadata = {"title": "Test"}
        image_json = {"ok": True}
        pipeline._load_required_json = lambda path, label: image_json if label == "image_prompt.json" else metadata
        pipeline._load_required_text_outputs = lambda: ("blend", "creature", "environment")
        pipeline.step_7_generate_image = lambda image_json: PROJECT_ROOT / "tmp-art.png"
        pipeline.step_9_generate_video = lambda art, prompt_path: PROJECT_ROOT / "tmp-video.mp4"
        pipeline.step_10a_render_image = lambda *args, **kwargs: PROJECT_ROOT / "tmp-card.png"
        pipeline.step_10b_render_video = lambda *args, **kwargs: PROJECT_ROOT / "tmp-card.mp4"
        pipeline.step_12_upload_vps = lambda *args, **kwargs: (
            "https://example.com/video.mp4",
            "https://example.com/thumb.png",
        )
        pipeline._build_caption_or_raise = lambda metadata, daily_data: "caption"
        pipeline.step_14_post_instagram = lambda *args, **kwargs: (_ for _ in ()).throw(
            PipelineStageError(
                stage="Instagram Posting",
                message="publish timeout",
                details="publish timeout",
                fallback_eligible=True,
            )
        )
        pipeline.step_15_archive = lambda *args, **kwargs: self.fail("archive should not run after publish failure")

        with patch("pipeline._setup_global_exception_handler", lambda instance: None):
            with self.assertRaises(PipelineStageError) as ctx:
                pipeline.run()

        self.assertEqual(ctx.exception.stage, "Instagram Posting")

    def test_unexpected_lookup_exit_code_is_not_fallback_eligible(self):
        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.base_dir = PROJECT_ROOT
        pipeline.output_dir = PROJECT_ROOT / "tmp-test-output"
        pipeline._set_heartbeat_context = lambda **kwargs: None
        pipeline._build_subprocess_details_tail = lambda result: "stderr tail"
        pipeline._handle_retryable_lookup_not_ready = lambda details: self.fail("unexpected retryable lookup path")
        pipeline._handle_retryable_lookup_external_failure = lambda details: self.fail(
            "unexpected transient external retry path"
        )

        failed = subprocess.CompletedProcess(
            args=["lookups.py"],
            returncode=1,
            stdout="lookup stdout",
            stderr="lookup stderr",
        )
        with patch("pipeline.subprocess.run", return_value=failed):
            with self.assertRaises(PipelineStageError) as ctx:
                WHOOPPipeline.step_2_3_lookups(pipeline)

        self.assertFalse(ctx.exception.fallback_eligible)

    def test_post_publish_ownership_loss_recovers_posted_state(self):
        warnings = []

        class _RecoveryDailyRun:
            def is_owner(self):
                return True

            def mark_posted(self, **kwargs):
                raise pipeline_module.OwnershipLostError("ownership moved")

            def mark_posted_after_publish(self, **kwargs):
                self.recovered_kwargs = kwargs
                return kwargs

        import pipeline as pipeline_module

        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.daily_run = _RecoveryDailyRun()
        pipeline._heartbeat_lock = threading.Lock()
        pipeline._heartbeat_status = "POSTING"
        pipeline._heartbeat_note = "Posting"
        pipeline._notify_post_success_cleanup_warning = lambda *args: warnings.append(args)
        pipeline._merge_details = WHOOPPipeline._merge_details.__get__(pipeline, WHOOPPipeline)

        WHOOPPipeline._mark_posted_terminal_success(
            pipeline,
            post_id="123",
            permalink="https://instagram.example/p/123",
            note="Instagram publish succeeded.",
        )

        self.assertEqual(pipeline.daily_run.recovered_kwargs["post_id"], "123")
        self.assertEqual(len(warnings), 1)
        self.assertIn("Forced POSTED state recovery", warnings[0][1])

    def test_claim_skip_posted_triggers_archive_recovery_check(self):
        recovered_states = []

        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.run_date = "2026-03-08"
        pipeline.daily_run = types.SimpleNamespace(
            acquire=lambda: ("skip_posted", {"status": "POSTED", "instagram_post_id": "123"}),
        )
        pipeline._recover_posted_archive_if_needed = lambda state: recovered_states.append(state)

        with self.assertRaises(SystemExit) as ctx:
            WHOOPPipeline._claim_daily_run_or_exit(pipeline)

        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(recovered_states, [{"status": "POSTED", "instagram_post_id": "123"}])

    def test_posted_archive_recovery_reuses_existing_run_artifacts(self):
        archived = []

        fake_db_module = types.ModuleType("database_manager")

        class _FakeCardDatabase:
            def has_complete_archive_for_date(self, run_date):
                return False

        fake_db_module.CardDatabase = _FakeCardDatabase

        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            pipeline.run_date = "2026-03-08"
            pipeline.output_dir = output_dir
            pipeline.post_to_instagram = True
            pipeline._load_required_text_outputs = lambda: ("blend", "creature", "environment")
            pipeline._merge_details = WHOOPPipeline._merge_details.__get__(pipeline, WHOOPPipeline)
            pipeline._notify_post_success_cleanup_warning = lambda *args, **kwargs: self.fail("recovery should succeed")
            pipeline.finalize_posted_run = lambda *args: archived.append(args)

            (output_dir / "daily_data.json").write_text(json.dumps({"date": "2026-03-08", "dasha": {}}), encoding="utf-8")
            (output_dir / "card_metadata.json").write_text(
                json.dumps({"title": "ERROR 404", "scene_description": "fallback scene"}),
                encoding="utf-8",
            )
            (output_dir / "image_prompt.json").write_text(json.dumps({"prompt": "x"}), encoding="utf-8")
            (output_dir / "card_final.png").write_bytes(b"png")
            (output_dir / "card_final.mp4").write_bytes(b"mp4")

            with patch.dict(sys.modules, {"database_manager": fake_db_module}):
                WHOOPPipeline._recover_posted_archive_if_needed(
                    pipeline,
                    {"instagram_post_id": "123", "instagram_permalink": "https://instagram.example/p/123"},
                )

        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0][5], "123")
        self.assertEqual(archived[0][6], "https://instagram.example/p/123")

    def test_archive_failure_after_post_is_warning_only(self):
        class _RecordingNotifier(_FakeNotifier):
            def __init__(self):
                self.warning_calls = []

            def notify_warning(self, **kwargs):
                self.warning_calls.append(kwargs)
                return True

        notifier = _RecordingNotifier()
        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.base_dir = PROJECT_ROOT
        pipeline.post_to_instagram = True
        pipeline.run_date = "2026-03-08"
        pipeline.output_dir = PROJECT_ROOT / "tmp-test-output"
        pipeline._set_heartbeat_context = lambda **kwargs: None

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            pipeline.output_dir = output_dir
            final_png = output_dir / "card_final.png"
            final_mp4 = output_dir / "card_final.mp4"
            final_png.write_bytes(b"png")
            final_mp4.write_bytes(b"mp4")

            failed = subprocess.CompletedProcess(
                args=["database_manager.py"],
                returncode=1,
                stdout="db stdout",
                stderr="db stderr",
            )
            with patch("pipeline.subprocess.run", return_value=failed):
                with patch("pipeline.get_notifier", return_value=notifier):
                    WHOOPPipeline.step_15_archive(
                        pipeline,
                        daily_data={"date": "2026-03-08", "dasha": {}},
                        metadata={"title": "ERROR 404", "scene_description": "fallback scene"},
                        final_png=final_png,
                        final_mp4=final_mp4,
                        image_json={"prompt": "x"},
                        post_id="123",
                        instagram_permalink="https://instagram.example/p/123",
                        blend_option="blend",
                        creature="creature",
                        environment="environment",
                    )

        self.assertEqual(len(notifier.warning_calls), 1)
        self.assertEqual(notifier.warning_calls[0]["step"], "Database Archive")


if __name__ == "__main__":
    unittest.main()

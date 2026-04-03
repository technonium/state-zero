import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "src/scripts"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import lookups as lookups_module
from daily_run_state import DailyRunStateManager
from instagram_token_manager import InstagramTokenManager
from database_manager import CardDatabase
from ops.instagram_token_healthcheck import _format_expiry_window, _load_state, _save_state
from pipeline import WHOOPPipeline


class _FakeRefreshResponse:
    def __init__(self, access_token: str, status_code: int = 200):
        self.status_code = status_code
        self._access_token = access_token
        self.text = "ok"

    def json(self):
        return {
            "access_token": self._access_token,
            "expires_in": 5_184_000,
        }


class ReliabilityHardeningTests(unittest.TestCase):
    def _build_manual_session_pipeline(self, tmpdir: str) -> WHOOPPipeline:
        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.output_dir = Path(tmpdir) / "runtime" / "output" / "2026-03-09"
        pipeline.output_dir.mkdir(parents=True, exist_ok=True)
        pipeline.session_file = pipeline.output_dir / "manual_session.json"
        pipeline.run_date = "2026-03-09"
        pipeline.run_token = "owner-token"
        pipeline.pipeline_timezone = "Asia/Kolkata"
        pipeline.manual_deadline_mode = "scheduled"
        pipeline.deadline_reason = "test window"
        pipeline.deadline_dt = datetime(2026, 3, 9, 18, 0, 0)
        pipeline._now = lambda: datetime(2026, 3, 9, 12, 0, 0)
        pipeline.daily_run = types.SimpleNamespace(load_state=lambda: {"run_token": "owner-token"})
        return pipeline

    def test_release_claim_keeps_corrupt_claim_without_matching_run_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"STATE_ZERO_PRIVATE_ROOT": tmpdir}, clear=False):
                manager = DailyRunStateManager(
                    run_date="2026-03-08",
                    timezone_name="Asia/Kolkata",
                    run_token="owner-token",
                )
                manager.claim_path.write_text(json.dumps({"date": "2026-03-08"}), encoding="utf-8")
                manager.release_claim()

                self.assertTrue(manager.claim_path.exists())

    def test_fresh_claim_without_state_is_still_treated_as_active(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"STATE_ZERO_PRIVATE_ROOT": tmpdir}, clear=False):
                owner = DailyRunStateManager(
                    run_date="2026-03-08",
                    timezone_name="Asia/Kolkata",
                    run_token="owner-token",
                )
                owner.claim_path.write_text(
                    json.dumps(
                        {
                            "date": "2026-03-08",
                            "run_token": "owner-token",
                            "claimed_at": owner.now_iso(),
                        }
                    ),
                    encoding="utf-8",
                )

                contender = DailyRunStateManager(
                    run_date="2026-03-08",
                    timezone_name="Asia/Kolkata",
                    run_token="other-token",
                )
                decision, _state = contender.acquire()

                self.assertEqual(decision, "skip_active")

    def _build_token_manager(self, tmpdir: str, **env_overrides) -> InstagramTokenManager:
        env = {
            "STATE_ZERO_PRIVATE_ROOT": tmpdir,
            "INSTAGRAM_ACCESS_TOKEN": "old-token",
            "INSTAGRAM_USER_ID": "123",
            "INSTAGRAM_AUTO_REFRESH_MODE": "hybrid",
            "INSTAGRAM_REFRESH_THRESHOLD_DAYS": "14",
            "INSTAGRAM_REFRESH_COOLDOWN_HOURS": "12",
            "FACEBOOK_APP_ID": "",
            "FACEBOOK_APP_SECRET": "",
        }
        env.update(env_overrides)
        with patch.dict(os.environ, env, clear=False):
            return InstagramTokenManager()

    def test_corrupt_token_state_preserves_recent_refresh_cooldown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "runtime" / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            state_path = state_dir / "instagram_token_state.json"
            state_path.write_text("{not-json", encoding="utf-8")
            now = time.time()
            os.utime(state_path, (now, now))

            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "INSTAGRAM_ACCESS_TOKEN": "",
                    "INSTAGRAM_USER_ID": "123",
                },
                clear=False,
            ):
                manager = InstagramTokenManager()

            self.assertIsNotNone(manager.last_refresh_attempt_at)
            self.assertFalse(manager._refresh_attempt_allowed())

    def test_needs_refresh_uses_configured_threshold_buffer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = self._build_token_manager(tmpdir, INSTAGRAM_REFRESH_THRESHOLD_DAYS="14")
            manager.last_refresh = datetime.now() - timedelta(days=45, hours=12)
            self.assertFalse(manager._needs_refresh())

            manager.last_refresh = datetime.now() - timedelta(days=46, minutes=5)
            self.assertTrue(manager._needs_refresh())

    def test_concurrent_refresh_calls_only_hit_network_once(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = self._build_token_manager(tmpdir)
            call_count = 0
            call_count_lock = threading.Lock()

            async def fake_refresh_request():
                nonlocal call_count
                with call_count_lock:
                    call_count += 1
                await asyncio.sleep(0.1)
                return _FakeRefreshResponse("new-token")

            manager._perform_refresh_request = fake_refresh_request
            manager._inspect_token_health_for_value = lambda token: {
                "valid": True,
                "detail": "ok",
                "scopes": [],
                "scope_validation_skipped": True,
            }
            manager._validate_token = lambda: (manager.access_token == "new-token", "ok")

            results = []

            def worker():
                results.append(manager._refresh_token(reason="concurrency test"))

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)

            self.assertEqual(call_count, 1)
            self.assertEqual(results, [True, True])
            self.assertEqual(manager.access_token, "new-token")

    def test_scope_validation_skip_is_explicit_without_app_credentials(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = self._build_token_manager(tmpdir, FACEBOOK_APP_ID="", FACEBOOK_APP_SECRET="")
            manager._validate_token_value = lambda token: (True, "ok")

            report = manager._inspect_token_health_for_value("old-token")

            self.assertTrue(report["scope_validation_skipped"])
            self.assertEqual(report["hours_to_expiry"], None)

    def test_healthcheck_formats_subday_expiry_in_hours(self):
        self.assertEqual(_format_expiry_window(0.25, 6.0), "6.0 hour(s)")
        self.assertEqual(_format_expiry_window(2.0, 48.0), "2 day(s)")

    def test_write_json_atomic_fsyncs_before_replace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"STATE_ZERO_PRIVATE_ROOT": tmpdir}, clear=False):
                manager = DailyRunStateManager(
                    run_date="2026-03-08",
                    timezone_name="Asia/Kolkata",
                    run_token="owner-token",
                )
                with patch("daily_run_state.os.fsync") as fsync_mock:
                    manager._write_json_atomic(manager.state_path, {"status": "STARTING"})

                fsync_mock.assert_called_once()

    def test_healthcheck_save_state_fsyncs_and_round_trips_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "runtime" / "state" / "instagram_token_health_state.json"
            state = {
                "last_checked_at": "2026-03-09T12:00:00",
                "last_expiry_alert_key": "2026-04-01T00:00:00:7",
                "consecutive_refresh_failures": 1,
            }

            with patch("ops.instagram_token_healthcheck.os.fsync") as fsync_mock:
                _save_state(state_path, state)

            fsync_mock.assert_called_once()
            self.assertEqual(_load_state(state_path), state)

    def test_manual_session_save_fsyncs_and_round_trips_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = self._build_manual_session_pipeline(tmpdir)
            session = {
                "run_date": "2026-03-09",
                "mode": "telegram",
                "run_token": "owner-token",
                "status": "WAITING_MANUAL",
                "timezone": "Asia/Kolkata",
                "deadline_local": "2026-03-09T18:00:00",
                "deadline_mode": "scheduled",
                "deadline_reason": "test window",
                "created_at": "2026-03-09T12:00:00",
                "telegram": {
                    "last_update_id": 7,
                    "prompt_message_id": 101,
                    "accepted_reply_message_ids": [101, 102],
                    "image_file_id": "img-file",
                    "image_file_name": "image.png",
                    "image_message_id": 201,
                    "video_file_id": "vid-file",
                    "video_file_name": "video.mp4",
                    "video_message_id": 202,
                },
            }

            with patch("pipeline.os.fsync") as fsync_mock:
                WHOOPPipeline._save_manual_session(pipeline, session)

            fsync_mock.assert_called_once()
            self.assertEqual(json.loads(pipeline.session_file.read_text(encoding="utf-8")), session)

    def test_load_or_init_manual_session_reuses_valid_existing_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = self._build_manual_session_pipeline(tmpdir)
            session = {
                "run_date": "2026-03-09",
                "mode": "telegram",
                "run_token": "owner-token",
                "status": "WAITING_MANUAL",
                "timezone": "Asia/Kolkata",
                "deadline_local": "2026-03-09T18:00:00",
                "deadline_mode": "scheduled",
                "deadline_reason": "test window",
                "created_at": "2026-03-09T12:00:00",
                "telegram": {
                    "last_update_id": 7,
                    "prompt_message_id": 101,
                    "accepted_reply_message_ids": [101, 102],
                    "image_file_id": None,
                    "image_file_name": None,
                    "image_message_id": None,
                    "video_file_id": None,
                    "video_file_name": None,
                    "video_message_id": None,
                },
            }
            pipeline.session_file.write_text(json.dumps(session, indent=2), encoding="utf-8")
            pipeline._get_latest_update_id = lambda: self.fail("should not refresh Telegram cursor for a reusable session")

            loaded = WHOOPPipeline._load_or_init_manual_session(pipeline)

            self.assertEqual(loaded, session)

    def test_load_manual_session_warns_and_backs_up_on_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = self._build_manual_session_pipeline(tmpdir)
            pipeline.session_file.write_text("{not-json", encoding="utf-8")
            pipeline._get_latest_update_id = lambda: 77
            warning_calls = []

            class _FakeNotifier:
                def notify_warning(self, **kwargs):
                    warning_calls.append(kwargs)
                    return True

            with patch("pipeline.get_notifier", return_value=_FakeNotifier()):
                loaded = WHOOPPipeline._load_or_init_manual_session(pipeline)

            backup_path = pipeline.session_file.with_suffix(".corrupt.json")
            self.assertTrue(backup_path.is_file())
            self.assertEqual(backup_path.read_text(encoding="utf-8"), "{not-json")
            self.assertEqual(loaded["status"], "WAITING_MANUAL")
            self.assertEqual(loaded["telegram"]["last_update_id"], 77)
            self.assertEqual(len(warning_calls), 1)
            self.assertEqual(warning_calls[0]["step"], "ManualSessionLoad")
            self.assertIn("last_update_id and asset refs may be lost", warning_calls[0]["message"])
            self.assertIn("Backup saved to manual_session.corrupt.json", warning_calls[0]["message"])
            self.assertEqual(
                warning_calls[0]["details_tail"],
                f"backup={backup_path}",
            )

    def test_load_manual_session_reports_backup_failure_when_copy_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = self._build_manual_session_pipeline(tmpdir)
            pipeline.session_file.write_text("{not-json", encoding="utf-8")
            pipeline._get_latest_update_id = lambda: 88
            warning_calls = []

            class _FakeNotifier:
                def notify_warning(self, **kwargs):
                    warning_calls.append(kwargs)
                    return True

            with patch("pipeline.shutil.copy2", side_effect=OSError("disk full")):
                with patch("pipeline.get_notifier", return_value=_FakeNotifier()):
                    loaded = WHOOPPipeline._load_or_init_manual_session(pipeline)

            backup_path = pipeline.session_file.with_suffix(".corrupt.json")
            self.assertFalse(backup_path.exists())
            self.assertEqual(loaded["status"], "WAITING_MANUAL")
            self.assertEqual(loaded["telegram"]["last_update_id"], 88)
            self.assertEqual(len(warning_calls), 1)
            self.assertEqual(warning_calls[0]["step"], "ManualSessionLoad")
            self.assertIn("Backup could not be saved (disk full)", warning_calls[0]["message"])
            self.assertEqual(
                warning_calls[0]["details_tail"],
                f"backup_copy_failed path={backup_path} error=disk full",
            )

    def test_load_manual_session_recovers_from_valid_json_wrong_shape(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = self._build_manual_session_pipeline(tmpdir)
            pipeline.session_file.write_text("[]", encoding="utf-8")
            pipeline._get_latest_update_id = lambda: 55
            warning_calls = []

            class _FakeNotifier:
                def notify_warning(self, **kwargs):
                    warning_calls.append(kwargs)
                    return True

            with patch("pipeline.get_notifier", return_value=_FakeNotifier()):
                loaded = WHOOPPipeline._load_or_init_manual_session(pipeline)

            backup_path = pipeline.session_file.with_suffix(".corrupt.json")
            self.assertTrue(backup_path.is_file())
            self.assertEqual(backup_path.read_text(encoding="utf-8"), "[]")
            self.assertEqual(loaded["status"], "WAITING_MANUAL")
            self.assertEqual(loaded["telegram"]["last_update_id"], 55)
            self.assertEqual(len(warning_calls), 1)
            self.assertEqual(warning_calls[0]["step"], "ManualSessionLoad")
            self.assertIn("manual_session root must be a JSON object", warning_calls[0]["message"])

    def test_load_manual_session_does_not_back_up_on_missing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = self._build_manual_session_pipeline(tmpdir)
            pipeline._get_latest_update_id = lambda: 33

            class _FakeNotifier:
                def notify_warning(self, **kwargs):
                    self._called = True
                    return True

            fake_notifier = _FakeNotifier()
            with patch("pipeline.get_notifier", return_value=fake_notifier):
                loaded = WHOOPPipeline._load_or_init_manual_session(pipeline)

            self.assertFalse(pipeline.session_file.with_suffix(".corrupt.json").exists())
            self.assertEqual(loaded["status"], "WAITING_MANUAL")
            self.assertEqual(loaded["telegram"]["last_update_id"], 33)
            self.assertFalse(hasattr(fake_notifier, "_called"))

    def test_get_session_deadline_falls_back_for_non_string_or_invalid_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = self._build_manual_session_pipeline(tmpdir)

            self.assertEqual(
                WHOOPPipeline._get_session_deadline(pipeline, {"deadline_local": 123}),
                pipeline.deadline_dt,
            )
            self.assertEqual(
                WHOOPPipeline._get_session_deadline(pipeline, {"deadline_local": "not-a-date"}),
                pipeline.deadline_dt,
            )
            self.assertEqual(
                WHOOPPipeline._get_session_deadline(pipeline, {"deadline_local": "2026-03-09T17:00:00"}),
                datetime(2026, 3, 9, 17, 0, 0),
            )

    def test_step_15_archive_payload_is_written_atomically(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = self._build_manual_session_pipeline(tmpdir)
            pipeline.base_dir = PROJECT_ROOT
            pipeline.post_to_instagram = False
            pipeline._set_heartbeat_context = lambda **kwargs: None
            final_png = pipeline.output_dir / "card_final.png"
            final_mp4 = pipeline.output_dir / "card_final.mp4"
            final_png.write_bytes(b"png")
            final_mp4.write_bytes(b"mp4")

            with patch("pipeline.os.fsync") as fsync_mock:
                WHOOPPipeline.step_15_archive(
                    pipeline,
                    daily_data={"date": "2026-03-09", "dasha": {}, "sleep_hours": 8.0},
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

            fsync_mock.assert_called_once()
            payload_path = pipeline.output_dir / "last_archived_payload.json"
            self.assertEqual(
                json.loads(payload_path.read_text(encoding="utf-8")),
                {
                    "date": "2026-03-09",
                    "title": "ERROR 404",
                    "scene_description": "fallback scene",
                    "environment": "environment",
                    "environment_name": "environment",
                    "environment_reason": None,
                    "creature": "creature",
                    "blend_option": "blend",
                    "energy_zone": None,
                    "recovery_pct": None,
                    "sleep_score_pct": None,
                    "strain": None,
                    "sleep_hours": 8.0,
                    "depth_level": None,
                    "dasha_maha": None,
                    "dasha_antar": None,
                    "dasha_pratyantar": None,
                    "dasha_sookshma": None,
                    "dasha_prana": None,
                    "image_path": str(final_png),
                    "video_path": str(final_mp4),
                    "image_prompt_json": json.dumps({"prompt": "x"}),
                    "instagram_post_id": "123",
                    "instagram_permalink": "https://instagram.example/p/123",
                },
            )

    def test_step_15_archive_prefers_resolved_environment_for_normalized_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = self._build_manual_session_pipeline(tmpdir)
            pipeline.base_dir = PROJECT_ROOT
            pipeline.post_to_instagram = False
            pipeline._set_heartbeat_context = lambda **kwargs: None
            final_png = pipeline.output_dir / "card_final.png"
            final_mp4 = pipeline.output_dir / "card_final.mp4"
            final_png.write_bytes(b"png")
            final_mp4.write_bytes(b"mp4")
            (pipeline.output_dir / "environment_selected.txt").write_text(
                "Glacial Valley — Resolved selection",
                encoding="utf-8",
            )

            WHOOPPipeline.step_15_archive(
                pipeline,
                daily_data={"date": "2026-03-09", "dasha": {}, "sleep_hours": 8.0},
                metadata={"title": "ERROR 404", "scene_description": "fallback scene"},
                final_png=final_png,
                final_mp4=final_mp4,
                image_json={"prompt": "x"},
                post_id="123",
                instagram_permalink="https://instagram.example/p/123",
                blend_option="blend",
                creature="creature",
                environment="Totally Invalid Realm",
            )

            payload = json.loads((pipeline.output_dir / "last_archived_payload.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["environment"], "Glacial Valley — Resolved selection")
            self.assertEqual(payload["environment_name"], "Glacial Valley")
            self.assertEqual(payload["environment_reason"], "Resolved selection")

    def test_step_4_6_prompts_enables_history_persist_only_for_real_runs(self):
        pipeline = self._build_manual_session_pipeline("/tmp/state-zero-test")
        captured_calls = []

        def fake_safe_step(*args, **kwargs):
            captured_calls.append(kwargs)

        pipeline.safe_step = fake_safe_step

        pipeline.post_to_instagram = True
        WHOOPPipeline.step_4_6_prompts(pipeline)
        self.assertEqual(
            captured_calls[-1]["env_overrides"]["PIPELINE_PERSIST_ENVIRONMENT_HISTORY"],
            "true",
        )

        pipeline.post_to_instagram = False
        WHOOPPipeline.step_4_6_prompts(pipeline)
        self.assertEqual(
            captured_calls[-1]["env_overrides"]["PIPELINE_PERSIST_ENVIRONMENT_HISTORY"],
            "false",
        )

    def test_lookups_write_json_atomic_fsyncs_and_round_trips_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "runtime" / "output" / "2026-03-09" / "daily_data.json"
            payload = {"date": "2026-03-09", "sleep_hours": 8.0}

            with patch("lookups.os.fsync") as fsync_mock:
                lookups_module._write_json_atomic(path, payload)

            fsync_mock.assert_called_once()
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)

    def test_fallback_post_upsert_updates_existing_run_date(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"STATE_ZERO_PRIVATE_ROOT": tmpdir}, clear=False):
                db = CardDatabase()
                db.insert_fallback_post(
                    {
                        "run_date": "2026-03-08",
                        "asset_source": "emergency_fallback",
                        "fallback_version": "error_404_v1",
                        "fallback_trigger_stage": "Image Generation",
                        "fallback_reason": "first reason",
                        "publish_mode": "prehosted",
                        "title": "ERROR 404",
                        "scene_description": "first scene",
                        "instagram_post_id": "123",
                        "instagram_permalink": "https://instagram.example/p/123",
                        "video_path_or_url": "https://example.com/video-a.mp4",
                        "image_path_or_url": "https://example.com/image-a.png",
                    }
                )
                db.insert_fallback_post(
                    {
                        "run_date": "2026-03-08",
                        "asset_source": "emergency_fallback",
                        "fallback_version": "error_404_v1",
                        "fallback_trigger_stage": "Instagram Posting",
                        "fallback_reason": "second reason",
                        "publish_mode": "runtime_vps_upload",
                        "title": "ERROR 404",
                        "scene_description": "second scene",
                        "instagram_post_id": "456",
                        "instagram_permalink": "https://instagram.example/p/456",
                        "video_path_or_url": "https://example.com/video-b.mp4",
                        "image_path_or_url": "https://example.com/image-b.png",
                    }
                )

                conn = sqlite3.connect(db.db_path)
                row = conn.execute(
                    """
                    SELECT fallback_trigger_stage, fallback_reason, publish_mode,
                           instagram_post_id, instagram_permalink
                    FROM fallback_posts
                    WHERE run_date = ?
                    """,
                    ("2026-03-08",),
                ).fetchone()
                conn.close()

        self.assertEqual(
            row,
            (
                "Instagram Posting",
                "second reason",
                "runtime_vps_upload",
                "456",
                "https://instagram.example/p/456",
            ),
        )

    def test_card_upsert_updates_existing_run_date(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"STATE_ZERO_PRIVATE_ROOT": tmpdir}, clear=False):
                db = CardDatabase()
                db.insert_card(
                    {
                        "date": "2026-03-08",
                        "title": "First Title",
                        "scene_description": "first scene",
                        "image_path": "/tmp/first.png",
                        "video_path": "/tmp/first.mp4",
                        "instagram_post_id": "123",
                        "instagram_permalink": "https://instagram.example/p/123",
                    }
                )
                db.insert_card(
                    {
                        "date": "2026-03-08",
                        "title": "Second Title",
                        "scene_description": "second scene",
                        "image_path": "/tmp/second.png",
                        "video_path": "/tmp/second.mp4",
                        "instagram_post_id": "456",
                        "instagram_permalink": "https://instagram.example/p/456",
                    }
                )

                conn = sqlite3.connect(db.db_path)
                row = conn.execute(
                    """
                    SELECT title, scene_description, image_path, video_path,
                           instagram_post_id, instagram_permalink, environment_name, environment_reason
                    FROM cards
                    WHERE date = ?
                    """,
                    ("2026-03-08",),
                ).fetchone()
                conn.close()

        self.assertEqual(
            row,
            (
                "Second Title",
                "second scene",
                "/tmp/second.png",
                "/tmp/second.mp4",
                "456",
                "https://instagram.example/p/456",
                None,
                None,
            ),
        )

    def test_recent_environment_names_fall_back_to_parsing_raw_environment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"STATE_ZERO_PRIVATE_ROOT": tmpdir}, clear=False):
                db = CardDatabase()
                db.insert_card(
                    {
                        "date": "2026-03-08",
                        "title": "First Title",
                        "scene_description": "scene",
                        "environment": "Stone Monuments — weathered silence",
                        "energy_zone": "LOW",
                        "image_path": "/tmp/first.png",
                        "video_path": "/tmp/first.mp4",
                        "instagram_post_id": "ig_real_123",
                    }
                )

                conn = sqlite3.connect(db.db_path)
                conn.execute("UPDATE cards SET environment_name = NULL, environment_reason = NULL WHERE date = ?", ("2026-03-08",))
                conn.commit()
                conn.close()

                recent_names = db.get_recent_environment_names("LOW", "2026-03-09", limit=5)

        self.assertEqual(recent_names, ["Stone Monuments"])

    def test_mark_posted_after_publish_records_recovery_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"STATE_ZERO_PRIVATE_ROOT": tmpdir}, clear=False):
                manager = DailyRunStateManager(
                    run_date="2026-03-08",
                    timezone_name="Asia/Kolkata",
                    run_token="new-owner-token",
                )
                manager.state_path.parent.mkdir(parents=True, exist_ok=True)
                manager.state_path.write_text(
                    json.dumps(
                        {
                            "date": "2026-03-08",
                            "status": "STARTING",
                            "run_token": "old-owner-token",
                            "created_at": manager.now_iso(),
                        }
                    ),
                    encoding="utf-8",
                )

                manager.mark_posted_after_publish(
                    post_id="123",
                    permalink="https://instagram.example/p/123",
                    note="Instagram publish succeeded.",
                )

                state = json.loads(manager.state_path.read_text(encoding="utf-8"))

        self.assertEqual(state["status"], "POSTED")
        self.assertTrue(state["post_sync_recovered"])
        self.assertEqual(state["post_sync_recovered_from_run_token"], "old-owner-token")
        self.assertEqual(state["instagram_post_id"], "123")


if __name__ == "__main__":
    unittest.main()

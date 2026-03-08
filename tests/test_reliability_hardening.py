import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "src/scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from daily_run_state import DailyRunStateManager
from instagram_token_healthcheck import _format_expiry_window
from instagram_token_manager import InstagramTokenManager
from database_manager import CardDatabase


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
                           instagram_post_id, instagram_permalink
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
            ),
        )

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

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "src/scripts"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from database_manager import CardDatabase
from instagram_poster import InstagramPoster, InstagramPublishDiagnosticsError


class SafeRuntimeHardeningTests(unittest.TestCase):
    @staticmethod
    def _monotonic_side_effect(*values):
        values = list(values)
        last = values[-1]

        def _next():
            nonlocal values
            if values:
                return values.pop(0)
            return last

        return _next

    def test_instagram_requests_use_explicit_timeouts(self):
        poster = InstagramPoster.__new__(InstagramPoster)
        poster.user_id = "123"
        poster.access_token = "token"
        poster.base_url = "https://graph.facebook.com/v21.0"
        poster.mock_mode = False
        poster.REQUEST_TIMEOUT = (10, 60)
        poster.POLL_INTERVAL_SECONDS = 10
        poster.POLL_MAX_BACKOFF_SECONDS = 60

        ok_response = Mock()
        ok_response.json.return_value = {"id": "container-id"}
        ok_response.status_code = 200

        with patch("instagram_poster.requests.post", return_value=ok_response) as post_mock:
            creation_id = poster.create_media_container("https://example.com/video.mp4", "https://example.com/cover.png", "caption")

        self.assertEqual(creation_id, "container-id")
        self.assertEqual(post_mock.call_args.kwargs["timeout"], (10, 60))

        permalink_response = Mock()
        permalink_response.json.return_value = {"permalink": "https://instagram.com/p/example"}
        permalink_response.status_code = 200

        with patch("instagram_poster.requests.get", return_value=permalink_response) as get_mock:
            permalink = poster.get_permalink("post-id", max_retries=1)

        self.assertEqual(permalink, "https://instagram.com/p/example")
        self.assertEqual(get_mock.call_args.kwargs["timeout"], (10, 60))

    def test_create_media_container_failure_writes_diagnostics_artifact(self):
        poster = InstagramPoster.__new__(InstagramPoster)
        poster.user_id = "123"
        poster.access_token = "token"
        poster.base_url = "https://graph.facebook.com/v21.0"
        poster.mock_mode = False
        poster.REQUEST_TIMEOUT = (10, 60)
        poster.POLL_INTERVAL_SECONDS = 10
        poster.POLL_MAX_BACKOFF_SECONDS = 60

        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        poster.diagnostics_output_dir = Path(tmpdir.name) / "runtime" / "output" / "2026-04-09"
        poster.run_date = "2026-04-09"

        response = Mock()
        response.status_code = 400
        response.headers = {"debug-link": "https://debug.example/create"}
        response.json.return_value = {
            "error": {
                "message": "bad request",
                "code": 400,
            }
        }
        response.text = '{"error":{"message":"bad request","code":400}}'

        with patch("instagram_poster.requests.post", return_value=response):
            with self.assertRaises(InstagramPublishDiagnosticsError) as ctx:
                poster.create_media_container(
                    "https://example.com/video.mp4",
                    "https://example.com/cover.png",
                    "caption",
                )

        self.assertEqual(ctx.exception.phase, "create_container")
        self.assertIn("Failed to create container", str(ctx.exception))

        artifact_path = poster.diagnostics_output_dir / "instagram_publish_diagnostics.json"
        self.assertTrue(artifact_path.is_file())
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["phase"], "create_container")
        self.assertEqual(payload["context"]["video_url"], "https://example.com/video.mp4")
        self.assertEqual(payload["context"]["cover_url"], "https://example.com/cover.png")
        self.assertEqual(payload["response"]["http_status"], 400)
        self.assertEqual(payload["response"]["headers"]["debug-link"], "https://debug.example/create")
        self.assertEqual(payload["create_container_response"]["http_status"], 400)

    def test_publish_context_is_included_in_diagnostics(self):
        poster = InstagramPoster.__new__(InstagramPoster)
        poster.user_id = "123"
        poster.access_token = "token"
        poster.base_url = "https://graph.facebook.com/v21.0"
        poster.mock_mode = False
        poster.REQUEST_TIMEOUT = (10, 60)
        poster.POLL_INTERVAL_SECONDS = 10
        poster.POLL_MAX_BACKOFF_SECONDS = 60
        poster.publish_context = {
            "asset_source": "auto_api",
            "public_url_checks": [{"label": "video", "reachable": True}],
        }

        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        poster.diagnostics_output_dir = Path(tmpdir.name) / "runtime" / "output" / "2026-04-09"
        poster.run_date = "2026-04-09"

        response = Mock()
        response.status_code = 400
        response.headers = {"debug-link": "https://debug.example/create"}
        response.json.return_value = {
            "error": {
                "message": "bad request",
                "code": 400,
            }
        }

        with patch("instagram_poster.requests.post", return_value=response):
            with self.assertRaises(InstagramPublishDiagnosticsError):
                poster.create_media_container(
                    "https://example.com/video.mp4",
                    "https://example.com/cover.png",
                    "caption",
                )

        artifact_path = poster.diagnostics_output_dir / "instagram_publish_diagnostics.json"
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["publish_context"]["asset_source"], "auto_api")
        self.assertEqual(payload["publish_context"]["public_url_checks"][0]["label"], "video")

    def test_poll_processing_status_retries_transient_network_then_succeeds(self):
        poster = InstagramPoster.__new__(InstagramPoster)
        poster.user_id = "123"
        poster.access_token = "token"
        poster.base_url = "https://graph.facebook.com/v21.0"
        poster.mock_mode = False
        poster.REQUEST_TIMEOUT = (10, 60)
        poster.POLL_INTERVAL_SECONDS = 10
        poster.POLL_MAX_BACKOFF_SECONDS = 60

        finished = Mock()
        finished.status_code = 200
        finished.json.return_value = {"status_code": "FINISHED"}
        finished.headers = {}

        with patch(
            "instagram_poster.requests.get",
            side_effect=[
                requests.exceptions.Timeout("timeout one"),
                requests.exceptions.Timeout("timeout two"),
                finished,
            ],
        ) as get_mock:
            with patch("instagram_poster.time.sleep", return_value=None):
                with patch(
                    "instagram_poster.time.monotonic",
                    side_effect=self._monotonic_side_effect(0, 0, 10, 10, 20, 20, 30),
                ):
                    ok = poster.poll_processing_status("creation-id", max_polls=10)

        self.assertTrue(ok)
        self.assertEqual(get_mock.call_count, 3)

    def test_poll_processing_status_allows_longer_transient_streak_before_success(self):
        poster = InstagramPoster.__new__(InstagramPoster)
        poster.user_id = "123"
        poster.access_token = "token"
        poster.base_url = "https://graph.facebook.com/v21.0"
        poster.mock_mode = False
        poster.REQUEST_TIMEOUT = (10, 60)
        poster.POLL_INTERVAL_SECONDS = 10
        poster.POLL_MAX_BACKOFF_SECONDS = 60

        finished = Mock()
        finished.status_code = 200
        finished.json.return_value = {"status_code": "FINISHED"}
        finished.headers = {}

        with patch(
            "instagram_poster.requests.get",
            side_effect=[
                requests.exceptions.Timeout("timeout one"),
                requests.exceptions.Timeout("timeout two"),
                requests.exceptions.Timeout("timeout three"),
                requests.exceptions.Timeout("timeout four"),
                finished,
            ],
        ) as get_mock:
            with patch("instagram_poster.time.sleep", return_value=None):
                with patch(
                    "instagram_poster.time.monotonic",
                    side_effect=self._monotonic_side_effect(0, 0, 10, 10, 20, 20, 30, 30, 40, 40, 50),
                ):
                    ok = poster.poll_processing_status("creation-id", max_polls=10)

        self.assertTrue(ok)
        self.assertEqual(get_mock.call_count, 5)

    def test_poll_processing_status_terminal_error_writes_diagnostics_artifact(self):
        poster = InstagramPoster.__new__(InstagramPoster)
        poster.user_id = "123"
        poster.access_token = "token"
        poster.base_url = "https://graph.facebook.com/v21.0"
        poster.mock_mode = False
        poster.REQUEST_TIMEOUT = (10, 60)
        poster.POLL_INTERVAL_SECONDS = 10
        poster.POLL_MAX_BACKOFF_SECONDS = 60

        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        poster.diagnostics_output_dir = Path(tmpdir.name) / "runtime" / "output" / "2026-04-09"
        poster.run_date = "2026-04-09"

        error_response = Mock()
        error_response.status_code = 200
        error_response.headers = {
            "debug-link": "https://debug.example/poll",
            "x-fb-request-id": "req-123",
        }
        error_response.json.return_value = {
            "status_code": "ERROR",
            "error": {
                "code": 2207076,
                "message": "media processing failed",
            },
        }

        with patch("instagram_poster.requests.get", return_value=error_response):
            with self.assertRaises(InstagramPublishDiagnosticsError) as ctx:
                poster.poll_processing_status("creation-id", max_polls=1)

        self.assertEqual(ctx.exception.phase, "poll_processing")
        self.assertIn("terminal status_code=ERROR", str(ctx.exception))

        artifact_path = poster.diagnostics_output_dir / "instagram_publish_diagnostics.json"
        self.assertTrue(artifact_path.is_file())
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["phase"], "poll_processing")
        self.assertEqual(payload["creation_id"], "creation-id")
        self.assertEqual(payload["terminal_status_code"], "ERROR")
        self.assertEqual(payload["error_object"]["code"], 2207076)
        self.assertEqual(payload["response"]["headers"]["debug-link"], "https://debug.example/poll")

    def test_publish_media_terminal_error_writes_diagnostics_artifact(self):
        poster = InstagramPoster.__new__(InstagramPoster)
        poster.user_id = "123"
        poster.access_token = "token"
        poster.base_url = "https://graph.facebook.com/v21.0"
        poster.mock_mode = False
        poster.REQUEST_TIMEOUT = (10, 60)
        poster.POLL_INTERVAL_SECONDS = 10
        poster.POLL_MAX_BACKOFF_SECONDS = 60

        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        poster.diagnostics_output_dir = Path(tmpdir.name) / "runtime" / "output" / "2026-04-09"
        poster.run_date = "2026-04-09"

        response = Mock()
        response.status_code = 400
        response.headers = {
            "debug-link": "https://debug.example/publish",
            "x-fb-request-id": "req-publish",
        }
        response.json.return_value = {
            "error": {
                "message": "publish failed",
                "code": 190,
            }
        }

        with patch("instagram_poster.requests.post", return_value=response):
            with patch("instagram_poster.time.sleep", return_value=None):
                with self.assertRaises(InstagramPublishDiagnosticsError) as ctx:
                    poster.publish_media("creation-id")

        self.assertEqual(ctx.exception.phase, "publish_media")
        artifact_path = poster.diagnostics_output_dir / "instagram_publish_diagnostics.json"
        self.assertTrue(artifact_path.is_file())
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["phase"], "publish_media")
        self.assertEqual(payload["creation_id"], "creation-id")
        self.assertEqual(payload["publish_response"]["http_status"], 400)
        self.assertEqual(payload["response"]["headers"]["debug-link"], "https://debug.example/publish")

    def test_publish_media_non_json_writes_diagnostics_artifact(self):
        poster = InstagramPoster.__new__(InstagramPoster)
        poster.user_id = "123"
        poster.access_token = "token"
        poster.base_url = "https://graph.facebook.com/v21.0"
        poster.mock_mode = False
        poster.REQUEST_TIMEOUT = (10, 60)
        poster.POLL_INTERVAL_SECONDS = 10
        poster.POLL_MAX_BACKOFF_SECONDS = 60

        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        poster.diagnostics_output_dir = Path(tmpdir.name) / "runtime" / "output" / "2026-04-09"
        poster.run_date = "2026-04-09"

        response = Mock()
        response.status_code = 502
        response.headers = {"debug-link": "https://debug.example/publish-non-json"}
        response.json.side_effect = ValueError("not json")
        response.text = "<html>bad gateway</html>"

        with patch("instagram_poster.requests.post", return_value=response):
            with patch("instagram_poster.time.sleep", return_value=None):
                with self.assertRaises(InstagramPublishDiagnosticsError) as ctx:
                    poster.publish_media("creation-id")

        self.assertEqual(ctx.exception.phase, "publish_media")
        artifact_path = poster.diagnostics_output_dir / "instagram_publish_diagnostics.json"
        self.assertTrue(artifact_path.is_file())
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["phase"], "publish_media")
        self.assertEqual(payload["creation_id"], "creation-id")
        self.assertEqual(payload["response"]["http_status"], 502)
        self.assertIn("not json", payload["parse_error"])

    def test_poll_processing_status_fails_after_time_budget_expires(self):
        poster = InstagramPoster.__new__(InstagramPoster)
        poster.user_id = "123"
        poster.access_token = "token"
        poster.base_url = "https://graph.facebook.com/v21.0"
        poster.mock_mode = False
        poster.REQUEST_TIMEOUT = (10, 60)
        poster.POLL_INTERVAL_SECONDS = 10
        poster.POLL_MAX_BACKOFF_SECONDS = 60

        with patch(
            "instagram_poster.requests.get",
            side_effect=requests.exceptions.Timeout("always timing out"),
        ) as get_mock:
            with patch("instagram_poster.time.sleep", return_value=None):
                with patch(
                    "instagram_poster.time.monotonic",
                    side_effect=self._monotonic_side_effect(0, 0, 10, 10, 20, 20, 30),
                ):
                    ok = poster.poll_processing_status("creation-id", max_polls=3)

        self.assertFalse(ok)
        self.assertEqual(get_mock.call_count, 3)

    def test_poll_processing_status_retries_transient_api_then_succeeds(self):
        poster = InstagramPoster.__new__(InstagramPoster)
        poster.user_id = "123"
        poster.access_token = "token"
        poster.base_url = "https://graph.facebook.com/v21.0"
        poster.mock_mode = False
        poster.REQUEST_TIMEOUT = (10, 60)
        poster.POLL_INTERVAL_SECONDS = 10
        poster.POLL_MAX_BACKOFF_SECONDS = 60

        transient = Mock()
        transient.status_code = 503
        transient.json.return_value = {"error": {"message": "temporary outage"}}
        transient.headers = {}

        finished = Mock()
        finished.status_code = 200
        finished.json.return_value = {"status_code": "FINISHED"}
        finished.headers = {}

        with patch(
            "instagram_poster.requests.get",
            side_effect=[transient, finished],
        ) as get_mock:
            with patch("instagram_poster.time.sleep", return_value=None):
                with patch(
                    "instagram_poster.time.monotonic",
                    side_effect=self._monotonic_side_effect(0, 0, 10, 10, 20),
                ):
                    ok = poster.poll_processing_status("creation-id", max_polls=10)

        self.assertTrue(ok)
        self.assertEqual(get_mock.call_count, 2)

    def test_poll_processing_status_honors_retry_after_header(self):
        poster = InstagramPoster.__new__(InstagramPoster)
        poster.user_id = "123"
        poster.access_token = "token"
        poster.base_url = "https://graph.facebook.com/v21.0"
        poster.mock_mode = False
        poster.REQUEST_TIMEOUT = (10, 60)
        poster.POLL_INTERVAL_SECONDS = 10
        poster.POLL_MAX_BACKOFF_SECONDS = 60

        transient = Mock()
        transient.status_code = 429
        transient.json.return_value = {"error": {"message": "rate limited"}}
        transient.headers = {"Retry-After": "25"}

        finished = Mock()
        finished.status_code = 200
        finished.json.return_value = {"status_code": "FINISHED"}
        finished.headers = {}

        with patch(
            "instagram_poster.requests.get",
            side_effect=[transient, finished],
        ):
            with patch("instagram_poster.time.sleep", return_value=None) as sleep_mock:
                with patch(
                    "instagram_poster.time.monotonic",
                    side_effect=self._monotonic_side_effect(0, 0, 25, 25, 50),
                ):
                    ok = poster.poll_processing_status("creation-id", max_polls=10)

        self.assertTrue(ok)
        self.assertEqual(sleep_mock.call_args_list[0].args[0], 25)

    def test_poll_processing_status_respects_max_polls_under_short_retry_after(self):
        poster = InstagramPoster.__new__(InstagramPoster)
        poster.user_id = "123"
        poster.access_token = "token"
        poster.base_url = "https://graph.facebook.com/v21.0"
        poster.mock_mode = False
        poster.REQUEST_TIMEOUT = (10, 60)
        poster.POLL_INTERVAL_SECONDS = 10
        poster.POLL_MAX_BACKOFF_SECONDS = 60

        transient = Mock()
        transient.status_code = 429
        transient.json.return_value = {"error": {"message": "rate limited"}}
        transient.headers = {"Retry-After": "1"}

        with patch(
            "instagram_poster.requests.get",
            side_effect=[transient, transient, transient, transient, transient],
        ) as get_mock:
            with patch("instagram_poster.time.sleep", return_value=None):
                with patch(
                    "instagram_poster.time.monotonic",
                    side_effect=self._monotonic_side_effect(0, 0, 1, 1, 2, 2, 3, 3, 4),
                ):
                    ok = poster.poll_processing_status("creation-id", max_polls=3)

        self.assertFalse(ok)
        self.assertEqual(get_mock.call_count, 3)

    def test_database_cli_returns_non_zero_for_missing_payload(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_ROOT / "database_manager.py"),
                "--insert",
                "--file",
                str(PROJECT_ROOT / "does-not-exist.json"),
            ],
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Could not find", result.stdout)

    def test_database_cli_inserts_payload_successfully(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payload_path = Path(tmpdir) / "payload.json"
            payload_path.write_text(
                json.dumps(
                    {
                        "date": "2026-03-28",
                        "title": "Test Title",
                        "scene_description": "Test scene",
                        "environment": "Frozen/Ice — Test",
                        "environment_name": "Frozen/Ice",
                        "environment_reason": "Test reason",
                        "creature": "Falcon — Test",
                        "blend_option": "Option A",
                        "energy_zone": "HIGH",
                        "recovery_pct": 85,
                        "sleep_score_pct": 82,
                        "strain": 12.5,
                        "sleep_hours": 7.2,
                        "depth_level": "SURFACE",
                        "dasha_maha": "Mars",
                        "dasha_antar": "Moon",
                        "dasha_pratyantar": "Sun",
                        "dasha_sookshma": "Mercury",
                        "dasha_prana": "Venus",
                        "image_path": "/tmp/image.png",
                        "video_path": "/tmp/video.mp4",
                        "image_prompt_json": "{}",
                        "instagram_post_id": "123",
                        "instagram_permalink": "https://instagram.com/p/example",
                    }
                ),
                encoding="utf-8",
            )

            env = os.environ.copy()
            env["STATE_ZERO_PRIVATE_ROOT"] = tmpdir
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_ROOT / "database_manager.py"),
                    "--insert",
                    "--file",
                    str(payload_path),
                ],
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

            with patch.dict(os.environ, {"STATE_ZERO_PRIVATE_ROOT": tmpdir}, clear=False):
                db = CardDatabase()
                self.assertTrue(db.has_card_for_date("2026-03-28"))


if __name__ == "__main__":
    unittest.main()

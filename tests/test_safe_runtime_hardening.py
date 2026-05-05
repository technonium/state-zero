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
from instagram_poster import InstagramPoster, InstagramPublishDiagnosticsError, InstagramPublishResult


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

    def test_resumable_publish_uploads_local_video_bytes_and_returns_permalink(self):
        poster = InstagramPoster.__new__(InstagramPoster)
        poster.user_id = "123"
        poster.access_token = "token"
        poster.base_url = "https://graph.facebook.com/v25.0"
        poster.graph_api_version = "v25.0"
        poster.mock_mode = False
        poster.REQUEST_TIMEOUT = (10, 60)
        poster.POLL_INTERVAL_SECONDS = 10
        poster.POLL_MAX_BACKOFF_SECONDS = 60
        poster.publish_context = None

        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        poster.diagnostics_output_dir = Path(tmpdir.name) / "runtime" / "output" / "2026-04-28"
        poster.run_date = "2026-04-28"
        video_path = Path(tmpdir.name) / "card_final.mp4"
        video_path.write_bytes(b"fake mp4 bytes")

        create_response = Mock()
        create_response.status_code = 200
        create_response.headers = {"x-fb-request-id": "req-create"}
        create_response.json.return_value = {
            "id": "creation-id",
            "uri": "https://rupload.facebook.com/ig-api-upload/v25.0/creation-id",
        }

        upload_response = Mock()
        upload_response.status_code = 200
        upload_response.headers = {"x-fb-request-id": "req-upload"}
        upload_response.json.return_value = {"success": True, "message": "Upload Successful."}

        publish_response = Mock()
        publish_response.status_code = 200
        publish_response.headers = {"x-fb-request-id": "req-publish"}
        publish_response.json.return_value = {"id": "post-id"}

        poll_response = Mock()
        poll_response.status_code = 200
        poll_response.headers = {}
        poll_response.json.return_value = {"status_code": "FINISHED"}

        permalink_response = Mock()
        permalink_response.status_code = 200
        permalink_response.headers = {}
        permalink_response.json.return_value = {"permalink": "https://instagram.example/reel/post-id/"}

        with patch.object(poster, "validate_local_video_for_reels", return_value={"path": str(video_path)}):
            with patch.object(poster, "check_content_publishing_limit", return_value={"data": []}):
                with patch(
                    "instagram_poster.requests.post",
                    side_effect=[create_response, upload_response, publish_response],
                ) as post_mock:
                    with patch("instagram_poster.requests.get", side_effect=[poll_response, permalink_response]):
                        result = poster.publish_resumable_binary_and_get_result(video_path, "caption")

        self.assertEqual(result.post_id, "post-id")
        self.assertEqual(result.permalink, "https://instagram.example/reel/post-id/")
        create_payload = post_mock.call_args_list[0].kwargs["data"]
        self.assertEqual(create_payload["upload_type"], "resumable")
        self.assertEqual(create_payload["media_type"], "REELS")
        self.assertNotIn("cover_url", create_payload)
        self.assertEqual(create_payload["thumb_offset"], "1000")
        upload_headers = post_mock.call_args_list[1].kwargs["headers"]
        self.assertEqual(upload_headers["Authorization"], "OAuth token")
        self.assertEqual(upload_headers["offset"], "0")
        self.assertEqual(upload_headers["file_size"], str(len(b"fake mp4 bytes")))
        self.assertEqual(upload_headers["Content-Type"], "video/mp4")

        diagnostics = json.loads((poster.diagnostics_output_dir / "instagram_publish_diagnostics.json").read_text(encoding="utf-8"))
        self.assertFalse(diagnostics["cover_url_supplied"])

    def test_resumable_publish_with_cover_url_sends_cover_url(self):
        poster = InstagramPoster.__new__(InstagramPoster)
        poster.user_id = "123"
        poster.access_token = "token"
        poster.base_url = "https://graph.facebook.com/v25.0"
        poster.graph_api_version = "v25.0"
        poster.mock_mode = False
        poster.REQUEST_TIMEOUT = (10, 60)
        poster.POLL_INTERVAL_SECONDS = 10
        poster.POLL_MAX_BACKOFF_SECONDS = 60
        poster.publish_context = None

        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        poster.diagnostics_output_dir = Path(tmpdir.name) / "runtime" / "output" / "2026-04-28"
        poster.run_date = "2026-04-28"
        video_path = Path(tmpdir.name) / "card_final.mp4"
        video_path.write_bytes(b"fake mp4 bytes")

        create_response = Mock()
        create_response.status_code = 200
        create_response.headers = {"x-fb-request-id": "req-create"}
        create_response.json.return_value = {
            "id": "creation-id",
            "uri": "https://rupload.facebook.com/ig-api-upload/v25.0/creation-id",
        }

        upload_response = Mock()
        upload_response.status_code = 200
        upload_response.headers = {"x-fb-request-id": "req-upload"}
        upload_response.json.return_value = {"success": True, "message": "Upload Successful."}

        publish_response = Mock()
        publish_response.status_code = 200
        publish_response.headers = {"x-fb-request-id": "req-publish"}
        publish_response.json.return_value = {"id": "post-id"}

        poll_response = Mock()
        poll_response.status_code = 200
        poll_response.headers = {}
        poll_response.json.return_value = {"status_code": "FINISHED"}

        permalink_response = Mock()
        permalink_response.status_code = 200
        permalink_response.headers = {}
        permalink_response.json.return_value = {"permalink": "https://instagram.example/reel/post-id/"}

        with patch.object(poster, "validate_local_video_for_reels", return_value={"path": str(video_path)}):
            with patch.object(poster, "check_content_publishing_limit", return_value={"data": []}):
                with patch(
                    "instagram_poster.requests.post",
                    side_effect=[create_response, upload_response, publish_response],
                ) as post_mock:
                    with patch("instagram_poster.requests.get", side_effect=[poll_response, permalink_response]):
                        result = poster.publish_resumable_binary_and_get_result(
                            video_path,
                            "caption",
                            cover_url="https://example.com/thumb.png",
                        )

        self.assertEqual(result.post_id, "post-id")
        self.assertEqual(result.permalink, "https://instagram.example/reel/post-id/")
        create_payload = post_mock.call_args_list[0].kwargs["data"]
        self.assertEqual(create_payload["cover_url"], "https://example.com/thumb.png")
        self.assertNotIn("thumb_offset", create_payload)
        diagnostics = json.loads((poster.diagnostics_output_dir / "instagram_publish_diagnostics.json").read_text(encoding="utf-8"))
        self.assertTrue(diagnostics["cover_url_supplied"])

    def test_resumable_upload_failure_writes_diagnostics_without_posting(self):
        poster = InstagramPoster.__new__(InstagramPoster)
        poster.user_id = "123"
        poster.access_token = "token"
        poster.base_url = "https://graph.facebook.com/v25.0"
        poster.graph_api_version = "v25.0"
        poster.mock_mode = False
        poster.REQUEST_TIMEOUT = (10, 60)
        poster.POLL_INTERVAL_SECONDS = 10
        poster.POLL_MAX_BACKOFF_SECONDS = 60
        poster.publish_context = None

        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        poster.diagnostics_output_dir = Path(tmpdir.name) / "runtime" / "output" / "2026-04-28"
        poster.run_date = "2026-04-28"
        video_path = Path(tmpdir.name) / "card_final.mp4"
        video_path.write_bytes(b"fake mp4 bytes")

        response = Mock()
        response.status_code = 500
        response.headers = {"x-fb-request-id": "req-upload"}
        response.json.return_value = {"error": {"message": "ProcessingFailedError"}}

        with patch("instagram_poster.requests.post", return_value=response):
            with self.assertRaises(InstagramPublishDiagnosticsError) as ctx:
                poster.upload_resumable_video("https://rupload.facebook.com/ig-api-upload/v25.0/creation-id", video_path)

        self.assertEqual(ctx.exception.phase, "upload_resumable_video")
        artifact_path = poster.diagnostics_output_dir / "instagram_publish_diagnostics.json"
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["phase"], "upload_resumable_video")
        self.assertEqual(payload["publish_strategy"], "resumable_binary")

    def test_resumable_preflight_allows_silent_video_with_warning(self):
        poster = InstagramPoster.__new__(InstagramPoster)
        video_path = Path(tempfile.gettempdir()) / "card_final.mp4"
        snapshot = {
            "path": str(video_path),
            "exists": True,
            "ffprobe": {
                "data": {
                    "streams": [
                        {
                            "codec_type": "video",
                            "codec_name": "h264",
                            "pix_fmt": "yuv420p",
                            "width": 1080,
                            "height": 1920,
                        }
                    ],
                    "format": {"duration": "8.0"},
                }
            },
        }

        with patch.object(poster, "_local_video_snapshot", return_value=snapshot):
            result = poster.validate_local_video_for_reels(video_path)

        self.assertEqual(result["preflight_warnings"], ["missing audio stream"])

    def test_auto_strategy_binary_success_does_not_require_public_urls(self):
        poster = InstagramPoster.__new__(InstagramPoster)
        video_path = Path(tempfile.gettempdir()) / "card_final.mp4"

        with patch.object(
            poster,
            "publish_resumable_binary_and_get_result",
            return_value=InstagramPublishResult("post-id", "https://instagram.example/reel/post-id/"),
        ) as binary_mock:
            with patch.object(poster, "publish_and_get_result") as url_mock:
                result = poster.publish_with_strategy(
                    video_url=None,
                    cover_url=None,
                    caption="caption",
                    local_video_path=video_path,
                    strategy="auto",
                )

        self.assertEqual(result.post_id, "post-id")
        binary_mock.assert_called_once_with(video_path, "caption", cover_url=None)
        url_mock.assert_not_called()

    def test_resumable_strategy_forwards_cover_url_to_binary_publish(self):
        poster = InstagramPoster.__new__(InstagramPoster)
        video_path = Path(tempfile.gettempdir()) / "card_final.mp4"

        with patch.object(
            poster,
            "publish_resumable_binary_and_get_result",
            return_value=InstagramPublishResult("post-id", "https://instagram.example/reel/post-id/"),
        ) as binary_mock:
            result = poster.publish_with_strategy(
                video_url="https://example.com/video.mp4",
                cover_url="https://example.com/thumb.png",
                caption="caption",
                local_video_path=video_path,
                strategy="resumable_binary",
            )

        self.assertEqual(result.post_id, "post-id")
        binary_mock.assert_called_once_with(
            video_path,
            "caption",
            cover_url="https://example.com/thumb.png",
        )

    def test_auto_strategy_binary_failure_without_urls_does_not_fall_back_to_video_url(self):
        poster = InstagramPoster.__new__(InstagramPoster)
        poster.user_id = "123"
        poster.diagnostics_output_dir = None
        poster.run_date = None
        video_path = Path(tempfile.gettempdir()) / "card_final.mp4"
        error = InstagramPublishDiagnosticsError(
            "upload_resumable_video",
            "binary failed",
            {"phase": "upload_resumable_video"},
        )

        with patch.object(poster, "publish_resumable_binary_and_get_result", side_effect=error):
            with patch.object(poster, "publish_and_get_result") as url_mock:
                with self.assertRaises(InstagramPublishDiagnosticsError) as ctx:
                    poster.publish_with_strategy(
                        video_url=None,
                        cover_url=None,
                        caption="caption",
                        local_video_path=video_path,
                        strategy="auto",
                    )

        self.assertIs(ctx.exception, error)
        url_mock.assert_not_called()

    def test_auto_strategy_binary_failure_falls_back_only_when_urls_exist(self):
        poster = InstagramPoster.__new__(InstagramPoster)
        poster.user_id = "123"
        poster.diagnostics_output_dir = None
        poster.run_date = None
        video_path = Path(tempfile.gettempdir()) / "card_final.mp4"
        error = InstagramPublishDiagnosticsError(
            "upload_resumable_video",
            "binary failed",
            {"phase": "upload_resumable_video"},
        )

        with patch.object(poster, "publish_resumable_binary_and_get_result", side_effect=error):
            with patch.object(
                poster,
                "publish_and_get_result",
                return_value=InstagramPublishResult("post-id", "https://instagram.example/reel/post-id/"),
            ) as url_mock:
                result = poster.publish_with_strategy(
                    video_url="https://example.com/video.mp4",
                    cover_url="https://example.com/thumb.png",
                    caption="caption",
                    local_video_path=video_path,
                    strategy="auto",
                )

        self.assertEqual(result.post_id, "post-id")
        url_mock.assert_called_once_with("https://example.com/video.mp4", "https://example.com/thumb.png", "caption")

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

    def test_diagnostics_history_preserves_multiple_publish_failures(self):
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

        def make_response(creation_id):
            response = Mock()
            response.status_code = 200
            response.headers = {"x-fb-request-id": f"req-{creation_id}"}
            response.json.return_value = {
                "status_code": "ERROR",
                "status": "Error: Media upload has failed with error code 2207076",
                "id": creation_id,
            }
            return response

        with patch("instagram_poster.requests.get", side_effect=[make_response("creation-1"), make_response("creation-2")]):
            with self.assertRaises(InstagramPublishDiagnosticsError):
                poster.poll_processing_status("creation-1", max_polls=1)
            with self.assertRaises(InstagramPublishDiagnosticsError):
                poster.poll_processing_status("creation-2", max_polls=1)

        history_path = poster.diagnostics_output_dir / "instagram_publish_diagnostics_history.json"
        self.assertTrue(history_path.is_file())
        history = json.loads(history_path.read_text(encoding="utf-8"))
        self.assertEqual(len(history["events"]), 2)
        self.assertEqual(history["events"][0]["creation_id"], "creation-1")
        self.assertEqual(history["events"][1]["creation_id"], "creation-2")

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
        self.assertEqual(payload["failure_classification"], "instagram_media_ingest_failure")
        self.assertEqual(payload["response"]["headers"]["debug-link"], "https://debug.example/poll")

    def test_poll_processing_status_continues_after_soft_2207076_error(self):
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

        soft_error = Mock()
        soft_error.status_code = 200
        soft_error.headers = {"x-fb-request-id": "req-soft"}
        soft_error.json.return_value = {
            "status_code": "ERROR",
            "status": "Error: Media upload has failed with error code 2207076",
            "id": "creation-id",
        }

        in_progress = Mock()
        in_progress.status_code = 200
        in_progress.headers = {}
        in_progress.json.return_value = {"status_code": "IN_PROGRESS"}

        finished = Mock()
        finished.status_code = 200
        finished.headers = {}
        finished.json.return_value = {"status_code": "FINISHED"}

        with patch("instagram_poster.requests.get", side_effect=[soft_error, in_progress, finished]) as get_mock:
            with patch("instagram_poster.time.sleep", return_value=None):
                ok = poster.poll_processing_status("creation-id", max_polls=5)

        self.assertTrue(ok)
        self.assertEqual(get_mock.call_count, 3)
        history_path = poster.diagnostics_output_dir / "instagram_publish_diagnostics_history.json"
        history = json.loads(history_path.read_text(encoding="utf-8"))
        self.assertEqual(history["events"][0]["phase"], "poll_processing_soft_error")
        self.assertEqual(history["events"][0]["soft_processing_error_count"], 1)

    def test_poll_processing_status_fails_after_soft_2207076_limit(self):
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

        def make_soft_error():
            response = Mock()
            response.status_code = 200
            response.headers = {"x-fb-request-id": "req-soft"}
            response.json.return_value = {
                "status_code": "ERROR",
                "status": "Error: Media upload has failed with error code 2207076",
                "id": "creation-id",
            }
            return response

        with patch.dict(os.environ, {"INSTAGRAM_SOFT_PROCESSING_ERROR_POLLS": "2"}, clear=False):
            with patch("instagram_poster.requests.get", side_effect=[make_soft_error(), make_soft_error(), make_soft_error()]):
                with patch("instagram_poster.time.sleep", return_value=None):
                    with self.assertRaises(InstagramPublishDiagnosticsError) as ctx:
                        poster.poll_processing_status("creation-id", max_polls=5)

        self.assertEqual(ctx.exception.phase, "poll_processing")
        self.assertEqual(ctx.exception.diagnostics["soft_processing_error_count"], 2)
        self.assertEqual(ctx.exception.diagnostics["soft_processing_error_poll_limit"], 2)

        history_path = poster.diagnostics_output_dir / "instagram_publish_diagnostics_history.json"
        history = json.loads(history_path.read_text(encoding="utf-8"))
        self.assertEqual(len(history["events"]), 3)
        self.assertEqual(history["events"][0]["phase"], "poll_processing_soft_error")
        self.assertEqual(history["events"][1]["phase"], "poll_processing_soft_error")
        self.assertEqual(history["events"][2]["phase"], "poll_processing")

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

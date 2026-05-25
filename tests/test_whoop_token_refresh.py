import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "src/scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from whoop_token_manager import WHOOPTokenManager, WhoopReauthRequired


class _FakeResponse:
    def __init__(self, status_code: int, body: str = "", json_payload: dict | None = None):
        self.status_code = status_code
        self.text = body
        self._json_payload = json_payload or {}

    def json(self):
        return self._json_payload


class _FakeHTTPClient:
    """Replaces httpx.AsyncClient. Returns scripted responses or raises per attempt."""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append({"url": url, "data": dict(data or {})})
        if not self._scripted:
            raise AssertionError("No more scripted responses for fake http client")
        item = self._scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class WHOOPTokenRefreshTests(unittest.IsolatedAsyncioTestCase):
    def _build_manager(self, tmpdir: str, **env_overrides) -> WHOOPTokenManager:
        env = {
            "STATE_ZERO_PRIVATE_ROOT": tmpdir,
            "WHOOP_CLIENT_ID": "client-id",
            "WHOOP_CLIENT_SECRET": "client-secret",
            "WHOOP_ACCESS_TOKEN": "stale-access",
            "WHOOP_REFRESH_TOKEN": "good-refresh",
        }
        env.update(env_overrides)
        WHOOPTokenManager._instance = None
        with patch.dict(os.environ, env, clear=False):
            manager = WHOOPTokenManager()
        return manager

    def _patch_http(self, manager, scripted):
        fake = _FakeHTTPClient(scripted)
        manager._notify_refresh_failure = lambda message: setattr(manager, "_last_warning", message)
        manager._notify_reauth_required = lambda message: setattr(manager, "_last_reauth", message)
        return fake

    async def test_whitespace_padded_credentials_are_stripped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = self._build_manager(
                tmpdir,
                WHOOP_CLIENT_ID="  client-id\n",
                WHOOP_CLIENT_SECRET="\tclient-secret  ",
            )
            self.assertEqual(manager.client_id, "client-id")
            self.assertEqual(manager.client_secret, "client-secret")

    async def test_invalid_grant_raises_reauth_required_without_retry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = self._build_manager(tmpdir)
            fake = self._patch_http(
                manager,
                [_FakeResponse(400, body='{"error":"invalid_grant"}')],
            )
            with patch("whoop_token_manager.httpx.AsyncClient", return_value=fake):
                with self.assertRaises(WhoopReauthRequired):
                    await manager._refresh_token()
            self.assertEqual(len(fake.calls), 1)
            self.assertTrue(getattr(manager, "_last_reauth", "").startswith("WHOOP refresh token was rejected"))

    async def test_non_reauth_4xx_fails_immediately_without_retry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = self._build_manager(tmpdir)
            fake = self._patch_http(
                manager,
                [_FakeResponse(400, body='{"error":"invalid_request"}')],
            )
            with patch("whoop_token_manager.httpx.AsyncClient", return_value=fake):
                result = await manager._refresh_token()
            self.assertFalse(result)
            self.assertEqual(len(fake.calls), 1)
            self.assertIn("invalid_request", getattr(manager, "_last_warning", ""))

    async def test_5xx_retries_then_succeeds_without_warning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = self._build_manager(tmpdir)
            fake = self._patch_http(
                manager,
                [
                    _FakeResponse(502, body="bad gateway"),
                    _FakeResponse(
                        200,
                        json_payload={
                            "access_token": "fresh-access",
                            "refresh_token": "fresh-refresh",
                            "expires_in": 3600,
                        },
                    ),
                ],
            )
            with patch("whoop_token_manager.httpx.AsyncClient", return_value=fake):
                with patch("whoop_token_manager.asyncio.sleep", new=_noop_async_sleep):
                    result = await manager._refresh_token()
            self.assertTrue(result)
            self.assertEqual(len(fake.calls), 2)
            self.assertEqual(manager.access_token, "fresh-access")
            self.assertEqual(manager.refresh_token, "fresh-refresh")
            self.assertFalse(hasattr(manager, "_last_warning"))

    async def test_network_exception_retries_then_succeeds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = self._build_manager(tmpdir)
            fake = self._patch_http(
                manager,
                [
                    ConnectionError("dns failure"),
                    _FakeResponse(
                        200,
                        json_payload={
                            "access_token": "fresh-access",
                            "refresh_token": "fresh-refresh",
                            "expires_in": 3600,
                        },
                    ),
                ],
            )
            with patch("whoop_token_manager.httpx.AsyncClient", return_value=fake):
                with patch("whoop_token_manager.asyncio.sleep", new=_noop_async_sleep):
                    result = await manager._refresh_token()
            self.assertTrue(result)
            self.assertEqual(len(fake.calls), 2)
            self.assertEqual(manager.access_token, "fresh-access")

    async def test_two_consecutive_5xx_warns_and_returns_false(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = self._build_manager(tmpdir)
            fake = self._patch_http(
                manager,
                [
                    _FakeResponse(503, body="upstream down"),
                    _FakeResponse(502, body="bad gateway"),
                ],
            )
            with patch("whoop_token_manager.httpx.AsyncClient", return_value=fake):
                with patch("whoop_token_manager.asyncio.sleep", new=_noop_async_sleep):
                    result = await manager._refresh_token()
            self.assertFalse(result)
            self.assertEqual(len(fake.calls), 2)
            self.assertIn("WHOOP token refresh failed: 502", getattr(manager, "_last_warning", ""))

    async def test_successful_first_attempt_does_not_call_sleep(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = self._build_manager(tmpdir)
            fake = self._patch_http(
                manager,
                [
                    _FakeResponse(
                        200,
                        json_payload={
                            "access_token": "fresh-access",
                            "refresh_token": "fresh-refresh",
                            "expires_in": 3600,
                        },
                    ),
                ],
            )
            sleep_calls = []

            async def tracking_sleep(seconds):
                sleep_calls.append(seconds)

            with patch("whoop_token_manager.httpx.AsyncClient", return_value=fake):
                with patch("whoop_token_manager.asyncio.sleep", new=tracking_sleep):
                    result = await manager._refresh_token()
            self.assertTrue(result)
            self.assertEqual(len(fake.calls), 1)
            self.assertEqual(sleep_calls, [])
            self.assertGreater(manager.token_expires_at, datetime.now() + timedelta(minutes=50))


async def _noop_async_sleep(_seconds):
    return None


if __name__ == "__main__":
    unittest.main()

import json
import os
import logging
import threading
import tempfile
from datetime import datetime, timedelta
import httpx
from utils import get_state_root, get_pipeline_run_date_str

logger = logging.getLogger(__name__)

# WHOOP tokens expire in 1 hour (3600 seconds per official docs)
# Refresh 5 minutes (300 seconds) before expiry
REFRESH_THRESHOLD_SECONDS = 300


class WhoopReauthRequired(Exception):
    """Raised when OAuth refresh can no longer proceed and user re-auth is required."""
    pass

class WHOOPTokenManager:
    _instance = None
    _lock = threading.Lock()
    _refresh_lock = threading.Lock()

    def __init__(self):
        # Durable token state lives in private runtime storage.
        self.state_file = get_state_root() / 'whoop_token_state.json'
        self.access_token = None
        self.refresh_token = None
        self.last_refresh_at = None
        self.last_refresh_attempt_at = None
        self.last_refresh_attempt_result = None
        # Default to expired so bootstrap tokens get refreshed on first use.
        self.token_expires_at = datetime.now() - timedelta(seconds=1)

        self._load_token_state()

        if not self.access_token:
            env_access_token = (os.getenv('WHOOP_ACCESS_TOKEN') or '').strip()
            self.access_token = env_access_token or None
        if not self.refresh_token:
            env_refresh_token = (os.getenv('WHOOP_REFRESH_TOKEN') or '').strip()
            self.refresh_token = env_refresh_token or None

        self.client_id = os.getenv('WHOOP_CLIENT_ID')
        self.client_secret = os.getenv('WHOOP_CLIENT_SECRET')
        self.token_url = "https://api.prod.whoop.com/oauth/oauth2/token"

        if self.access_token:
            os.environ["WHOOP_ACCESS_TOKEN"] = self.access_token
        if self.refresh_token:
            os.environ["WHOOP_REFRESH_TOKEN"] = self.refresh_token

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _needs_refresh(self) -> bool:
        time_until_expiry = self.token_expires_at - datetime.now()
        seconds_until_expiry = time_until_expiry.total_seconds()
        return seconds_until_expiry <= REFRESH_THRESHOLD_SECONDS

    def _load_token_state(self):
        if not self.state_file.exists():
            return

        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)

            access_token = (state.get('access_token') or '').strip()
            refresh_token = (state.get('refresh_token') or '').strip()
            if access_token:
                self.access_token = access_token
            if refresh_token:
                self.refresh_token = refresh_token

            expires_raw = state.get('token_expires_at')
            if expires_raw:
                self.token_expires_at = datetime.fromisoformat(expires_raw)

            last_refresh_raw = state.get('last_refresh_at')
            if last_refresh_raw:
                self.last_refresh_at = datetime.fromisoformat(last_refresh_raw)

            last_attempt_raw = state.get('last_refresh_attempt_at')
            if last_attempt_raw:
                self.last_refresh_attempt_at = datetime.fromisoformat(last_attempt_raw)

            self.last_refresh_attempt_result = state.get('last_refresh_attempt_result')
        except Exception as e:
            logger.warning(f"Failed to load WHOOP token state: {e}")

    async def _refresh_token(self) -> bool:
        if not self._refresh_lock.acquire(blocking=False):
            self._refresh_lock.acquire(blocking=True)
            self._refresh_lock.release()
            return True

        try:
            if not self.refresh_token:
                note = "WHOOP_REFRESH_TOKEN is missing; re-authentication is required."
                self._record_refresh_attempt(False, note)
                self._notify_reauth_required(note)
                raise WhoopReauthRequired(note)
            if not self.client_id or not self.client_secret:
                note = "WHOOP client credentials are missing (WHOOP_CLIENT_ID/WHOOP_CLIENT_SECRET)."
                self._record_refresh_attempt(False, note)
                self._notify_refresh_failure(note)
                raise RuntimeError(note)

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.token_url,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": self.refresh_token,
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "scope": "offline read:recovery read:cycles read:sleep read:profile read:workout",
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=30.0,
                )

                if response.status_code != 200:
                    body_preview = (response.text or "").strip().replace("\n", " ")[:300]
                    note = f"WHOOP token refresh failed: {response.status_code} body={body_preview}"
                    logger.error(note)
                    if response.status_code in (400, 401):
                        lowered = body_preview.lower()
                        if "invalid_grant" in lowered or "expired" in lowered or "revoked" in lowered:
                            note = "WHOOP refresh token was rejected by OAuth server; re-authentication is required."
                            self._record_refresh_attempt(False, note)
                            self._notify_reauth_required(note)
                            raise WhoopReauthRequired(note)
                    self._record_refresh_attempt(False, note)
                    self._notify_refresh_failure(note)
                    return False

                tokens = response.json()
                self.access_token = tokens.get("access_token")
                self.refresh_token = tokens.get("refresh_token", self.refresh_token)
                
                # Use expires_in from response (typically 3600 seconds = 1 hour)
                expires_in = tokens.get("expires_in", 3600)
                self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)

                # Update environment only (not .env file)
                os.environ["WHOOP_ACCESS_TOKEN"] = self.access_token
                os.environ["WHOOP_REFRESH_TOKEN"] = self.refresh_token
                self._record_refresh_attempt(True, f"expires_in={expires_in}")

                logger.info(f"✅ Token refreshed, expires {self.token_expires_at}")
                return True
        except WhoopReauthRequired:
            raise
        except Exception as e:
            note = f"WHOOP token refresh error: {e}"
            logger.error(note, exc_info=True)
            self._record_refresh_attempt(False, note)
            self._notify_refresh_failure(note)
            return False
        finally:
            self._refresh_lock.release()

    def _save_token_state(self):
        """Save token state to JSON file in private runtime storage."""
        tmp_path = None
        try:
            state = {
                'access_token': self.access_token,
                'refresh_token': self.refresh_token,
                'access_token_prefix': self.access_token[:20] + '...' if self.access_token else None,
                'refresh_token_prefix': self.refresh_token[:20] + '...' if self.refresh_token else None,
                'token_expires_at': self.token_expires_at.isoformat(),
                'last_refresh_at': self.last_refresh_at.isoformat() if self.last_refresh_at else None,
                'last_refresh_attempt_at': self.last_refresh_attempt_at.isoformat() if self.last_refresh_attempt_at else None,
                'last_refresh_attempt_result': self.last_refresh_attempt_result,
            }
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                'w',
                encoding='utf-8',
                dir=self.state_file.parent,
                delete=False,
            ) as f:
                os.chmod(f.name, 0o600)
                json.dump(state, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
                tmp_path = f.name
            os.replace(tmp_path, self.state_file)
            os.chmod(self.state_file, 0o600)
            logger.info(f"WHOOP token state saved to {self.state_file}")
        except Exception as e:
            logger.error(f"Failed to save WHOOP token state: {e}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _record_refresh_attempt(self, success: bool, note: str):
        now = datetime.now()
        self.last_refresh_attempt_at = now
        self.last_refresh_attempt_result = f"{'success' if success else 'failure'}: {note}"
        if success:
            self.last_refresh_at = now
        self._save_token_state()

    def _notify_refresh_failure(self, message: str):
        try:
            from notifier import get_notifier

            run_date = get_pipeline_run_date_str()
            get_notifier().notify_warning(
                run_date=run_date,
                step="WhoopTokenRefresh",
                message="WHOOP token refresh failed.",
                details_tail=message,
            )
        except Exception as notify_error:
            logger.warning(f"Failed to send WHOOP refresh warning: {notify_error}")

    def _notify_reauth_required(self, message: str):
        try:
            from notifier import get_notifier

            run_date = get_pipeline_run_date_str()
            get_notifier().notify_error(
                run_date=run_date,
                step="WhoopTokenRefresh",
                error_type="WhoopReauthRequired",
                message=message,
                fatal=False,
            )
        except Exception as notify_error:
            logger.warning(f"Failed to send WHOOP re-auth notification: {notify_error}")

    async def get_valid_token(self) -> str:
        if self._needs_refresh():
            refreshed = await self._refresh_token()
            if not refreshed:
                raise WhoopReauthRequired("WHOOP token refresh failed and could not be recovered automatically.")
        if not self.access_token:
            raise WhoopReauthRequired("WHOOP_ACCESS_TOKEN is missing after refresh attempt.")
        return self.access_token

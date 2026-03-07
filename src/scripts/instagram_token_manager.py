"""
Instagram Token Manager - Automated refresh for long-lived Instagram Graph API tokens.

Instagram tokens work as follows:
- Short-lived tokens: 1 hour expiry
- Long-lived tokens: 60 days expiry (exchanged from short-lived)
- Refresh: Can be done via /refresh_access_token endpoint if token is at least 24 hours old

This manager:
1. Tracks last refresh timestamp in a local JSON file (private runtime state)
2. Automatically refreshes token if it's been > 40 days (well within 60-day limit)
3. Persists tokens ONLY to private runtime state files, never to .env
"""

import os
import json
import logging
import threading
import hashlib
from datetime import datetime, timedelta
import httpx
from utils import get_state_root, ensure_path

logger = logging.getLogger(__name__)

# Instagram long-lived tokens last 60 days
# Refresh threshold: 40 days (gives 20 days buffer, well within the 24-hour minimum)
REFRESH_THRESHOLD_DAYS = 40
# Minimum age before token can be refreshed (Meta requirement)
MIN_REFRESH_AGE_DAYS = 1

# Token refresh endpoint
REFRESH_URL = "https://graph.instagram.com/refresh_access_token"
GRAPH_BASE_URL = "https://graph.facebook.com/v21.0"
REQUIRED_PUBLISH_SCOPES = {"instagram_content_publish"}


class InstagramTokenManager:
    """Manages Instagram access token lifecycle with automatic refresh."""
    
    _instance = None
    _lock = threading.Lock()
    _refresh_lock = threading.Lock()

    def __init__(self):
        # Durable token state lives in the private runtime folder.
        self.token_state_file = ensure_path(get_state_root()) / 'instagram_token_state.json'

        self.access_token = None
        self.last_refresh = datetime.now() - timedelta(days=30)
        self.token_created_at = datetime.now()
        self.token_fingerprint = None
        self.last_refresh_attempt_at = None
        self.last_refresh_attempt_result = None

        # Load durable state first; environment can explicitly override it.
        self._load_token_state()

        env_token = (os.getenv('INSTAGRAM_ACCESS_TOKEN') or '').strip()
        if env_token:
            self.access_token = env_token

        self.user_id = os.getenv('INSTAGRAM_USER_ID')
        # legacy_ig = use graph.instagram.com refresh endpoint; off = validate-only strategy
        self.auto_refresh_mode = (os.getenv('INSTAGRAM_AUTO_REFRESH_MODE', 'off') or 'off').strip().lower()
        self.app_id = (os.getenv('FACEBOOK_APP_ID') or os.getenv('META_APP_ID') or '').strip()
        self.app_secret = (os.getenv('FACEBOOK_APP_SECRET') or os.getenv('META_APP_SECRET') or '').strip()
        self.refresh_threshold_days = int((os.getenv('INSTAGRAM_REFRESH_THRESHOLD_DAYS', '14') or '14').strip())
        self.refresh_cooldown_hours = int((os.getenv('INSTAGRAM_REFRESH_COOLDOWN_HOURS', '12') or '12').strip())

        if self.access_token:
            os.environ['INSTAGRAM_ACCESS_TOKEN'] = self.access_token
            self._sync_if_token_changed()

    @classmethod
    def get_instance(cls):
        """Get singleton instance with thread-safe initialization."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _load_token_state(self):
        """Load token state from JSON file or initialize defaults."""
        if self.token_state_file.exists():
            try:
                with open(self.token_state_file, 'r') as f:
                    state = json.load(f)
                    stored_token = (state.get('access_token') or '').strip()
                    if stored_token:
                        self.access_token = stored_token
                    self.last_refresh = datetime.fromisoformat(state.get('last_refresh', '2020-01-01T00:00:00'))
                    self.token_created_at = datetime.fromisoformat(state.get('token_created_at', '2020-01-01T00:00:00'))
                    self.token_fingerprint = state.get('token_fingerprint')
                    last_attempt_raw = state.get('last_refresh_attempt_at')
                    self.last_refresh_attempt_at = datetime.fromisoformat(last_attempt_raw) if last_attempt_raw else None
                    self.last_refresh_attempt_result = state.get('last_refresh_attempt_result')
                    logger.info(f"Token state loaded: last_refresh={self.last_refresh}")
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                logger.warning(f"Failed to load token state: {e}, initializing fresh")
                self._init_fresh_state()
        else:
            self._init_fresh_state()

    @staticmethod
    def _fingerprint(token: str) -> str | None:
        if not token:
            return None
        return hashlib.sha256(token.encode('utf-8')).hexdigest()[:16]

    def _init_fresh_state(self):
        """Initialize fresh token state - assumes token was just set."""
        # If we have a token, assume it was just set (or use current time as placeholder)
        self.last_refresh = datetime.now() - timedelta(days=30)  # Assume it's mid-life
        self.token_created_at = datetime.now()
        self.token_fingerprint = self._fingerprint(self.access_token)
        self.last_refresh_attempt_at = None
        self.last_refresh_attempt_result = None

    def _save_token_state(self):
        """Save token state to JSON file."""
        try:
            state = {
                'access_token': self.access_token,
                'last_refresh': self.last_refresh.isoformat(),
                'token_created_at': self.token_created_at.isoformat(),
                'access_token_prefix': self.access_token[:20] + '...' if self.access_token else None,
                'token_fingerprint': self.token_fingerprint,
                'last_refresh_attempt_at': self.last_refresh_attempt_at.isoformat() if self.last_refresh_attempt_at else None,
                'last_refresh_attempt_result': self.last_refresh_attempt_result,
            }
            with open(self.token_state_file, 'w') as f:
                json.dump(state, f, indent=2)
            logger.info(f"Token state saved to {self.token_state_file}")
        except Exception as e:
            logger.error(f"Failed to save token state: {e}")

    def _sync_if_token_changed(self):
        current_fp = self._fingerprint(self.access_token)
        if current_fp and current_fp != self.token_fingerprint:
            logger.info("Detected new Instagram token value. Resetting token lifecycle state.")
            self.token_fingerprint = current_fp
            self.token_created_at = datetime.now()
            self.last_refresh = datetime.now() - timedelta(days=30)
            self.last_refresh_attempt_at = None
            self.last_refresh_attempt_result = None
            self._save_token_state()

    def _refresh_attempt_allowed(self) -> bool:
        if not self.last_refresh_attempt_at:
            return True
        return (datetime.now() - self.last_refresh_attempt_at) >= timedelta(hours=max(1, self.refresh_cooldown_hours))

    def _record_refresh_attempt(self, success: bool, note: str):
        self.last_refresh_attempt_at = datetime.now()
        self.last_refresh_attempt_result = f"{'success' if success else 'failure'}: {note}"
        self._save_token_state()

    def _needs_refresh(self) -> bool:
        """Check if token needs refreshing based on time since last refresh."""
        if not self.access_token:
            logger.warning("No INSTAGRAM_ACCESS_TOKEN found")
            return False

        days_since_refresh = (datetime.now() - self.last_refresh).days
        
        # Check if token is old enough to refresh (Meta requirement: > 24 hours)
        if days_since_refresh < MIN_REFRESH_AGE_DAYS:
            logger.info(f"Token is only {days_since_refresh} days old, skipping refresh (min {MIN_REFRESH_AGE_DAYS} days)")
            return False

        # Check if token is due for refresh (> 40 days = 20 day buffer before 60-day expiry)
        if days_since_refresh >= REFRESH_THRESHOLD_DAYS:
            logger.info(f"Token is {days_since_refresh} days old (threshold: {REFRESH_THRESHOLD_DAYS}), needs refresh")
            return True

        logger.info(f"Token is {days_since_refresh} days old, no refresh needed (threshold: {REFRESH_THRESHOLD_DAYS} days)")
        return False

    def _validate_token_value(self, token: str) -> tuple[bool, str]:
        """
        Validate any token against Graph API using configured Instagram user id.
        Returns (is_valid, detail).
        """
        if not token:
            return False, "INSTAGRAM_ACCESS_TOKEN is missing."
        if not self.user_id:
            return False, "INSTAGRAM_USER_ID is missing."

        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.get(
                    f"{GRAPH_BASE_URL}/{self.user_id}",
                    params={
                        "fields": "id,username",
                        "access_token": token,
                    },
                )
            if response.status_code == 200:
                return True, "Token accepted by Graph API."
            body = response.text[:500]
            return False, f"Graph token validation failed ({response.status_code}): {body}"
        except Exception as e:
            return False, f"Graph token validation request failed: {e}"

    def _inspect_token_health_for_value(self, token: str) -> dict:
        """
        Inspect health for a specific token value.
        Expiry metadata requires APP_ID/APP_SECRET for debug_token.
        """
        valid, detail = self._validate_token_value(token)
        report = {
            "valid": valid,
            "detail": detail,
            "checked_at": datetime.now().isoformat(),
            "expires_at": None,
            "days_to_expiry": None,
            "scopes": [],
        }
        if not valid:
            return report

        # Optional expiry/scopes introspection via Graph debug_token.
        if not (self.app_id and self.app_secret):
            return report

        app_access_token = f"{self.app_id}|{self.app_secret}"
        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.get(
                    f"{GRAPH_BASE_URL}/debug_token",
                    params={
                        "input_token": token,
                        "access_token": app_access_token,
                    },
                )
            if response.status_code != 200:
                report["detail"] = f"{detail} | debug_token failed: {response.status_code}"
                return report

            data = response.json().get("data", {})
            expires_at = int(data.get("expires_at") or 0)
            if expires_at > 0:
                dt = datetime.fromtimestamp(expires_at)
                report["expires_at"] = dt.isoformat()
                report["days_to_expiry"] = max(0, (dt - datetime.now()).days)
            report["scopes"] = data.get("scopes") or []
            report["valid"] = bool(data.get("is_valid", valid))
            if not report["valid"]:
                report["detail"] = "debug_token reports invalid token."
            return report
        except Exception as e:
            report["detail"] = f"{detail} | debug_token error: {e}"
            return report

    async def _refresh_token_async(self) -> bool:
        """
        Refresh the Instagram access token via the refresh endpoint.
        
        Returns:
            bool: True if refresh succeeded, False otherwise
        """
        if not self.access_token:
            logger.error("No access token to refresh")
            return False

        # Use refresh lock to prevent concurrent refresh attempts
        if not self._refresh_lock.acquire(blocking=False):
            logger.info("Another refresh is in progress, waiting...")
            self._refresh_lock.acquire(blocking=True)
            self._refresh_lock.release()
            valid, detail = self._validate_token()
            if not valid:
                logger.error(f"Concurrent refresh finished but token still invalid: {detail}")
            return valid

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    REFRESH_URL,
                    params={
                        'grant_type': 'ig_refresh_token',
                        'access_token': self.access_token
                    },
                    timeout=30.0
                )

                if response.status_code != 200:
                    body = response.text[:500]
                    logger.error(f"Token refresh failed: {response.status_code} - {body}")
                    return False

                data = response.json()
                
                # Extract new token
                new_token = data.get('access_token')
                if not new_token:
                    logger.error(f"No access_token in refresh response: {data}")
                    return False

                # Get new token duration (expires_in is in seconds)
                expires_in = data.get('expires_in', 5184000)  # Default 60 days in seconds

                # Validate refreshed token before committing to runtime/.env.
                refreshed_report = self._inspect_token_health_for_value(new_token)
                if not refreshed_report.get("valid"):
                    logger.error(
                        "Refreshed token failed validation; keeping existing token. "
                        f"Detail: {refreshed_report.get('detail')}"
                    )
                    return False

                old_token_prefix = self.access_token[:20] + '...'

                # Persist token to state file only (not .env)
                self.access_token = new_token
                self.last_refresh = datetime.now()
                self.token_created_at = datetime.now()
                self.token_fingerprint = self._fingerprint(new_token)
                os.environ['INSTAGRAM_ACCESS_TOKEN'] = new_token
                self._save_token_state()

                logger.info(f"✅ Instagram token refreshed successfully!")
                logger.info(f"   Old token: {old_token_prefix}")
                logger.info(f"   New token: {new_token[:20]}...")
                logger.info(f"   Token expires in: {expires_in} seconds (~{expires_in // 86400} days)")
                
                return True

        except httpx.RequestError as e:
            logger.error(f"Network error during token refresh: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during token refresh: {e}", exc_info=True)
            return False
        finally:
            self._refresh_lock.release()

    def _refresh_token(self) -> bool:
        """Synchronous wrapper for token refresh."""
        try:
            import asyncio
            return asyncio.run(self._refresh_token_async())
        except Exception as e:
            logger.error(f"Token refresh failed: {e}")
            return False

    def maybe_auto_refresh(self, report: dict = None, force_on_invalid: bool = False) -> tuple[bool, str]:
        """
        Hybrid auto-refresh policy:
        - force_on_invalid=True: attempt refresh if token invalid.
        - otherwise refresh when days_to_expiry <= threshold.
        Returns (attempted_and_succeeded, detail_message).
        """
        if self.auto_refresh_mode not in ('hybrid', 'legacy_ig'):
            return False, "auto-refresh disabled"

        if not self._refresh_attempt_allowed():
            return False, f"cooldown active ({self.refresh_cooldown_hours}h)"

        if report is None:
            report = self.inspect_token_health()

        should_attempt = False
        reason = ""
        if force_on_invalid and not report.get("valid"):
            should_attempt = True
            reason = "token invalid"
        elif self.auto_refresh_mode == 'legacy_ig':
            should_attempt = self._needs_refresh()
            reason = "legacy age-based refresh window"
        else:
            days_left = report.get("days_to_expiry")
            if days_left is not None and days_left <= self.refresh_threshold_days:
                should_attempt = True
                reason = f"expiry threshold reached ({days_left}d <= {self.refresh_threshold_days}d)"

        if not should_attempt:
            return False, "refresh not needed"

        ok = self._refresh_token()
        self._record_refresh_attempt(ok, reason)
        if ok:
            # keep fingerprint in sync after successful refresh/token update
            self.token_fingerprint = self._fingerprint(self.access_token)
            self._save_token_state()
            return True, f"refresh succeeded: {reason}"
        return False, f"refresh failed: {reason}"

    def _validate_token(self) -> tuple[bool, str]:
        """
        Validate token against Graph API using configured Instagram user id.
        Returns (is_valid, detail).
        """
        return self._validate_token_value(self.access_token)

    def inspect_token_health(self) -> dict:
        """
        Inspect token health and (optionally) expiry metadata.
        Expiry metadata requires APP_ID/APP_SECRET for debug_token.
        """
        return self._inspect_token_health_for_value(self.access_token)

    def _assert_publish_scopes(self, report: dict):
        """
        Ensure posting-critical scopes are present when scope introspection is available.
        If scopes are unavailable, skip (still allows operation in validate-only environments).
        """
        scopes = report.get("scopes") or []
        if not scopes:
            return
        missing = REQUIRED_PUBLISH_SCOPES - set(scopes)
        if missing:
            raise RuntimeError(
                "Instagram token is valid but missing required publish scope(s): "
                + ", ".join(sorted(missing))
            )

    def get_valid_token(self) -> str:
        """
        Get a valid Instagram access token, refreshing if necessary.
        
        This is the main entry point - always call this instead of directly
        reading from environment variables.
        
        Returns:
            str: Valid Instagram access token
            
        Raises:
            RuntimeError: If token is missing and cannot be refreshed
        """
        if not self.access_token:
            raise RuntimeError(
                "INSTAGRAM_ACCESS_TOKEN is missing. "
                "Provide a valid Instagram long-lived token via bootstrap env vars or private runtime state."
            )

        self._sync_if_token_changed()
        report = self.inspect_token_health()
        valid = bool(report.get("valid"))
        detail = report.get("detail", "Token check failed")
        if valid:
            logger.info("Instagram token validation passed.")
            self._assert_publish_scopes(report)
            # In hybrid mode, proactively refresh near expiry but never block if token is still valid.
            if self.auto_refresh_mode == 'hybrid':
                refreshed, refresh_msg = self.maybe_auto_refresh(report=report, force_on_invalid=False)
                if refreshed:
                    report_after = self.inspect_token_health()
                    if report_after.get("valid"):
                        self._assert_publish_scopes(report_after)
                        logger.info("Instagram token proactively refreshed in hybrid mode.")
                    else:
                        logger.error(
                            "Hybrid proactive refresh reported success but token check failed afterward: "
                            f"{report_after.get('detail')}"
                        )
                elif refresh_msg not in ("refresh not needed", "auto-refresh disabled"):
                    logger.info(f"Hybrid refresh skipped: {refresh_msg}")
            return self.access_token

        logger.error(detail)
        if self.auto_refresh_mode in ('legacy_ig', 'hybrid'):
            logger.info("Attempting auto-refresh recovery for invalid token...")
            refreshed, refresh_msg = self.maybe_auto_refresh(report=report, force_on_invalid=True)
            logger.info(f"Auto-refresh recovery result: {refresh_msg}")
            if refreshed:
                report_after = self.inspect_token_health()
                if report_after.get("valid"):
                    self._assert_publish_scopes(report_after)
                    logger.info("Instagram token recovered after refresh.")
                    return self.access_token
                logger.error(f"Token still invalid after refresh: {report_after.get('detail')}")

        raise RuntimeError(
            "Instagram token is invalid or expired. "
            "Generate a fresh posting token, update INSTAGRAM_ACCESS_TOKEN, then retry."
        )

        return self.access_token

    def get_user_id(self) -> str:
        """Get the Instagram user ID."""
        return self.user_id

    def force_refresh(self) -> bool:
        """Force a token refresh regardless of age."""
        logger.info("Forcing token refresh...")
        return self._refresh_token()


# Convenience function for easy import
def get_instagram_token_manager() -> InstagramTokenManager:
    """Get the singleton InstagramTokenManager instance."""
    return InstagramTokenManager.get_instance()


if __name__ == '__main__':
    # Test script - refresh token if needed
    logging.basicConfig(level=logging.INFO)
    
    manager = InstagramTokenManager.get_instance()
    try:
        token = manager.get_valid_token()
        print(f"Current token: {token[:30]}...")
        print(f"User ID: {manager.get_user_id()}")
    except Exception as e:
        print(f"Token check failed: {e}")

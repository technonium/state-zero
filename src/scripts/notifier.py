"""
Centralized notification module for Telegram alerts.

This module provides a unified interface for sending error, warning, status,
and success notifications through Telegram. It includes:
- Deduplication to prevent spam
- Retry logic for failed sends
- Thread-safe operation
- Graceful fallback to logging if Telegram is unavailable
"""

import hashlib
import html
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from zoneinfo import ZoneInfo
from utils import get_state_root, load_project_dotenv, sanitize_error

_TEST_MODE = os.getenv('STATE_ZERO_TEST_MODE') == '1'

def _in_test_process() -> bool:
    # Keep test isolation when a fixture temporarily clears the environment.
    return _TEST_MODE or os.getenv('STATE_ZERO_TEST_MODE') == '1'


if not _in_test_process():
    load_project_dotenv()

# Configure local logging as fallback
LOCAL_LOG = logging.getLogger("notifier")
LOCAL_LOG.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(logging.Formatter("%(asctime)s [NOTIFIER] %(levelname)s: %(message)s"))
LOCAL_LOG.addHandler(_handler)


class NotificationError(Exception):
    """Raised when notification sending fails after all retries."""
    pass


class Notifier:
    """
    Centralized Telegram notifier with deduplication and retry logic.
    
    Supports:
    - Error notifications (fatal)
    - Warning notifications (recoverable)
    - Status notifications (milestones)
    - Success notifications (MP4 + permalink)
    """
    
    _instance: Optional['Notifier'] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """Singleton pattern for centralized state."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '').strip()
        
        # Notification toggles
        self.notify_errors = os.getenv('TELEGRAM_NOTIFY_ERRORS', 'true').strip().lower() in ('1', 'true', 'yes', 'on')
        self.notify_success = os.getenv('TELEGRAM_NOTIFY_SUCCESS', 'true').strip().lower() in ('1', 'true', 'yes', 'on')
        
        # Dedupe settings
        self.dedupe_seconds = int(os.getenv('TELEGRAM_ERROR_DEDUPE_SECONDS', '300'))
        self._last_notification: dict[str, float] = {}
        
        # Retry settings
        self.max_retries = 3
        self.retry_delay = 2.0
        
        # Stderr lines to include
        self.include_stderr_lines = int(os.getenv('TELEGRAM_NOTIFY_INCLUDE_STDERR_LINES', '40'))
        
        # Timezone for timestamps
        self.timezone = ZoneInfo(os.getenv('PIPELINE_TIMEZONE', 'Asia/Kolkata'))
        
        self._initialized = True
        self._notification_lock = threading.Lock()
    
    def _is_enabled(self) -> bool:
        """Check if notifications are enabled."""
        return bool(self.bot_token and self.chat_id)
    
    def _make_dedupe_key(self, step: str, error_type: str, message: str) -> str:
        """Create a hash key for deduplication."""
        content = f"{step}:{error_type}:{message[:100]}"
        return hashlib.md5(content.encode()).hexdigest()
    
    def _escape_html(self, text: str) -> str:
        """Escape text for Telegram HTML parse mode."""
        return html.escape(sanitize_error(text), quote=True)
    
    def _should_send(self, dedupe_key: str) -> bool:
        """Check if notification should be sent (deduplication)."""
        now = time.time()
        with self._notification_lock:
            last_sent = self._last_notification.get(dedupe_key, 0)
            if now - last_sent < self.dedupe_seconds:
                return False
            self._last_notification[dedupe_key] = now
            return True
    
    def _telegram_request(self, method: str, *, data=None, files=None, timeout=60, use_get=False) -> dict:
        """Make request to Telegram API."""
        # Credentials may already be cached; test isolation must hold at the HTTP boundary.
        if _in_test_process():
            raise NotificationError("Real Telegram transport disabled in a test process; mock this boundary")
        if not self._is_enabled():
            raise NotificationError("Telegram credentials not configured")
        
        url = f"https://api.telegram.org/bot{self.bot_token}/{method}"
        
        if use_get:
            resp = requests.get(url, params=data or {}, timeout=timeout)
        else:
            resp = requests.post(url, data=data or {}, files=files, timeout=timeout)
        
        payload = resp.json()
        if not payload.get('ok'):
            raise NotificationError(f"Telegram API {method} failed (HTTP {resp.status_code})")
        return payload
    
    def _send_message(self, text: str, parse_mode: str = None) -> bool:
        """Send text message to Telegram with retry logic."""
        text = sanitize_error(text)
        if not self._is_enabled():
            if os.getenv("FLOW_SHADOW_ALERTS_OWNED_BY_RUNNER") == "true":
                return False
            LOCAL_LOG.warning(f"Telegram not configured. Message: {text[:200]}")
            return False
        
        payload = {'chat_id': self.chat_id, 'text': text}
        if parse_mode:
            payload['parse_mode'] = parse_mode
        
        try:
            self._telegram_request('sendMessage', data=payload, timeout=(3, 7))
            return True
        except Exception:
            # Status delivery must not consume the generation/rescue window.
            LOCAL_LOG.error('Telegram message delivery failed; pipeline continues.')
            return False
    
    def _send_document(self, file_path: Path, caption: str = None) -> bool:
        """Send document to Telegram with retry logic."""
        if not self._is_enabled():
            if os.getenv("FLOW_SHADOW_ALERTS_OWNED_BY_RUNNER") != "true":
                LOCAL_LOG.warning(f"Telegram not configured. Would send document: {file_path}")
            return False
        
        if not file_path.exists():
            LOCAL_LOG.error(f"Cannot send non-existent file: {file_path}")
            return False
        
        payload = {'chat_id': self.chat_id}
        if caption:
            payload['caption'] = caption
        
        for attempt in range(self.max_retries):
            try:
                with open(file_path, 'rb') as f:
                    self._telegram_request('sendDocument', data=payload, files={'document': f})
                return True
            except Exception as e:
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))
                else:
                    LOCAL_LOG.error(f"Failed to send Telegram document after {self.max_retries} attempts: {sanitize_error(str(e))}")
                    return False
        return False
    
    def notify_error(
        self,
        run_date: str,
        step: str,
        error_type: str,
        message: str,
        details_tail: str = None,
        fatal: bool = True
    ) -> bool:
        """
        Send fatal error notification.
        
        Args:
            run_date: The pipeline run date (YYYY-MM-DD)
            step: Step name where error occurred
            error_type: Type of error (Validation, Subprocess, etc.)
            message: Error message
            details_tail: Optional stderr/stdout tail to include
            fatal: Whether this is a fatal error (pipeline will exit)
        
        Returns:
            bool: True if notification sent successfully
        """
        if not self.notify_errors:
            LOCAL_LOG.info(f"Error notifications disabled. Would send: [{step}] {sanitize_error(message)}")
            return False
        
        dedupe_key = self._make_dedupe_key(step, error_type, message)
        if not self._should_send(dedupe_key):
            LOCAL_LOG.info(f"Throttled error notification for {step}")
            return False
        
        timestamp = datetime.now(self.timezone).strftime('%Y-%m-%d %H:%M:%S %Z')
        
        step_escaped = self._escape_html(step)
        error_type_escaped = self._escape_html(error_type)
        message_escaped = self._escape_html(message)
        
        emoji = "🔴" if fatal else "🟡"
        text = f"{emoji} <b>PIPELINE ERROR</b> | {self._escape_html(run_date)}\n"
        text += f"<b>Step:</b> {step_escaped}\n"
        text += f"<b>Type:</b> {error_type_escaped}\n"
        text += f"<b>Message:</b> {message_escaped}\n"
        
        if details_tail:
            text += "\nAdditional diagnostics retained privately."
        
        text += f"\n<i>{self._escape_html(timestamp)}</i>"
        
        return self._send_message(text, parse_mode='HTML')
    
    def notify_warning(
        self,
        run_date: str,
        step: str,
        message: str,
        details_tail: str = None
    ) -> bool:
        """
        Send warning notification (recoverable error).
        
        Args:
            run_date: The pipeline run date
            step: Step name where warning occurred
            message: Warning message
            details_tail: Optional additional details
        
        Returns:
            bool: True if notification sent successfully
        """
        if not self.notify_errors:
            return False
        dedupe_key = None if step.startswith('Manual') else self._make_dedupe_key(f'{run_date}:{step}', 'warning', message)
        if dedupe_key and not self._should_send(dedupe_key):
            return False
        
        timestamp = datetime.now(self.timezone).strftime('%Y-%m-%d %H:%M:%S %Z')

        run_date_escaped = self._escape_html(run_date)
        step_escaped = self._escape_html(step)
        message_escaped = self._escape_html(message)

        text = f"🟡 <b>PIPELINE WARNING</b> | {run_date_escaped}\n"
        text += f"<b>Step:</b> {step_escaped}\n"
        text += f"<b>Message:</b> {message_escaped}\n"

        if details_tail:
            text += "\nAdditional diagnostics retained privately."

        text += f"\n<i>{self._escape_html(timestamp)}</i>"

        sent = self._send_message(text, parse_mode='HTML')
        if not sent and dedupe_key:
            with self._notification_lock:
                self._last_notification.pop(dedupe_key, None)
        return sent

    @staticmethod
    def _post_summary(run_date: str, output_dir: Path, fallback_version: str | None = None) -> str:
        def read(path):
            try:
                value = json.loads(path.read_text())
                return value if isinstance(value, dict) else {}
            except (OSError, ValueError):
                return {}

        state = read(get_state_root() / 'daily_runs' / f'{run_date}.json')
        try:
            seconds = int((datetime.fromisoformat(state['posted_at']) -
                           datetime.fromisoformat(state['claimed_at'])).total_seconds())
            if seconds < 0:
                raise ValueError('Post predates claim')
            duration = f'{seconds // 60}m{seconds % 60:02d}s since claim'
        except (KeyError, TypeError, ValueError):
            duration = 'duration unavailable'
        providers, recovered, replacements, paid = [], [], 0, []
        for stage in ('image', 'video'):
            path = output_dir / f'flow_{stage}_diagnostics.json'
            saved = read(path)
            api = read(output_dir / f'google_{stage}_diagnostics.json')
            provider = saved.get('provider') or ('google_api' if api.get('selected_sha256') else None)
            providers.append({'flow': 'Flow', 'google_api': 'Google MEDIA API'}.get(provider, 'unavailable'))
            if provider == 'google_api':
                paid.append(f'{stage} used')
            elif saved.get('api_reserved') or api.get('logical_attempt'):
                paid.append(f'{stage} requested; acceptance/charge unconfirmed')
            if saved:
                try:
                    from flow_media_client import FlowMediaClient
                    attempts = FlowMediaClient._daily_attempts(stage, path)
                    if replacements is not None:
                        replacements += max(0, sum(a['submission'] not in {
                            'not_attempted', 'blocked_before_submission'} for a in attempts) - 1)
                except Exception:
                    replacements = None
                if any('_RECOVERED_' in event for event in saved.get('notification_events', {})):
                    recovered.append(f'exact {stage} recovered')
                elif any(a.get('invocations') for a in (saved.get('recovery_workers') or {}).get('attempts', {}).values()):
                    recovered.append(f'{stage} recovery workers used')
        if any(read(path).get('state') == 'recovered' for path in output_dir.glob('flow_*_attempt_*.upload.json')):
            recovered.append('exact upload recovered')
        provider = (f'Emergency/404 {fallback_version}' if fallback_version else
                    'Manual' if read(output_dir / 'instagram_publish_diagnostics.json').get('publish_context', {}).get('asset_source') == 'manual_telegram' else
                    providers[0] if len(set(providers)) == 1 else f'image {providers[0]} / video {providers[1]}')
        parts = [provider, duration, ', '.join(recovered) or 'no recovery recorded',
                 f'replacements {replacements if replacements is not None else "unavailable"}',
                 'MEDIA API: ' + (', '.join(paid) if paid else 'none recorded')]
        if fallback_version:
            parts.append('trigger: ' + str(state.get('fallback_trigger_stage') or 'unavailable'))
        return ' · '.join(parts)
    
    def notify_status(
        self,
        run_date: str,
        status: str,
        message: str
    ) -> bool:
        """
        Send status/milestone notification.
        
        Args:
            run_date: The pipeline run date
            status: Status text (e.g., "FALLBACK_STARTED", "DEADLINE_PASSED")
            message: Status message
        
        Returns:
            bool: True if notification sent successfully
        """
        if not self.notify_errors:
            return False
        
        timestamp = datetime.now(self.timezone).strftime('%Y-%m-%d %H:%M:%S %Z')

        run_date_escaped = self._escape_html(run_date)
        status_escaped = self._escape_html(status)
        message_escaped = self._escape_html(message)

        text = f"🔵 <b>PIPELINE STATUS</b> | {run_date_escaped}\n"
        text += f"<b>Status:</b> {status_escaped}\n"
        text += f"<b>Message:</b> {message_escaped}\n"
        text += f"\n<i>{self._escape_html(timestamp)}</i>"
        
        return self._send_message(text, parse_mode='HTML')
    
    def notify_success_posted(
        self,
        run_date: str,
        final_mp4_path: Path,
        instagram_permalink: str
    ) -> bool:
        """
        Send success notification after real Instagram publish.
        
        Sends ONLY:
        - card_final.mp4 as document
        - Instagram permalink in message
        
        Args:
            run_date: The pipeline run date
            final_mp4_path: Path to the final MP4 file
            instagram_permalink: Public Instagram permalink
        
        Returns:
            bool: True if notification sent successfully
        """
        if not self.notify_success:
            LOCAL_LOG.info("Success notifications disabled")
            return False
        
        return self._send_post_success(
            run_date=run_date,
            final_mp4_path=final_mp4_path,
            instagram_permalink=instagram_permalink,
            document_caption=f"🎉 SUCCESS | {run_date}\nPosted to Instagram!",
            message_title="✅ <b>Posted to Instagram</b>",
            version_label=None,
        )

    def notify_emergency_fallback_activated(
        self,
        run_date: str,
        trigger_stage: str,
        fallback_version: str,
    ) -> bool:
        """Send status notification when emergency fallback activates."""
        message = (
            f"Emergency fallback activated because {trigger_stage} failed. "
            f"Posting {fallback_version}."
        )
        return self.notify_status(run_date, "EMERGENCY_POST_FALLBACK", message)

    def notify_emergency_fallback_posted(
        self,
        run_date: str,
        final_mp4_path: Path,
        instagram_permalink: str,
        fallback_version: str,
    ) -> bool:
        """Send success notification after an emergency fallback post succeeds."""
        return self._send_post_success(
            run_date=run_date,
            final_mp4_path=final_mp4_path,
            instagram_permalink=instagram_permalink,
            document_caption=f"⚠️ FALLBACK SUCCESS | {run_date}\nEmergency fallback posted.",
            message_title="⚠️ <b>Emergency Fallback Posted</b>",
            version_label=fallback_version,
        )

    def _send_post_success(
        self,
        *,
        run_date: str,
        final_mp4_path: Path,
        instagram_permalink: str,
        document_caption: str,
        message_title: str,
        version_label: str | None,
    ) -> bool:
        """Send a post-success notification with MP4 and permalink."""
        timestamp = datetime.now(self.timezone).strftime('%Y-%m-%d %H:%M:%S %Z')
        run_date_escaped = self._escape_html(run_date)
        permalink = (instagram_permalink or 'Unavailable').strip()

        doc_sent = self._send_document(final_mp4_path, document_caption)

        text = f"{message_title}\n"
        text += f"<b>Date:</b> {run_date_escaped}\n"
        if version_label:
            text += f"<b>Version:</b> {self._escape_html(version_label)}\n"
        try:
            summary = self._post_summary(run_date, Path(final_mp4_path).parent, version_label)
        except Exception:
            summary = 'Provider/duration/recovery summary unavailable; diagnostics retained privately.'
        text += self._escape_html(summary) + '\n'
        if permalink.startswith(("http://", "https://")):
            href = self._escape_html(permalink)
            text += f"<b>Link:</b> <a href=\"{href}\">{href}</a>\n"
        else:
            text += f"<b>Link:</b> {self._escape_html(permalink)}\n"
        text += f"\n<i>{self._escape_html(timestamp)}</i>"

        msg_sent = self._send_message(text, parse_mode='HTML')
        return doc_sent and msg_sent
    
    def notify_dry_run_complete(self, run_date: str, mode: str = 'automatic', output_dir: Path = None) -> bool:
        LOCAL_LOG.info("Dry run complete: %s (%s); no Instagram post was made. Outputs: %s",
                       run_date, mode, output_dir)
        return False


def get_notifier() -> Notifier:
    """Get the global notifier instance."""
    return Notifier()


# Convenience functions for direct use
def notify_error(run_date: str, step: str, error_type: str, message: str, details_tail: str = None, fatal: bool = True) -> bool:
    """Send error notification."""
    return get_notifier().notify_error(run_date, step, error_type, message, details_tail, fatal)

def notify_warning(run_date: str, step: str, message: str, details_tail: str = None) -> bool:
    """Send warning notification."""
    return get_notifier().notify_warning(run_date, step, message, details_tail)

def notify_status(run_date: str, status: str, message: str) -> bool:
    """Send status notification."""
    return get_notifier().notify_status(run_date, status, message)

def notify_success_posted(run_date: str, final_mp4_path: Path, instagram_permalink: str) -> bool:
    """Send success notification with MP4 and permalink."""
    return get_notifier().notify_success_posted(run_date, final_mp4_path, instagram_permalink)


def notify_emergency_fallback_activated(run_date: str, trigger_stage: str, fallback_version: str) -> bool:
    """Send status notification when emergency fallback activates."""
    return get_notifier().notify_emergency_fallback_activated(run_date, trigger_stage, fallback_version)


def notify_emergency_fallback_posted(
    run_date: str,
    final_mp4_path: Path,
    instagram_permalink: str,
    fallback_version: str,
) -> bool:
    """Send success notification for an emergency fallback post."""
    return get_notifier().notify_emergency_fallback_posted(
        run_date,
        final_mp4_path,
        instagram_permalink,
        fallback_version,
    )

def notify_dry_run_complete(run_date: str, mode: str = 'automatic', output_dir = None) -> bool:
    """Send dry run complete notification."""
    return get_notifier().notify_dry_run_complete(run_date, mode, output_dir)


def safe_send_telegram_message(text: str, run_date: str = None, context: str = '') -> bool:
    """
    Best-effort wrapper for sending Telegram messages. NEVER raises - catches all exceptions.
    
    This wrapper ensures Telegram API failures never block pipeline progress.
    On failure, it logs a warning and returns False.
    
    Args:
        text: Message text to send
        run_date: Optional run date for logging
        context: Optional context description for error messages
    
    Returns:
        bool: True if message sent successfully, False if it failed (non-fatal)
    """
    try:
        notifier = get_notifier()
        return notifier._send_message(text)
    except Exception as e:
        LOCAL_LOG.warning(f"Telegram send failed{context}: {sanitize_error(str(e))}")
        if run_date:
            LOCAL_LOG.warning(f"[RUN {run_date}] Telegram dispatch failure - pipeline continues automatically")
        return False

def safe_notify_status(run_date: str, status: str, message: str) -> bool:
    """
    Best-effort wrapper for status notifications. NEVER raises.
    
    Returns:
        bool: True if notification sent, False if it failed (non-fatal)
    """
    try:
        notifier = get_notifier()
        return notifier.notify_status(run_date, status, message)
    except Exception as e:
        LOCAL_LOG.warning(f"Status notification failed: {sanitize_error(str(e))}")
        return False

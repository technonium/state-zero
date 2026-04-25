import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
import shlex
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from colorama import Fore, Style, init
from PIL import Image

from utils import (
    get_project_root,
    get_output_root,
    get_local_vps_root,
    get_media_mode,
    VALID_MEDIA_MODES,
    env_bool,
    ensure_path,
    get_pipeline_run_date_str,
    get_live_vps_config_error,
    load_project_dotenv,
    get_pipeline_deadline,
    is_terminal_rescue_run as infer_terminal_rescue_run,
)
from environment_utils import split_environment_output
from notifier import get_notifier, safe_send_telegram_message, safe_notify_status
from daily_run_state import DailyRunStateManager, OwnershipLostError

load_project_dotenv()
init()


HASHTAGS = [
    '#GenerativeArt',
    '#AIArt',
    '#DataArt',
    '#DataVisualization',
    '#CreativeCoding',
    '#GenerativeAI',
    '#AIGenerated',
    '#AlgorithmicArt',
    '#WHOOP',
    '#WHOOPData',
    '#QuantifiedSelf',
    '#SelfTracking',
    '#Biohacking',
    '#Biohacker',
    '#DigitalHealth',
    '#HealthTech',
    '#WearableTech',
    '#DigitalArt',
]


def _build_hashtags(date_str: str) -> str:
    return ' '.join(HASHTAGS)


def _setup_global_exception_handler(pipeline_instance):
    """Set up global exception handlers for uncaught exceptions."""
    run_date = pipeline_instance.run_date
    output_dir = pipeline_instance.output_dir
    
    def exception_handler(exc_type, exc_value, exc_traceback):
        # Don't handle keyboard interrupt
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        
        import traceback
        tb_str = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        
        # Log locally
        with open(output_dir / 'pipeline_error.log', 'a', encoding='utf-8') as f:
            f.write(f"[UNCAUGHT EXCEPTION] {exc_type.__name__}: {exc_value}\n{tb_str}\n")
        
        # Send Telegram notification
        notifier = get_notifier()
        notifier.notify_error(
            run_date=run_date,
            step='UNCAUGHT_EXCEPTION',
            error_type=exc_type.__name__,
            message=str(exc_value),
            details_tail=tb_str[-2000:],
            fatal=True
        )
        
        # Print to console
        print(f"{Fore.RED}❌ UNCAUGHT EXCEPTION: {exc_type.__name__}: {exc_value}{Style.RESET_ALL}")
        print(f"{Fore.RED}{tb_str}{Style.RESET_ALL}")
    
    sys.excepthook = exception_handler
    
    # Thread exception handler
    def threading_exception_handler(args):
        notifier = get_notifier()
        notifier.notify_error(
            run_date=run_date,
            step='THREAD_EXCEPTION',
            error_type=args.exc_type.__name__ if args.exc_type else 'ThreadError',
            message=str(args.exc_value),
            fatal=True
        )
    
    threading.excepthook = threading_exception_handler


class PipelineStageError(Exception):
    def __init__(
        self,
        stage: str,
        message: str,
        details: str | None = None,
        details_obj: object | None = None,
        fallback_eligible: bool = False,
        failure_classification: str | None = None,
    ):
        self.stage = stage
        self.message = message
        self.details = details
        self.details_obj = details_obj
        self.fallback_eligible = fallback_eligible
        self.failure_classification = failure_classification
        super().__init__(message)


class WHOOPPipeline:
    def __init__(self):
        raw_mode = os.getenv('PIPELINE_MODE', 'automatic').strip().lower()
        self.mode = self._normalize_mode(raw_mode)
        # post_to_instagram=False means dry-run (skip Instagram publish path)
        self.post_to_instagram = env_bool('PIPELINE_POST_TO_INSTAGRAM', default=True)
        self.media_mode = get_media_mode()
        if self.media_mode not in VALID_MEDIA_MODES:
            print(
                f"{Fore.YELLOW}⚠ Invalid PIPELINE_MEDIA_MODE={self.media_mode}. "
                f"Use one of: {', '.join(VALID_MEDIA_MODES)}.{Style.RESET_ALL}"
            )
            sys.exit(1)

        self.base_dir = get_project_root()
        self.pipeline_timezone = os.getenv('PIPELINE_TIMEZONE', 'Asia/Kolkata').strip() or 'Asia/Kolkata'
        self.tz = ZoneInfo(self.pipeline_timezone)
        run_date = os.getenv('PIPELINE_DATE') or get_pipeline_run_date_str()
        self.run_date = run_date
        os.environ['PIPELINE_DATE'] = run_date

        self.output_dir = get_output_root() / self.run_date
        self.local_vps_dir = get_local_vps_root()

        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '').strip()
        self.telegram_poll_seconds = int(os.getenv('PIPELINE_TELEGRAM_POLL_SECONDS', '20'))
        self.manual_deadline_local = os.getenv('PIPELINE_MANUAL_DEADLINE_LOCAL', '14:00').strip() or '14:00'
        self.manual_deadline_mode = os.getenv('PIPELINE_MANUAL_DEADLINE_MODE', 'run_date').strip().lower()
        self.manual_window_minutes = int(os.getenv('PIPELINE_MANUAL_WINDOW_MINUTES', '120'))
        self.manual_match_strict = env_bool('PIPELINE_MANUAL_MATCH_STRICT')

        self.session_file = self.output_dir / 'manual_session.json'
        self.deadline_dt, self.deadline_reason = self._build_deadline_dt()
        # Tracks whether media came from API generation or manual Telegram upload.
        self.asset_source = 'auto_api'
        self.run_token = uuid.uuid4().hex[:12]
        self.daily_run = DailyRunStateManager(
            run_date=self.run_date,
            timezone_name=self.pipeline_timezone,
            run_token=self.run_token,
            stale_after=timedelta(minutes=20),
        )
        self._heartbeat_lock = threading.Lock()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = None
        self._heartbeat_status = 'STARTING'
        self._heartbeat_note = 'Pipeline initialized.'
        self._runtime_dirs_ready = False
        self.in_auto_fallback = False
        self.in_emergency_fallback = False
        self._last_emergency_fallback_error: str | None = None
        self._last_emergency_fallback_details: str | None = None
        self._last_emergency_fallback_classification: str | None = None
        self._active_emergency_fallback_version: str | None = None
        self._active_emergency_fallback_publish_mode: str | None = None

    def _normalize_mode(self, raw_mode: str) -> str:
        """Normalize mode - only 'automatic' and 'telegram' are valid."""
        if raw_mode not in ('automatic', 'telegram'):
            print(f"{Fore.YELLOW}⚠ Unknown PIPELINE_MODE={raw_mode}. Use 'automatic' or 'telegram'.{Style.RESET_ALL}")
            sys.exit(1)
        return raw_mode

    def _build_deadline_dt(self) -> tuple[datetime, str]:
        return get_pipeline_deadline(now=self._now())

    def _now(self) -> datetime:
        return datetime.now(self.tz)

    def _ensure_owner_runtime_dirs(self):
        if self._runtime_dirs_ready:
            return
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._runtime_dirs_ready = True

    def _cleanup_non_authoritative_daily_state(self):
        if self.post_to_instagram:
            return

        try:
            self.daily_run.release_claim()
        except Exception:
            pass

        try:
            self.daily_run.delete_state()
        except Exception:
            pass

    def _current_generation_status(self) -> str:
        return 'AUTO_FALLBACK_RUNNING' if self.in_auto_fallback else 'STARTING'

    def _is_terminal_rescue_run(self) -> bool:
        deadline = getattr(self, 'deadline_dt', None)
        try:
            current_time = self._now()
        except Exception:
            current_time = None
        try:
            return infer_terminal_rescue_run(current_time, deadline=deadline)
        except Exception:
            return False

    def _failure_classification_for_stage(self, stage: str) -> str:
        normalized = (stage or '').strip().lower()
        if normalized == 'validation':
            return 'validation'
        if normalized in {'whoop data unavailable', 'lookup retry exhausted'}:
            return 'lookup_not_ready'
        if normalized == 'data retrieve & dasha lookups':
            return 'lookup_failure'
        if normalized in {'llm prompts (interpretation -> video)', 'prompt output loading', 'image generation', 'video generation'}:
            return 'generation'
        if normalized in {'render static card', 'render animated card'}:
            return 'render'
        if normalized == 'vps upload':
            return 'upload'
        if normalized in {'instagram token preflight', 'instagram token', 'instagram media preflight', 'instagram posting'}:
            return 'instagram_main_post'
        if normalized == 'emergency post fallback':
            return 'fallback_publish_failed'
        return 'pre_post_failure'

    def _emergency_failure_classification(self) -> str:
        classification = (self._last_emergency_fallback_classification or '').strip().lower()
        if classification in {'manifest_invalid', 'asset_integrity_failed'}:
            return 'fallback_unavailable'
        return 'fallback_publish_failed'

    def _set_heartbeat_context(self, *, status: str | None = None, note: str | None = None, pulse: bool = False):
        if self.daily_run.is_owner():
            current_status = self.daily_run.current_status()
            if current_status in {'POSTED', 'FAILED_FATAL'} and status and status != current_status:
                return

        with self._heartbeat_lock:
            if status:
                self._heartbeat_status = status
            if note:
                self._heartbeat_note = note
        if pulse:
            try:
                self._pulse_heartbeat()
            except OwnershipLostError as e:
                self.log_error('Daily Ownership', str(e))

    def _pulse_heartbeat(self):
        if not self.daily_run.is_owner():
            return
        with self._heartbeat_lock:
            status = self._heartbeat_status
            note = self._heartbeat_note
        self.daily_run.heartbeat(status=status, note=note)

    def _heartbeat_loop(self):
        while not self._heartbeat_stop.wait(60):
            try:
                self._pulse_heartbeat()
            except OwnershipLostError as e:
                print(f"{Fore.YELLOW}⚠ Lost daily ownership during heartbeat: {e}{Style.RESET_ALL}")
                return
            except SystemExit:
                return
            except Exception as e:
                print(f"{Fore.YELLOW}⚠ Heartbeat update failed: {e}{Style.RESET_ALL}")

    def _start_heartbeat_thread(self):
        if self._heartbeat_thread is not None:
            return
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f'daily-run-heartbeat-{self.run_date}',
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _stop_heartbeat_thread(self):
        self._heartbeat_stop.set()
        thread = self._heartbeat_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)
        self._heartbeat_thread = None

    def _claim_daily_run_or_exit(self):
        decision, state = self.daily_run.acquire()
        if decision == 'owner':
            self._set_heartbeat_context(
                status='STARTING',
                note=f'Claimed daily run ownership (token {self.run_token}).',
                pulse=True,
            )
            self._start_heartbeat_thread()
            return

        status = (state or {}).get('status', 'UNKNOWN')
        if decision == 'skip_posted':
            self._recover_posted_archive_if_needed(state or {})
            print(f"{Fore.YELLOW}⚠ Daily run already POSTED for {self.run_date}. Exiting cleanly.{Style.RESET_ALL}")
            raise SystemExit(0)
        if decision == 'skip_failed_fatal':
            print(
                f"{Fore.YELLOW}⚠ Daily run is blocked by FAILED_FATAL for {self.run_date}. "
                f"Manual intervention required.{Style.RESET_ALL}"
            )
            raise SystemExit(0)

        print(
            f"{Fore.YELLOW}⚠ Another active run already owns {self.run_date} "
            f"(status={status}). Exiting cleanly.{Style.RESET_ALL}"
        )
        raise SystemExit(0)

    def _recover_posted_archive_if_needed(self, state: dict):
        if not self.post_to_instagram:
            return

        payload_path = self.output_dir / 'last_archived_payload.json'
        has_card = False
        card_check_error = None

        try:
            from database_manager import CardDatabase

            has_card = CardDatabase().has_card_for_date(self.run_date)
        except Exception as e:
            card_check_error = str(e)

        if payload_path.exists() and has_card:
            return

        try:
            daily_data = json.loads((self.output_dir / 'daily_data.json').read_text(encoding='utf-8'))
            metadata = json.loads((self.output_dir / 'card_metadata.json').read_text(encoding='utf-8'))
            image_json = json.loads((self.output_dir / 'image_prompt.json').read_text(encoding='utf-8'))
            blend_option, creature, environment = self._load_required_text_outputs()
            final_png = self.output_dir / 'card_final.png'
            final_mp4 = self.output_dir / 'card_final.mp4'
            if not final_png.exists():
                raise FileNotFoundError(f'Missing archived asset: {final_png}')
            if not final_mp4.exists():
                raise FileNotFoundError(f'Missing archived asset: {final_mp4}')

            post_id = state.get('instagram_post_id') or 'unknown'
            instagram_permalink = state.get('instagram_permalink')
            self.step_15_archive(
                daily_data,
                metadata,
                final_png,
                final_mp4,
                image_json,
                post_id,
                instagram_permalink,
                blend_option,
                creature,
                environment,
            )
            print(
                f"{Fore.GREEN}✅ Recovered archive/database artifacts for already-posted day {self.run_date}{Style.RESET_ALL}"
            )
        except Exception as e:
            details = self._merge_details(
                card_check_error,
                ''.join(traceback.format_exception(type(e), e, e.__traceback__))[-2000:],
            )
            self._notify_post_success_cleanup_warning(
                'Post Publish Recovery',
                'Daily run was already POSTED, but archive/database recovery could not be completed.',
                details,
            )

    def _recover_posted_state_after_publish(
        self,
        *,
        post_id: str | None,
        permalink: str | None,
        note: str,
        details_tail: str | None = None,
    ):
        try:
            self.daily_run.mark_posted_after_publish(
                post_id=post_id,
                permalink=permalink,
                note=note,
            )
        except Exception as recovery_error:
            self._notify_post_success_cleanup_warning(
                'Post Publish State Sync',
                'Instagram publish succeeded, but POSTED state recovery failed. Manual intervention may be required to avoid reposts.',
                self._merge_details(details_tail, str(recovery_error)),
            )
            return

        self._notify_post_success_cleanup_warning(
            'Post Publish State Sync',
            'Instagram publish succeeded after daily ownership moved. Forced POSTED state recovery to prevent duplicate reposts.',
            details_tail,
        )

    def _mark_posted_terminal_success(self, post_id: str | None, permalink: str | None, note: str):
        with self._heartbeat_lock:
            self._heartbeat_status = 'POSTED'
            self._heartbeat_note = note
        if not self.daily_run.is_owner():
            self._recover_posted_state_after_publish(
                post_id=post_id,
                permalink=permalink,
                note=note,
                details_tail='Daily run ownership was already lost when syncing POSTED state after Instagram publish.',
            )
            return
        try:
            self.daily_run.mark_posted(post_id=post_id, permalink=permalink, note=note)
        except OwnershipLostError as e:
            self._recover_posted_state_after_publish(
                post_id=post_id,
                permalink=permalink,
                note=note,
                details_tail=str(e),
            )

    def _release_retryable_lookup_failure(
        self,
        *,
        retry_message: str,
        rescue_stage: str,
        rescue_message: str,
        notifier_step: str,
        notifier_message: str,
        details_tail: str | None = None,
        failure_classification: str = 'lookup_not_ready',
    ):
        self._stop_heartbeat_thread()
        if self._is_terminal_rescue_run():
            raise PipelineStageError(
                stage=rescue_stage,
                message=rescue_message,
                details=details_tail,
                fallback_eligible=True,
                failure_classification=failure_classification,
            )

        retry_cleanup_notes: list[str] = []
        try:
            self.daily_run.mark_retryable_failure(
                step='Data Retrieve & Dasha Lookups',
                message=retry_message,
                details_tail=details_tail,
                failure_classification=failure_classification,
            )
        except Exception as e:
            retry_cleanup_notes.append(f'Failed to record FAILED_RETRYABLE state: {e}')
        finally:
            try:
                self.daily_run.release_claim()
            except Exception as e:
                retry_cleanup_notes.append(f'Failed to release claim: {e}')

        warning_details = details_tail
        if retry_cleanup_notes:
            cleanup_tail = '\n'.join(retry_cleanup_notes)
            warning_details = f"{details_tail}\n\n{cleanup_tail}" if details_tail else cleanup_tail

        notifier = get_notifier()
        notifier.notify_warning(
            run_date=self.run_date,
            step=notifier_step,
            message=notifier_message,
            details_tail=warning_details,
        )
        if retry_cleanup_notes:
            print(f"{Fore.YELLOW}⚠ {' | '.join(retry_cleanup_notes)}{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}⚠ {retry_message}{Style.RESET_ALL}")
        raise SystemExit(0)

    def _handle_retryable_lookup_not_ready(self, details_tail: str | None = None):
        self._release_retryable_lookup_failure(
            retry_message='WHOOP daily data for today is not ready yet. Releasing claim for next cron retry.',
            rescue_stage='WHOOP Data Unavailable',
            rescue_message='WHOOP daily data never became ready. Terminal rescue run triggering emergency fallback.',
            notifier_step='WHOOPRecoveryNotReady',
            notifier_message='WHOOP daily data not ready yet. This run was released for the next cron retry.',
            details_tail=details_tail,
            failure_classification='lookup_not_ready',
        )

    def _handle_retryable_lookup_external_failure(self, details_tail: str | None = None):
        self._release_retryable_lookup_failure(
            retry_message='Transient WHOOP or lookup failure encountered. Releasing claim for next cron retry.',
            rescue_stage='Lookup Retry Exhausted',
            rescue_message='Transient WHOOP or lookup failure persisted into the terminal rescue run. Triggering emergency fallback.',
            notifier_step='LookupRetryableFailure',
            notifier_message='Transient WHOOP or lookup failure. This run was released for the next cron retry.',
            details_tail=details_tail,
            failure_classification='lookup_not_ready',
        )

    def _release_retryable_stage_failure(self, exc: PipelineStageError):
        self._stop_heartbeat_thread()
        classification = exc.failure_classification or self._failure_classification_for_stage(exc.stage)
        retry_message = f'{exc.message} Releasing claim for next cron retry.'

        retry_cleanup_notes: list[str] = []
        try:
            self.daily_run.mark_retryable_failure(
                step=exc.stage,
                message=retry_message,
                details_tail=exc.details,
                failure_classification=classification,
            )
        except Exception as e:
            retry_cleanup_notes.append(f'Failed to record FAILED_RETRYABLE state: {e}')
        finally:
            try:
                self.daily_run.release_claim()
            except Exception as e:
                retry_cleanup_notes.append(f'Failed to release claim: {e}')

        warning_details = exc.details
        if retry_cleanup_notes:
            cleanup_tail = '\n'.join(retry_cleanup_notes)
            warning_details = f"{exc.details}\n\n{cleanup_tail}" if exc.details else cleanup_tail

        notifier = get_notifier()
        notifier.notify_warning(
            run_date=self.run_date,
            step=exc.stage,
            message=f'{exc.message} This run was released for the next cron retry.',
            details_tail=warning_details,
        )
        if retry_cleanup_notes:
            print(f"{Fore.YELLOW}⚠ {' | '.join(retry_cleanup_notes)}{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}⚠ {retry_message}{Style.RESET_ALL}")
        raise SystemExit(0)

    def _extract_validation_fatal_error(self, result: subprocess.CompletedProcess) -> tuple[str, str] | None:
        marker = 'EMERGENCY_FALLBACK_UNAVAILABLE:'
        combined_output = '\n'.join(part for part in (result.stdout, result.stderr) if part)
        for line in combined_output.splitlines():
            if marker not in line:
                continue
            message = line.split(marker, 1)[1].strip() or 'Emergency fallback package is unavailable.'
            return message, combined_output.strip() or message
        return None

    def log_error(
        self,
        step_name: str,
        error_msg: str,
        details_tail: str = None,
        failure_classification: str | None = None,
    ):
        if self.post_to_instagram and self.daily_run.is_owner():
            try:
                self.daily_run.mark_fatal_failure(
                    step=step_name,
                    message=error_msg,
                    details_tail=details_tail,
                    failure_classification=failure_classification or self._failure_classification_for_stage(step_name),
                )
            except OwnershipLostError:
                pass

        # Write to error log
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with open(self.output_dir / 'pipeline_error.log', 'a', encoding='utf-8') as f:
            f.write(f"[{step_name}] ERROR: {error_msg}\n")
        
        print(f"{Fore.RED}❌ {step_name} failed: {error_msg}{Style.RESET_ALL}")
        
        # Send Telegram error notification
        notifier = get_notifier()
        notifier.notify_error(
            run_date=self.run_date,
            step=step_name,
            error_type='PipelineError',
            message=error_msg,
            details_tail=details_tail,
            fatal=True
        )
        
        sys.exit(1)

    def _merge_details(self, *details_parts: str | None) -> str | None:
        parts = [part.strip() for part in details_parts if part and part.strip()]
        if not parts:
            return None
        return '\n\n'.join(parts)

    def _coerce_publish_diagnostics(self, details_obj: object | None, details: str | None) -> dict | str | None:
        if isinstance(details_obj, dict):
            return details_obj
        if details:
            try:
                parsed = json.loads(details)
            except Exception:
                return details
            if isinstance(parsed, dict):
                return parsed
        return details

    def _write_emergency_fallback_publish_failure_log(
        self,
        manager,
        *,
        trigger_stage: str,
        reason: str,
        publish_mode: str | None,
        video_url: str | None,
        thumb_url: str | None,
        publish_diagnostics: dict | str | None,
    ) -> None:
        try:
            manager.write_emergency_log(
                self.output_dir,
                trigger_stage=trigger_stage,
                reason=reason,
                publish_mode=publish_mode,
                video_url=video_url or '',
                thumb_url=thumb_url or '',
                instagram_post_id=None,
                instagram_permalink=None,
                reused_existing_post=False,
                publish_status='failed',
                publish_diagnostics=publish_diagnostics,
            )
        except Exception as e:
            notifier = get_notifier()
            notifier.notify_warning(
                run_date=self.run_date,
                step='Emergency Fallback Archive',
                message='Emergency fallback failed, and emergency_fallback_used.json could not be written.',
                details_tail=str(e),
            )

    def _build_subprocess_details_tail(self, result: subprocess.CompletedProcess) -> str | None:
        """
        Build stderr/stdout tail using TELEGRAM_NOTIFY_INCLUDE_STDERR_LINES.
        Prefers stderr first, but includes both when available.
        """
        notifier = get_notifier()
        max_lines = max(1, int(getattr(notifier, 'include_stderr_lines', 40)))

        stderr_lines = (result.stderr or '').splitlines()
        stdout_lines = (result.stdout or '').splitlines()

        chunks = []
        if stderr_lines:
            chunks.append('[STDERR]\n' + '\n'.join(stderr_lines[-max_lines:]))
        if stdout_lines:
            chunks.append('[STDOUT]\n' + '\n'.join(stdout_lines[-max_lines:]))

        if not chunks:
            return None
        return '\n\n'.join(chunks)

    @staticmethod
    def _is_media_like_content_type(content_type: str, media_kind: str) -> bool:
        normalized = (content_type or "").split(";", 1)[0].strip().lower()
        if not normalized:
            return False
        if normalized in {"application/octet-stream", "binary/octet-stream"}:
            return True
        if media_kind == "video":
            return normalized.startswith("video/")
        return normalized.startswith("image/")

    def _set_emergency_fallback_failure(
        self,
        classification: str,
        message: str,
        details: str | None = None,
    ):
        self._last_emergency_fallback_classification = classification
        self._last_emergency_fallback_error = f'[{classification}] {message}'
        self._last_emergency_fallback_details = details or message

    def safe_step(
        self,
        step_name: str,
        script_path: str,
        args: list = None,
        status: str = 'STARTING',
        fallback_eligible: bool = False,
        env_overrides: dict | None = None,
    ):
        try:
            print(f"{Fore.CYAN}▶ Running {step_name}...{Style.RESET_ALL}")
            self._set_heartbeat_context(status=status, note=f'Running {step_name}.', pulse=True)
            cmd = [sys.executable, str(self.base_dir / script_path)]
            if args:
                cmd.extend(args)
            env = os.environ.copy()
            if env_overrides:
                env.update(env_overrides)
            result = subprocess.run(cmd, capture_output=True, text=True, env=env)
            if result.stdout:
                print(f"{Fore.LIGHTBLACK_EX}   STDOUT: {result.stdout.strip()}{Style.RESET_ALL}")
            if result.returncode != 0:
                print(f"{Fore.RED}Script Error STDOUT:\n{result.stdout}{Style.RESET_ALL}")
                print(f"{Fore.RED}Script Error STDERR:\n{result.stderr}{Style.RESET_ALL}")

                details_tail = self._build_subprocess_details_tail(result)
                if fallback_eligible:
                    raise PipelineStageError(
                        stage=step_name,
                        message=f'Script exited with code {result.returncode}',
                        details=details_tail,
                        fallback_eligible=True,
                    )
                self.log_error(step_name, f'Script exited with code {result.returncode}', details_tail)
            print(f"{Fore.GREEN}✅ {step_name} completed{Style.RESET_ALL}")
            self._set_heartbeat_context(status=status, note=f'Completed {step_name}.', pulse=True)
            return result.stdout
        except PipelineStageError:
            raise
        except Exception as e:
            if fallback_eligible:
                raise PipelineStageError(
                    stage=step_name,
                    message=str(e),
                    details=str(e),
                    fallback_eligible=True,
                ) from e
            self.log_error(step_name, str(e), str(e))

    def _handle_runtime_stage_error(self, exc: PipelineStageError) -> bool:
        if exc.fallback_eligible and self.post_to_instagram and not self.in_emergency_fallback:
            if self._is_terminal_rescue_run():
                if env_bool('EMERGENCY_FALLBACK_ENABLED', default=False):
                    notifier = get_notifier()
                    notifier.notify_warning(
                        run_date=self.run_date,
                        step=exc.stage,
                        message=f"{exc.message} Emergency fallback will be attempted.",
                        details_tail=exc.details,
                    )
                    if self._run_emergency_fallback(exc.stage, exc.message):
                        print(f"{Fore.GREEN}🎉 Pipeline completed via emergency post fallback! 🎉{Style.RESET_ALL}")
                        return True

                    fatal_message = self._last_emergency_fallback_error or exc.message
                    fatal_details = self._merge_details(exc.details, self._last_emergency_fallback_details)
                    self.log_error(
                        'Emergency Post Fallback',
                        fatal_message,
                        fatal_details,
                        failure_classification=self._emergency_failure_classification(),
                    )
                    return False
            else:
                self._release_retryable_stage_failure(exc)

        self.log_error(
            exc.stage,
            exc.message,
            exc.details,
            failure_classification=exc.failure_classification,
        )
        return False

    def _build_caption_or_raise(self, metadata: dict, daily_data: dict) -> str:
        try:
            return self.step_13_build_caption(metadata, daily_data)
        except PipelineStageError:
            raise
        except Exception as e:
            raise PipelineStageError(
                stage='Caption Build',
                message='Caption building failed unexpectedly.',
                details=''.join(traceback.format_exception(type(e), e, e.__traceback__))[-2000:],
                fallback_eligible=True,
            ) from e

    def _notify_post_success_cleanup_warning(self, step: str, message: str, details_tail: str | None = None):
        print(f"{Fore.YELLOW}⚠ {message}{Style.RESET_ALL}")
        notifier = get_notifier()
        notifier.notify_warning(
            run_date=self.run_date,
            step=step,
            message=message,
            details_tail=details_tail,
        )

    def _coerce_unexpected_runtime_error(self, exc: Exception) -> PipelineStageError:
        details = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-2000:]
        return PipelineStageError(
            stage='Unhandled Runtime Pre-Post',
            message='Unexpected runtime failure before Instagram post completion.',
            details=details,
            fallback_eligible=True,
            failure_classification='pre_post_failure',
        )

    def run(self):
        try:
            self._claim_daily_run_or_exit()
            self._ensure_owner_runtime_dirs()
            # Set up global exception handler for uncaught errors after ownership is established.
            _setup_global_exception_handler(self)

            print(f"{Fore.MAGENTA}=== STARTING STATE ZERO PIPELINE ==={Style.RESET_ALL}")
            print(
                f"{Fore.LIGHTBLACK_EX}Mode: {self.mode} | "
                f"Post to Instagram: {self.post_to_instagram} "
                f"(PIPELINE_POST_TO_INSTAGRAM={str(self.post_to_instagram).lower()}) | "
                f"Media mode: {self.media_mode} | Run token: {self.run_token}{Style.RESET_ALL}"
            )

            try:
                self.step_1_validate()

                # Only validate Instagram token when posting is enabled
                if self.post_to_instagram:
                    self.step_1b_validate_instagram_token()
                else:
                    print(f"{Fore.YELLOW}⚠ Dry-run mode: skipping Instagram token validation.{Style.RESET_ALL}")

                daily_data = self.step_2_3_lookups()
                self.step_4_6_prompts()

                image_json = self._load_required_json(self.output_dir / 'image_prompt.json', 'image_prompt.json')
                metadata = self._load_required_json(self.output_dir / 'card_metadata.json', 'card_metadata.json')
                blend_option, creature, environment = self._load_required_text_outputs()

                if self.mode == 'telegram':
                    # In telegram mode: always do manual wait + fallback (not skipped in dry run)
                    art_path, video_path = self.step_7_9_manual_or_fallback(image_json)
                else:
                    # Automatic mode: just generate
                    art_path = self.step_7_generate_image(image_json)
                    video_path = self.step_9_generate_video(art_path, self.output_dir / 'video_prompt.txt')

                final_png = self.step_10a_render_image(art_path, daily_data, metadata)
                final_mp4 = self.step_10b_render_video(video_path, daily_data, metadata)

                # Only upload to VPS and post to Instagram when enabled
                if self.post_to_instagram:
                    video_url, thumb_url = self.step_12_upload_vps(final_mp4, final_png)
                else:
                    print(f"{Fore.YELLOW}⚠ Dry-run mode: skipping VPS upload.{Style.RESET_ALL}")
                    video_url, thumb_url = None, None

                caption = self._build_caption_or_raise(metadata, daily_data)

                # Only post to Instagram when enabled
                if self.post_to_instagram:
                    post_result = self.step_14_post_instagram(video_url, thumb_url, caption)
                else:
                    print(f"{Fore.YELLOW}⚠ Dry-run mode: skipping Instagram post.{Style.RESET_ALL}")
                    post_result = None

                if isinstance(post_result, dict) and post_result.get('already_posted'):
                    print(f"{Fore.YELLOW}⚠ State already marked POSTED. Skipping archive/database work.{Style.RESET_ALL}")
                    return

                if isinstance(post_result, dict):
                    post_id = post_result.get('post_id', 'unknown')
                    instagram_permalink = post_result.get('permalink')
                else:
                    post_id = 'dry-run'
                    instagram_permalink = None

                print(f"{Fore.CYAN}▶ Archiving Data and Updating Database...{Style.RESET_ALL}")
                self.step_15_archive(
                    daily_data,
                    metadata,
                    final_png,
                    final_mp4,
                    image_json,
                    post_id,
                    instagram_permalink,
                    blend_option,
                    creature,
                    environment,
                )

                # Send dry-run completion notification if not posting
                if not self.post_to_instagram:
                    notifier = get_notifier()
                    notifier.notify_dry_run_complete(self.run_date, self.mode, self.output_dir)

                print(f"{Fore.GREEN}🎉 Pipeline completed successfully! 🎉{Style.RESET_ALL}")
            except PipelineStageError as exc:
                if self._handle_runtime_stage_error(exc):
                    return
            except Exception as exc:
                if self.daily_run.current_status() == 'POSTED':
                    self._notify_post_success_cleanup_warning(
                        'Post Success Cleanup',
                        'Instagram post succeeded, but later cleanup failed. Leaving daily state as POSTED.',
                        ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-2000:],
                    )
                    return
                if self._handle_runtime_stage_error(self._coerce_unexpected_runtime_error(exc)):
                    return
        finally:
            self._stop_heartbeat_thread()
            self._cleanup_non_authoritative_daily_state()

    def _load_required_json(self, path: Path, label: str) -> dict:
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            raise PipelineStageError(
                stage='Prompt Output Loading',
                message=f'{label} not found - prompts.py failed',
                fallback_eligible=True,
            )
        except json.JSONDecodeError as e:
            raise PipelineStageError(
                stage='Prompt Output Loading',
                message=f'{label} is invalid JSON: {e}',
                details=str(e),
                fallback_eligible=True,
            ) from e

    def _load_required_text_outputs(self) -> tuple[str, str, str]:
        try:
            blend_option = (self.output_dir / 'blend_option.txt').read_text(encoding='utf-8').strip()
            # Selected outputs are canonical downstream handoff files; raw outputs remain for debugging.
            creature_path = self.output_dir / 'creature_selected.txt'
            if not creature_path.exists():
                creature_path = self.output_dir / 'creature.txt'
            creature = creature_path.read_text(encoding='utf-8').strip()
            environment_path = self.output_dir / 'environment_selected.txt'
            if not environment_path.exists():
                environment_path = self.output_dir / 'environment.txt'
            environment = environment_path.read_text(encoding='utf-8').strip()
            return blend_option, creature, environment
        except FileNotFoundError as e:
            raise PipelineStageError(
                stage='Prompt Output Loading',
                message=f'Missing prompt output file: {e.filename}',
                details=str(e),
                fallback_eligible=True,
            ) from e

    def _load_or_init_manual_session(self) -> dict:
        if self.session_file.exists():
            try:
                session = json.loads(self.session_file.read_text(encoding='utf-8'))
                session = self._migrate_manual_session(session)
                if self._is_session_reusable(session):
                    return session
                print(f"{Fore.YELLOW}⚠ Existing manual session is stale/terminal. Starting a fresh session.{Style.RESET_ALL}")
            except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
                corrupt_backup = self.session_file.with_suffix('.corrupt.json')
                backup_saved = False
                backup_error = None
                try:
                    shutil.copy2(self.session_file, corrupt_backup)
                    backup_saved = True
                except OSError as copy_error:
                    backup_error = str(copy_error)
                backup_status = (
                    f'Backup saved to {corrupt_backup.name}'
                    if backup_saved
                    else f'Backup could not be saved ({backup_error or "unknown error"})'
                )
                print(
                    f"{Fore.YELLOW}⚠ Existing manual session is corrupt or unreadable "
                    f"({e}). Starting a fresh session. {backup_status}{Style.RESET_ALL}"
                )
                notifier = get_notifier()
                notifier.notify_warning(
                    run_date=self.run_date,
                    step='ManualSessionLoad',
                    message=(
                        f'manual_session.json was corrupt or unreadable ({e}). Starting a fresh session. '
                        'last_update_id and asset refs may be lost, so watch for duplicate Telegram '
                        f'prompts or missed manual asset recovery. {backup_status}.'
                    ),
                    details_tail=(
                        f'backup={corrupt_backup}'
                        if backup_saved
                        else f'backup_copy_failed path={corrupt_backup} error={backup_error or "unknown error"}'
                    ),
                )

        last_update_id = self._get_latest_update_id()
        session = {
            'run_date': self.run_date,
            'mode': 'telegram',
            'run_token': self.run_token,
            'status': 'WAITING_MANUAL',
            'timezone': self.pipeline_timezone,
            'deadline_local': self.deadline_dt.isoformat(),
            'deadline_mode': self.manual_deadline_mode,
            'deadline_reason': self.deadline_reason,
            'created_at': self._now().isoformat(),
            'telegram': {
                'last_update_id': last_update_id,
                'prompt_message_id': None,
                'accepted_reply_message_ids': [],
                'image_file_id': None,
                'image_file_name': None,
                'image_message_id': None,
                'video_file_id': None,
                'video_file_name': None,
                'video_message_id': None,
            },
        }
        self._save_manual_session(session)
        return session

    def _migrate_manual_session(self, session: dict) -> dict:
        if not isinstance(session, dict):
            raise TypeError('manual_session root must be a JSON object')
        tg = session.get('telegram')
        if tg is None:
            tg = {}
            session['telegram'] = tg
        elif not isinstance(tg, dict):
            raise TypeError('manual_session.telegram must be a JSON object')
        tg.setdefault('last_update_id', 0)
        tg.setdefault('prompt_message_id', None)
        reply_ids = tg.get('accepted_reply_message_ids')
        if not isinstance(reply_ids, list):
            reply_ids = []
        if tg.get('prompt_message_id'):
            try:
                reply_ids.append(int(tg['prompt_message_id']))
            except (ValueError, TypeError):
                pass
        # Preserve order while deduping.
        tg['accepted_reply_message_ids'] = list(dict.fromkeys(reply_ids))
        tg.setdefault('image_file_id', None)
        tg.setdefault('image_file_name', None)
        tg.setdefault('image_message_id', None)
        tg.setdefault('video_file_id', None)
        tg.setdefault('video_file_name', None)
        tg.setdefault('video_message_id', None)
        return session

    def _is_session_reusable(self, session: dict) -> bool:
        owner_state = self.daily_run.load_state() or {}
        if owner_state.get('run_token') != self.run_token:
            return False
        if session.get('run_date') != self.run_date:
            return False
        if session.get('run_token') != self.run_token:
            return False
        status = (session.get('status') or '').strip().upper()
        if status in ('COMPLETED', 'AUTO_FALLBACK_RUNNING'):
            return False
        if status and status != 'WAITING_MANUAL':
            return False
        session_deadline = self._get_session_deadline(session)
        return self._now() < session_deadline

    def _write_json_atomic(self, path: Path, payload: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                'w',
                encoding='utf-8',
                dir=path.parent,
                delete=False,
            ) as handle:
                json.dump(payload, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
                tmp_path = handle.name
            os.replace(tmp_path, path)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _save_manual_session(self, session: dict):
        self._write_json_atomic(self.session_file, session)

    def _telegram_api(self, method: str, *, data=None, files=None, timeout=60, use_get=False) -> dict:
        url = f'https://api.telegram.org/bot{self.bot_token}/{method}'
        if use_get:
            resp = requests.get(url, params=data or {}, timeout=timeout)
        else:
            resp = requests.post(url, data=data or {}, files=files, timeout=timeout)

        payload = resp.json()
        if not payload.get('ok'):
            raise RuntimeError(f'Telegram API {method} failed: {payload}')
        return payload

    def _get_latest_update_id(self) -> int:
        try:
            payload = self._telegram_api(
                'getUpdates',
                data={'timeout': 0, 'allowed_updates': json.dumps(['message'])},
                use_get=True,
                timeout=30,
            )
            updates = payload.get('result', [])
            if updates:
                return int(updates[-1].get('update_id', 0))
        except Exception as e:
            print(f"{Fore.YELLOW}⚠ Could not fetch Telegram update cursor: {e}{Style.RESET_ALL}")
        return 0

    def _send_telegram_message(self, text: str) -> int | None:
        payload = self._telegram_api('sendMessage', data={'chat_id': self.chat_id, 'text': text})
        return payload.get('result', {}).get('message_id')

    def _send_long_telegram_text(self, text: str, header: str | None = None) -> list[int]:
        """
        Telegram message text is capped at ~4096 chars.
        Chunk long prompts so they're easy to copy/paste from chat.
        """
        chunk_size = 3500
        body = text.strip()
        if header:
            body = f"{header}\n\n{body}"
        if not body:
            return []

        chunks = [body[i:i + chunk_size] for i in range(0, len(body), chunk_size)]
        total = len(chunks)
        sent_message_ids: list[int] = []
        for idx, chunk in enumerate(chunks, start=1):
            prefix = f"[{idx}/{total}] " if total > 1 else ""
            message_id = self._send_telegram_message(f"{prefix}{chunk}")
            if message_id:
                sent_message_ids.append(int(message_id))
        return sent_message_ids

    def _send_telegram_document(self, path: Path, caption: str):
        with open(path, 'rb') as f:
            self._telegram_api(
                'sendDocument',
                data={'chat_id': self.chat_id, 'caption': caption},
                files={'document': f},
            )

    def _send_manual_instruction_packet(self, session: dict):
        run_token = session['run_token']
        image_prompt_text = (self.output_dir / 'image_prompt.json').read_text(encoding='utf-8')
        video_prompt_text = (self.output_dir / 'video_prompt.txt').read_text(encoding='utf-8')
        session_deadline = self._get_session_deadline(session)
        msg = (
            f"State Zero manual window for {self.run_date} (token: {run_token})\n"
            f"Deadline: {session_deadline.strftime('%Y-%m-%d %H:%M %Z')}\n\n"
            "Please reply with BOTH files as Telegram documents:\n"
            "1) Image (.png/.jpg)\n"
            "2) Video (.mp4)\n\n"
            "You can reply to this message OR any prompt chunk below.\n"
            "If both files are not received before deadline, pipeline auto-falls back to full auto mode."
        )

        message_id = self._send_telegram_message(msg)
        # Send copy-paste friendly prompt text directly in chat (text-only flow).
        image_message_ids = self._send_long_telegram_text(image_prompt_text)
        video_message_ids = self._send_long_telegram_text(video_prompt_text)

        session['telegram']['prompt_message_id'] = message_id
        accepted_reply_ids = []
        if message_id:
            accepted_reply_ids.append(int(message_id))
        accepted_reply_ids.extend(image_message_ids)
        accepted_reply_ids.extend(video_message_ids)
        session['telegram']['accepted_reply_message_ids'] = list(dict.fromkeys(accepted_reply_ids))
        session['instruction_sent_at'] = self._now().isoformat()
        self._save_manual_session(session)

    def _fetch_telegram_updates(self, session: dict) -> list[dict]:
        last_update_id = int(session['telegram'].get('last_update_id') or 0)
        payload = self._telegram_api(
            'getUpdates',
            data={
                'offset': last_update_id + 1,
                'timeout': 0,
                'allowed_updates': json.dumps(['message']),
            },
            use_get=True,
            timeout=30,
        )

        updates = payload.get('result', [])
        if updates:
            session['telegram']['last_update_id'] = updates[-1]['update_id']
            self._save_manual_session(session)
        return updates

    def _extract_manual_assets_from_updates(self, session: dict, updates: list[dict]):
        tg = session['telegram']
        run_token = session.get('run_token', '').strip().lower()
        prompt_message_id = tg.get('prompt_message_id')
        accepted_reply_ids = {int(mid) for mid in tg.get('accepted_reply_message_ids', []) if mid}
        if prompt_message_id:
            try:
                accepted_reply_ids.add(int(prompt_message_id))
            except Exception:
                pass
        instruction_sent_at = session.get('instruction_sent_at')
        instruction_epoch = 0
        if instruction_sent_at:
            try:
                instruction_epoch = int(datetime.fromisoformat(instruction_sent_at).timestamp())
            except Exception:
                instruction_epoch = 0

        def _is_relevant_message(message: dict) -> bool:
            # Preferred: user replies directly to the instruction message.
            reply_to = message.get('reply_to_message') or {}
            reply_to_id = reply_to.get('message_id')
            try:
                if reply_to_id and int(reply_to_id) in accepted_reply_ids:
                    return True
            except Exception:
                pass

            if prompt_message_id and reply_to_id == prompt_message_id:
                return True

            # Secondary: user includes run token in caption/text.
            text = (message.get('text') or '').lower()
            caption = (message.get('caption') or '').lower()
            if run_token and (run_token in text or run_token in caption):
                return True

            # Relaxed default for single-user bots: accept fresh uploads during active manual window.
            if not self.manual_match_strict:
                msg_date = int(message.get('date') or 0)
                has_asset = bool(message.get('document') or message.get('video') or message.get('photo'))
                if has_asset and instruction_epoch and msg_date >= instruction_epoch:
                    return True

            return False

        for update in updates:
            message = update.get('message') or {}
            chat = message.get('chat') or {}
            if str(chat.get('id')) != str(self.chat_id):
                continue
            if not _is_relevant_message(message):
                continue

            document = message.get('document')
            if document:
                file_id = document.get('file_id')
                file_name = (document.get('file_name') or '').lower()
                mime_type = (document.get('mime_type') or '').lower()
                message_id = message.get('message_id')

                if mime_type.startswith('image/') or file_name.endswith(('.png', '.jpg', '.jpeg', '.webp')):
                    tg['image_file_id'] = file_id
                    tg['image_file_name'] = file_name
                    tg['image_message_id'] = message_id
                    print(f"{Fore.GREEN}✅ Received manual image from Telegram.{Style.RESET_ALL}")
                    continue

                if mime_type.startswith('video/') or file_name.endswith('.mp4'):
                    tg['video_file_id'] = file_id
                    tg['video_file_name'] = file_name
                    tg['video_message_id'] = message_id
                    print(f"{Fore.GREEN}✅ Received manual video from Telegram.{Style.RESET_ALL}")
                    continue

            # Telegram 'video' uploads are accepted for convenience.
            video = message.get('video')
            if video and video.get('file_id'):
                tg['video_file_id'] = video['file_id']
                tg['video_file_name'] = f"telegram-video-{video['file_id']}.mp4"
                tg['video_message_id'] = message.get('message_id')
                print(f"{Fore.GREEN}✅ Received manual video from Telegram.{Style.RESET_ALL}")
                continue

            # Telegram photo uploads are accepted for convenience.
            photos = message.get('photo') or []
            if photos:
                best = photos[-1]
                if best.get('file_id'):
                    tg['image_file_id'] = best['file_id']
                    tg['image_file_name'] = f"telegram-photo-{best['file_id']}.jpg"
                    tg['image_message_id'] = message.get('message_id')
                    print(f"{Fore.GREEN}✅ Received manual image from Telegram photo upload.{Style.RESET_ALL}")

        self._save_manual_session(session)

    def _download_telegram_file(self, file_id: str, out_path: Path):
        metadata = self._telegram_api('getFile', data={'file_id': file_id}).get('result', {})
        remote_path = metadata.get('file_path')
        if not remote_path:
            raise RuntimeError(f'Missing Telegram file path for file_id={file_id}')

        url = f'https://api.telegram.org/file/bot{self.bot_token}/{remote_path}'
        resp = requests.get(url, timeout=120)
        if resp.status_code >= 400:
            raise RuntimeError(f'Failed to download Telegram file ({resp.status_code})')

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(resp.content)

    def _validate_image_file(self, image_path: Path):
        try:
            with Image.open(image_path) as img:
                w, h = img.size
                img.verify()
        except Exception as e:
            notifier = get_notifier()
            notifier.notify_warning(
                run_date=self.run_date,
                step='ManualImageValidation',
                message='Image invalid/unreadable. Please resend.',
                details_tail=str(e)[-500:]
            )
            raise RuntimeError('Manual image is invalid or unreadable.')

        if w < 900 or h < 1200:
            # Send warning notification for small image
            notifier = get_notifier()
            notifier.notify_warning(
                run_date=self.run_date,
                step='ManualImageValidation',
                message=f'Image too small ({w}x{h}). Minimum is 900x1200. Please resend.',
                details_tail=f'Dimensions: {w}x{h}'
            )
            raise RuntimeError(f'Manual image too small ({w}x{h}). Please send at least ~1080x1440 quality.')

    def _validate_video_file(self, video_path: Path):
        cmd = [
            'ffprobe',
            '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            str(video_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            # Send warning notification for invalid video
            notifier = get_notifier()
            notifier.notify_warning(
                run_date=self.run_date,
                step='ManualVideoValidation',
                message='Video invalid/unreadable. Please resend MP4.',
                details_tail=result.stderr[-500:]
            )
            raise RuntimeError('Manual video is invalid or ffprobe could not read it.')
        try:
            duration = float((result.stdout or '0').strip() or '0')
        except ValueError:
            duration = 0.0
        if duration < 1.0:
            # Send warning notification for short video
            notifier = get_notifier()
            notifier.notify_warning(
                run_date=self.run_date,
                step='ManualVideoValidation',
                message=f'Video duration too short ({duration:.1f}s). Minimum is 1 second.',
                details_tail=f'Duration: {duration:.1f}s'
            )
            raise RuntimeError('Manual video duration is too short or unreadable.')

    def _clear_manual_asset(self, session: dict, asset_type: str, note: str | None = None):
        tg = session.get('telegram', {})
        if asset_type == 'image':
            tg['image_file_id'] = None
            tg['image_file_name'] = None
            tg['image_message_id'] = None
        elif asset_type == 'video':
            tg['video_file_id'] = None
            tg['video_file_name'] = None
            tg['video_message_id'] = None
        if note:
            session['note'] = note
        session['updated_at'] = self._now().isoformat()
        self._save_manual_session(session)

    def _try_fetch_manual_assets(self, session: dict) -> tuple[Path, Path] | None:
        tg = session['telegram']
        if not tg.get('image_file_id') or not tg.get('video_file_id'):
            return None

        art_path = self.output_dir / 'generated_art.png'
        video_path = self.output_dir / 'generated_video.mp4'
        raw_image_path = self.output_dir / '_telegram_image_raw'
        raw_video_path = self.output_dir / '_telegram_video_raw.mp4'

        try:
            self._download_telegram_file(tg['image_file_id'], raw_image_path)
            self._download_telegram_file(tg['video_file_id'], raw_video_path)

            try:
                with Image.open(raw_image_path) as img:
                    # Normalize all incoming image formats to pipeline's expected PNG path.
                    img.convert('RGB').save(art_path, format='PNG')
            except Exception as e:
                notifier = get_notifier()
                notifier.notify_warning(
                    run_date=self.run_date,
                    step='ManualImageValidation',
                    message='Image invalid/unreadable. Please resend.',
                    details_tail=str(e)[-500:]
                )
                art_path.unlink(missing_ok=True)
                self._clear_manual_asset(session, 'image', 'Manual image decode failed. Waiting for resend.')
                raise RuntimeError('Manual image decode failed.')

            try:
                self._validate_image_file(art_path)
            except Exception:
                art_path.unlink(missing_ok=True)
                self._clear_manual_asset(session, 'image', 'Manual image validation failed. Waiting for resend.')
                raise

            try:
                self._validate_video_file(raw_video_path)
            except Exception:
                self._clear_manual_asset(session, 'video', 'Manual video validation failed. Waiting for resend.')
                raise

            if video_path.exists():
                video_path.unlink()
            shutil.move(str(raw_video_path), str(video_path))
            return art_path, video_path
        finally:
            # Always clean transient raw files once they're no longer needed.
            raw_image_path.unlink(missing_ok=True)
            raw_video_path.unlink(missing_ok=True)

    def _delete_telegram_message(self, message_id: int):
        self._telegram_api('deleteMessage', data={'chat_id': self.chat_id, 'message_id': message_id})

    def _cleanup_manual_telegram_messages(self, session: dict):
        tg = session.get('telegram', {})
        for key in ('image_message_id', 'video_message_id'):
            message_id = tg.get(key)
            if not message_id:
                continue
            try:
                self._delete_telegram_message(int(message_id))
            except Exception as e:
                print(f"{Fore.YELLOW}⚠ Could not delete Telegram message {message_id}: {e}{Style.RESET_ALL}")

    def _get_session_deadline(self, session: dict) -> datetime:
        raw = session.get('deadline_local')
        if not raw or not isinstance(raw, str):
            return self.deadline_dt
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return self.deadline_dt

    def _mark_session(self, session: dict, status: str, note: str | None = None):
        session['status'] = status
        session['updated_at'] = self._now().isoformat()
        if note:
            session['note'] = note
        self._save_manual_session(session)
        if self.daily_run.is_owner() and status in {'WAITING_MANUAL', 'AUTO_FALLBACK_RUNNING'}:
            self._set_heartbeat_context(status=status, note=note or f'Manual session status={status}.', pulse=True)

    def step_7_9_manual_or_fallback(self, image_json: dict) -> tuple[Path, Path]:
        # In telegram mode, we always do manual wait + fallback (not skipped in dry run)
        # The post_to_instagram flag only controls whether we publish to Instagram at the end
        self.in_auto_fallback = False

        if not self.bot_token or not self.chat_id:
            self.in_auto_fallback = True
            print(f"{Fore.YELLOW}⚠ Telegram credentials missing. Falling back to automatic generation.{Style.RESET_ALL}")
            self._set_heartbeat_context(
                status='AUTO_FALLBACK_RUNNING',
                note='Telegram credentials missing. Running automatic fallback generation.',
                pulse=True,
            )
            art_path = self.step_7_generate_image(image_json)
            video_path = self.step_9_generate_video(art_path, self.output_dir / 'video_prompt.txt')
            return art_path, video_path

        session = self._load_or_init_manual_session()
        session_deadline = self._get_session_deadline(session)

        now = self._now()
        if now >= session_deadline:
            self.in_auto_fallback = True
            print(f"{Fore.YELLOW}⚠ Manual deadline already passed. Triggering automatic fallback now.{Style.RESET_ALL}")
            self._set_heartbeat_context(
                status='AUTO_FALLBACK_RUNNING',
                note='Manual deadline already passed at pipeline start. Running automatic fallback.',
                pulse=True,
            )
            
            # Send status notification for automatic fallback
            notifier = get_notifier()
            notifier.notify_status(
                run_date=self.run_date,
                status='AUTO_FALLBACK',
                message=f'Deadline already passed at pipeline start. Running automatic generation.'
            )
            
            self._mark_session(
                session,
                'AUTO_FALLBACK_RUNNING',
                f"Deadline already passed at pipeline start ({session_deadline.isoformat()}).",
            )
            art_path = self.step_7_generate_image(image_json)
            video_path = self.step_9_generate_video(art_path, self.output_dir / 'video_prompt.txt')
            self._mark_session(session, 'COMPLETED', 'Completed via automatic fallback.')
            return art_path, video_path

        if not session['telegram'].get('prompt_message_id'):
            print(f"{Fore.CYAN}▶ Sending manual prompts to Telegram...{Style.RESET_ALL}")
            try:
                self._send_manual_instruction_packet(session)
            except Exception as e:
                self.in_auto_fallback = True
                # Telegram dispatch failed - mark session and fall back to automatic generation
                print(f"{Fore.YELLOW}⚠ Telegram dispatch failed: {e}{Style.RESET_ALL}")
                print(f"{Fore.YELLOW}⚠ Falling back to automatic generation.{Style.RESET_ALL}")
                
                # Mark session with failure reason
                self._mark_session(
                    session,
                    'AUTO_FALLBACK_RUNNING',
                    f"Telegram dispatch failed: {e}. Running automatic generation.",
                )
                
                # Send status notification (non-blocking)
                safe_notify_status(
                    run_date=self.run_date,
                    status='TELEGRAM_DISPATCH_FAILED',
                    message=f'Telegram prompt dispatch failed: {e}. Switching to automatic generation.'
                )
                
                # Immediately proceed with automatic generation
                art_path = self.step_7_generate_image(image_json)
                video_path = self.step_9_generate_video(art_path, self.output_dir / 'video_prompt.txt')
                self._mark_session(session, 'COMPLETED', 'Completed via automatic fallback after Telegram dispatch failure.')
                return art_path, video_path

        print(
            f"{Fore.YELLOW}⏳ Waiting for manual image+video on Telegram until "
            f"{session_deadline.strftime('%Y-%m-%d %H:%M %Z')}...{Style.RESET_ALL}"
        )
        self._set_heartbeat_context(
            status='WAITING_MANUAL',
            note=f'Waiting for Telegram manual assets until {session_deadline.isoformat()}.',
            pulse=True,
        )

        poll_error_streak = 0
        last_poll_warning_epoch = 0.0
        while self._now() < session_deadline:
            try:
                self._set_heartbeat_context(
                    status='WAITING_MANUAL',
                    note=f'Polling Telegram for manual assets until {session_deadline.isoformat()}.',
                    pulse=True,
                )
                updates = self._fetch_telegram_updates(session)
                if updates:
                    self._extract_manual_assets_from_updates(session, updates)
                # Retry ingest whenever both file IDs exist, even if no fresh updates arrive.
                manual_assets = self._try_fetch_manual_assets(session)
                poll_error_streak = 0
                if manual_assets:
                    self.asset_source = 'manual_telegram'
                    self._cleanup_manual_telegram_messages(session)
                    self._mark_session(session, 'COMPLETED', 'Completed with manual media from Telegram.')
                    # Use safe wrapper - won't block if Telegram fails
                    safe_send_telegram_message(
                        f"Received both manual files for {self.run_date}. Continuing pipeline now.",
                        run_date=self.run_date,
                        context=' (success ack)'
                    )
                    print(f"{Fore.GREEN}✅ Using manual media from Telegram.{Style.RESET_ALL}")
                    return manual_assets
            except Exception as e:
                poll_error_streak += 1
                print(f"{Fore.YELLOW}⚠ Telegram polling error: {e}{Style.RESET_ALL}")
                now_epoch = time.time()
                if poll_error_streak >= 3 and (now_epoch - last_poll_warning_epoch) >= 300:
                    notifier = get_notifier()
                    notifier.notify_warning(
                        run_date=self.run_date,
                        step='TelegramPolling',
                        message='Repeated Telegram polling errors while waiting for manual files. Retrying automatically.',
                        details_tail=str(e)[-1000:],
                    )
                    last_poll_warning_epoch = now_epoch

            time.sleep(max(5, self.telegram_poll_seconds))

        print(f"{Fore.YELLOW}⚠ Manual deadline reached. Triggering full automatic fallback...{Style.RESET_ALL}")
        self.in_auto_fallback = True
        self._set_heartbeat_context(
            status='AUTO_FALLBACK_RUNNING',
            note='Manual deadline reached. Running automatic fallback generation.',
            pulse=True,
        )
        
        # Send status notification for deadline fallback (non-blocking)
        safe_notify_status(
            run_date=self.run_date,
            status='DEADLINE_FALLBACK',
            message=f'Manual deadline reached before assets received. Running automatic generation.'
        )
        
        self._mark_session(session, 'AUTO_FALLBACK_RUNNING', 'Manual deadline reached before both assets were received.')
        # Use safe wrapper - deadline fallback cannot be blocked by Telegram API errors
        safe_send_telegram_message(
            f"Manual deadline reached for {self.run_date}. Running automatic image+video generation now.",
            run_date=self.run_date,
            context=' (deadline fallback)'
        )

        art_path = self.step_7_generate_image(image_json)
        video_path = self.step_9_generate_video(art_path, self.output_dir / 'video_prompt.txt')
        self._mark_session(session, 'COMPLETED', 'Completed via automatic fallback.')
        return art_path, video_path

    def step_1_validate(self):
        print(f"{Fore.CYAN}▶ Running Validation...{Style.RESET_ALL}")
        self._set_heartbeat_context(status='STARTING', note='Running Validation.', pulse=True)
        cmd = [sys.executable, str(self.base_dir / 'src/scripts/validate.py')]
        env = os.environ.copy()
        deadline = getattr(self, 'deadline_dt', None)
        if deadline is not None:
            env['PIPELINE_EFFECTIVE_DEADLINE_ISO'] = deadline.isoformat()
        deadline_reason = getattr(self, 'deadline_reason', '')
        if deadline_reason:
            env['PIPELINE_EFFECTIVE_DEADLINE_REASON'] = deadline_reason
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if result.stdout:
            print(f"{Fore.LIGHTBLACK_EX}   STDOUT: {result.stdout.strip()}{Style.RESET_ALL}")
        if result.returncode != 0:
            print(f"{Fore.RED}Script Error STDOUT:\n{result.stdout}{Style.RESET_ALL}")
            print(f"{Fore.RED}Script Error STDERR:\n{result.stderr}{Style.RESET_ALL}")
            fatal_validation = self._extract_validation_fatal_error(result)
            if fatal_validation:
                message, details_tail = fatal_validation
                raise PipelineStageError(
                    stage='Validation',
                    message=message,
                    details=details_tail,
                    fallback_eligible=False,
                    failure_classification='fallback_unavailable',
                )
            details_tail = self._build_subprocess_details_tail(result)
            raise PipelineStageError(
                stage='Validation',
                message=f'Script exited with code {result.returncode}',
                details=details_tail,
                fallback_eligible=True,
                failure_classification='validation',
            )
        print(f"{Fore.GREEN}✅ Validation completed{Style.RESET_ALL}")
        self._set_heartbeat_context(status='STARTING', note='Completed Validation.', pulse=True)

    def step_1b_validate_instagram_token(self):
        """
        Fail fast before expensive steps (WHOOP/LLM/Image/Video) if IG token is invalid.
        Only runs when posting is enabled.
        """
        print(f"{Fore.CYAN}▶ Validating Instagram token (fail-fast)...{Style.RESET_ALL}")
        self._set_heartbeat_context(status='STARTING', note='Validating Instagram token preflight.', pulse=True)
        try:
            from instagram_token_manager import get_instagram_token_manager
            token_manager = get_instagram_token_manager()
            token = token_manager.get_valid_token()
            user_id = token_manager.get_user_id()
            if not token or token == 'mock':
                raise RuntimeError("INSTAGRAM_ACCESS_TOKEN is missing/invalid for full pipeline mode.")
            if not user_id:
                raise RuntimeError("INSTAGRAM_USER_ID is missing for full pipeline mode.")
            print(f"{Fore.GREEN}✅ Instagram token validation passed{Style.RESET_ALL}")
            self._set_heartbeat_context(status='STARTING', note='Instagram token preflight passed.', pulse=True)
        except Exception as e:
            raise PipelineStageError(
                stage='Instagram Token Preflight',
                message=str(e),
                details=str(e),
                fallback_eligible=True,
                failure_classification='instagram_main_post',
            ) from e

    def step_2_3_lookups(self) -> dict:
        # No more --test flag or mock data - always fetch real WHOOP data
        args = []

        target_date = os.getenv('PIPELINE_DATE')
        if target_date:
            args.extend(['--date', target_date])

        step_name = 'Data Retrieve & Dasha Lookups'
        print(f"{Fore.CYAN}▶ Running {step_name}...{Style.RESET_ALL}")
        self._set_heartbeat_context(status='STARTING', note=f'Running {step_name}.', pulse=True)
        cmd = [sys.executable, str(self.base_dir / 'src/scripts/lookups.py'), *args]
        env = os.environ.copy()
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if result.stdout:
            print(f"{Fore.LIGHTBLACK_EX}   STDOUT: {result.stdout.strip()}{Style.RESET_ALL}")
        if result.returncode == 2:
            details_tail = self._build_subprocess_details_tail(result)
            self._handle_retryable_lookup_not_ready(details_tail)
        elif result.returncode == 3:
            details_tail = self._build_subprocess_details_tail(result)
            self._handle_retryable_lookup_external_failure(details_tail)
        elif result.returncode == 4:
            print(f"{Fore.RED}Script Error STDOUT:\n{result.stdout}{Style.RESET_ALL}")
            print(f"{Fore.RED}Script Error STDERR:\n{result.stderr}{Style.RESET_ALL}")
            details_tail = self._build_subprocess_details_tail(result)
            raise PipelineStageError(
                stage=step_name,
                message='Script reported a terminal lookup failure',
                details=details_tail,
                fallback_eligible=True,
            )
        elif result.returncode != 0:
            print(f"{Fore.RED}Script Error STDOUT:\n{result.stdout}{Style.RESET_ALL}")
            print(f"{Fore.RED}Script Error STDERR:\n{result.stderr}{Style.RESET_ALL}")
            details_tail = self._build_subprocess_details_tail(result)
            raise PipelineStageError(
                stage=step_name,
                message=f'lookups.py exited with unexpected code {result.returncode}',
                details=details_tail,
                fallback_eligible=False,
            )
        print(f"{Fore.GREEN}✅ {step_name} completed{Style.RESET_ALL}")
        self._set_heartbeat_context(status='STARTING', note=f'Completed {step_name}.', pulse=True)

        try:
            with open(self.output_dir / 'daily_data.json', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            raise PipelineStageError(
                stage=step_name,
                message=f'Failed to load daily_data.json: {e}',
                details=str(e),
                fallback_eligible=True,
            ) from e

    def step_4_6_prompts(self):
        self.safe_step(
            'LLM Prompts (Interpretation -> Video)',
            'src/scripts/prompts.py',
            ['--step', 'all', '--data', str(self.output_dir / 'daily_data.json')],
            fallback_eligible=True,
            env_overrides={
                'PIPELINE_PERSIST_ENVIRONMENT_HISTORY': 'true' if self.post_to_instagram else 'false',
            },
        )

    def step_7_generate_image(self, image_json: dict) -> Path:
        args = ['--json', str(self.output_dir / 'image_prompt.json'), '--out', str(self.output_dir / 'generated_art.png')]
        self.safe_step(
            'Image Generation',
            'src/scripts/image_gen.py',
            args,
            status=self._current_generation_status(),
            fallback_eligible=True,
        )
        art_path = self.output_dir / 'generated_art.png'
        if not art_path.exists():
            raise PipelineStageError(
                stage='Image Generation',
                message=f'Expected output missing: {art_path}',
                fallback_eligible=True,
            )
        return art_path

    def step_9_generate_video(self, art_path: Path, video_prompt_path: Path) -> Path:
        print(f"{Fore.CYAN}▶ Running Video Generation...{Style.RESET_ALL}")
        generation_status = self._current_generation_status()
        self._set_heartbeat_context(status=generation_status, note='Generating video asset.', pulse=True)
        try:
            from google_video_client import GoogleVideoClient

            client = GoogleVideoClient()
            prompt_text = Path(video_prompt_path).read_text(encoding='utf-8').strip()
            out_path = self.output_dir / 'generated_video.mp4'
            client.generate_from_image(prompt_text=prompt_text, image_path=art_path, output_path=out_path)
            if not out_path.exists():
                raise FileNotFoundError(f'Expected output missing: {out_path}')
            if out_path.stat().st_mtime < art_path.stat().st_mtime:
                raise RuntimeError(
                    'generated_video.mp4 is older than generated_art.png — '
                    'stale file from a previous run. Aborting to prevent wrong video in card.'
                )
            print(f"{Fore.GREEN}✅ Video Generation completed{Style.RESET_ALL}")
            self._set_heartbeat_context(status=generation_status, note='Video generation completed.', pulse=True)
            return out_path
        except Exception as e:
            raise PipelineStageError(
                stage='Video Generation',
                message=str(e),
                details=str(e),
                fallback_eligible=True,
            ) from e

    def step_10a_render_image(self, art_path: Path, daily_data: dict, metadata: dict) -> Path:
        print(f"{Fore.CYAN}▶ Rendering Static Card...{Style.RESET_ALL}")
        self._set_heartbeat_context(status='STARTING', note='Rendering static card.', pulse=True)
        final_png = self.output_dir / 'card_final.png'

        args = [
            '--type',
            'image',
            '--image',
            str(art_path),
            '--data',
            str(self.output_dir / 'daily_data.json'),
            '--meta',
            str(self.output_dir / 'card_metadata.json'),
            '--output',
            str(final_png),
        ]
        self.safe_step('Render Static Card', 'src/scripts/composite.py', args, fallback_eligible=True)

        if not final_png.exists():
            raise PipelineStageError(
                stage='Render Static Card',
                message=f'Expected output missing: {final_png}',
                fallback_eligible=True,
            )

        print(f"{Fore.GREEN}✅ Render Static Card completed{Style.RESET_ALL}")
        return final_png

    def step_10b_render_video(self, video_path: Path, daily_data: dict, metadata: dict) -> Path:
        print(f"{Fore.CYAN}▶ Rendering Animated Card...{Style.RESET_ALL}")
        self._set_heartbeat_context(status='STARTING', note='Rendering animated card.', pulse=True)
        final_mp4 = self.output_dir / 'card_final.mp4'

        if self.asset_source != 'manual_telegram':
            art_path = self.output_dir / 'generated_art.png'
            if video_path.stat().st_mtime < art_path.stat().st_mtime:
                raise PipelineStageError(
                    stage='Render Animated Card',
                    message=f'Video file ({video_path.name}) is older than art — stale data from a previous run. Aborting.',
                    fallback_eligible=True,
                )

        args = [
            '--type',
            'video',
            '--video',
            str(video_path),
            '--data',
            str(self.output_dir / 'daily_data.json'),
            '--meta',
            str(self.output_dir / 'card_metadata.json'),
            '--output',
            str(final_mp4),
        ]
        self.safe_step('Render Animated Card', 'src/scripts/composite.py', args, fallback_eligible=True)

        if not final_mp4.exists():
            raise PipelineStageError(
                stage='Render Animated Card',
                message=f'Expected output missing: {final_mp4}',
                fallback_eligible=True,
            )

        print(f"{Fore.GREEN}✅ Render Animated Card completed{Style.RESET_ALL}")
        return final_mp4

    def step_12_upload_vps(self, final_mp4: Path, cover_image: Path) -> tuple:
        vps_base = os.getenv('VPS_PUBLIC_BASE_URL')
        if not vps_base or 'mock' in vps_base:
            print(f"{Fore.YELLOW}⚠ VPS_PUBLIC_BASE_URL not set. Using local mock URLs.{Style.RESET_ALL}")
            vps_base = 'https://mock-vps.com/media'

        print(f"{Fore.CYAN}▶ Preparing public media URLs...{Style.RESET_ALL}")
        self._set_heartbeat_context(status='STARTING', note='Preparing VPS media upload.', pulse=True)

        remote_video_name = f'{self.run_date}-card_final.mp4'
        remote_thumb_name = f'{self.run_date}-card_final.png'
        uploads = [
            (final_mp4, remote_video_name),
            (cover_image, remote_thumb_name),
        ]

        if 'mock' not in vps_base and self.post_to_instagram:
            if self.media_mode == 'local_test':
                ensure_path(self.local_vps_dir)
                for local_path, remote_name in uploads:
                    staged_path = self.local_vps_dir / remote_name
                    shutil.copy2(local_path, staged_path)
                print(
                    f"{Fore.GREEN}✅ Local media staged for ngrok → {self.local_vps_dir}{Style.RESET_ALL}"
                )
            else:
                ssh_host = (os.getenv('VPS_SSH_HOST') or '').strip()
                ssh_user = (os.getenv('VPS_SSH_USER') or '').strip()
                ssh_path = (os.getenv('VPS_SSH_PATH') or '').strip()
                live_vps_config_error = get_live_vps_config_error()
                if live_vps_config_error:
                    raise PipelineStageError(
                        stage='VPS Upload',
                        message=live_vps_config_error,
                        details=live_vps_config_error,
                        fallback_eligible=True,
                    )
                if not (ssh_host and ssh_user and ssh_path):
                    raise PipelineStageError(
                        stage='VPS Upload',
                        message='VPS_SSH_HOST/VPS_SSH_USER/VPS_SSH_PATH are required for live_vps media mode.',
                        fallback_eligible=True,
                    )
                else:
                    mounted_upload_dir = Path(ssh_path)
                    if mounted_upload_dir.exists():
                        try:
                            ensure_path(mounted_upload_dir)
                            for local_path, remote_name in uploads:
                                shutil.copy2(local_path, mounted_upload_dir / remote_name)
                            print(
                                f"{Fore.GREEN}✅ Media copied directly to mounted VPS path → "
                                f"{mounted_upload_dir}{Style.RESET_ALL}"
                            )
                        except Exception as e:
                            raise PipelineStageError(
                                stage='VPS Upload',
                                message='Failed to copy media into mounted VPS upload path.',
                                details=str(e),
                                fallback_eligible=True,
                            ) from e
                    else:
                        target = f'{ssh_user}@{ssh_host}'
                        ssh_opts = [
                            '-o', 'StrictHostKeyChecking=no',
                            '-o', 'UserKnownHostsFile=/dev/null',
                        ]
                        mkdir_cmd = ['ssh', *ssh_opts, target, f"mkdir -p {shlex.quote(ssh_path)}"]
                        mkdir_result = subprocess.run(mkdir_cmd, capture_output=True, text=True)
                        if mkdir_result.returncode != 0:
                            details = self._build_subprocess_details_tail(mkdir_result)
                            raise PipelineStageError(
                                stage='VPS Upload',
                                message='Failed to create remote VPS upload directory.',
                                details=details,
                                fallback_eligible=True,
                            )

                        for local_path, remote_name in uploads:
                            remote_target = f'{target}:{ssh_path.rstrip("/")}/{remote_name}'
                            scp_cmd = ['scp', *ssh_opts, str(local_path), remote_target]
                            scp_result = subprocess.run(scp_cmd, capture_output=True, text=True)
                            if scp_result.returncode != 0:
                                details = self._build_subprocess_details_tail(scp_result)
                                raise PipelineStageError(
                                    stage='VPS Upload',
                                    message=f'Failed to upload {local_path.name} to VPS.',
                                    details=details,
                                    fallback_eligible=True,
                                )

        vps_base = vps_base.rstrip("/")
        video_url = f'{vps_base}/{remote_video_name}'
        thumb_url = f'{vps_base}/{remote_thumb_name}'

        if 'mock' not in vps_base and self.post_to_instagram:
            try:
                self._ensure_public_urls_reachable(
                    (('video', 'video', video_url), ('image', 'thumbnail', thumb_url)),
                )
            except Exception as e:
                raise PipelineStageError(
                    stage='VPS Upload',
                    message=str(e),
                    details=str(e),
                    fallback_eligible=True,
                ) from e

        print(f"{Fore.GREEN}✅ VPS assets ready: {video_url}{Style.RESET_ALL}")
        self._set_heartbeat_context(status='STARTING', note='VPS media upload completed.', pulse=True)
        return video_url, thumb_url

    def _probe_public_media_url(self, media_kind: str, label: str, url: str) -> dict:
        snapshot = {
            "media_kind": media_kind,
            "label": label,
            "url": url,
            "reachable": False,
        }
        response = None
        try:
            response = requests.get(url, timeout=20, stream=True, allow_redirects=True)
            headers = {}
            for key in (
                "Content-Type",
                "Content-Length",
                "Cache-Control",
                "ETag",
                "Last-Modified",
                "Accept-Ranges",
                "Server",
            ):
                value = response.headers.get(key)
                if value:
                    headers[key.lower().replace("-", "_")] = value
            snapshot["http_status"] = response.status_code
            snapshot["final_url"] = response.url
            snapshot["headers"] = headers

            if response.status_code != 200:
                snapshot["failure_reason"] = f"HTTP {response.status_code}"
                return snapshot

            first_chunk = next(response.iter_content(chunk_size=1), b"")
            snapshot["has_body"] = bool(first_chunk)
            content_type = response.headers.get("Content-Type", "")
            snapshot["content_type_ok"] = self._is_media_like_content_type(content_type, media_kind)

            if not first_chunk:
                snapshot["failure_reason"] = "empty body"
                return snapshot
            if not snapshot["content_type_ok"]:
                snapshot["failure_reason"] = f"unexpected content-type {content_type or 'missing'}"
                return snapshot

            snapshot["reachable"] = True
            return snapshot
        except Exception as e:
            snapshot["error"] = str(e)
            snapshot["failure_reason"] = str(e)
            return snapshot
        finally:
            if response is not None:
                response.close()

    def _ensure_public_urls_reachable(self, media_urls: tuple[tuple[str, str, str], ...], capture_snapshots: bool = False):
        snapshots = []
        for media_kind, label, url in media_urls:
            probe = None
            for attempt in range(3):
                probe = self._probe_public_media_url(media_kind, label, url)
                if probe.get("reachable"):
                    break
                if attempt < 2:
                    time.sleep(2)
            assert probe is not None
            snapshots.append(probe)
            if not probe.get("reachable"):
                failure_reason = probe.get("failure_reason") or "unknown"
                if failure_reason == "empty body":
                    reason_code = f"empty_public_{label}_body"
                elif failure_reason.startswith("unexpected content-type"):
                    reason_code = f"invalid_public_{label}_content_type"
                else:
                    reason_code = f"unreachable_public_{label}_url"
                raise RuntimeError(f'{reason_code}: Public {label} URL is not reachable: {url} ({failure_reason})')
        if capture_snapshots:
            return snapshots

    def _sha256_file(self, path: Path) -> str | None:
        if not path.is_file():
            return None
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def _probe_local_media_file(self, path: Path, media_kind: str) -> dict:
        snapshot = {
            "path": str(path),
            "exists": path.is_file(),
        }
        if not path.is_file():
            return snapshot

        stat = path.stat()
        snapshot["size_bytes"] = stat.st_size
        snapshot["sha256"] = self._sha256_file(path)
        snapshot["mtime_utc"] = datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z"

        if media_kind == "video":
            ffprobe_path = shutil.which("ffprobe")
            if not ffprobe_path:
                snapshot["ffprobe"] = {
                    "available": False,
                    "error": "ffprobe not available on PATH",
                }
                return snapshot

            cmd = [
                ffprobe_path,
                "-v",
                "error",
                "-show_entries",
                "format=duration,size:stream=index,codec_name,codec_type,width,height,r_frame_rate,avg_frame_rate,pix_fmt,profile,bit_rate,level",
                "-of",
                "json",
                str(path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                snapshot["ffprobe"] = {
                    "available": False,
                    "returncode": result.returncode,
                    "error": (result.stderr or "").strip(),
                }
                return snapshot
            try:
                snapshot["ffprobe"] = {
                    "available": True,
                    "data": json.loads(result.stdout or "{}"),
                }
            except Exception as e:
                snapshot["ffprobe"] = {
                    "available": False,
                    "error": f"ffprobe JSON parse failed: {e}",
                    "stdout_tail": (result.stdout or "")[-1000:],
                }
            return snapshot

        try:
            with Image.open(path) as img:
                snapshot["image"] = {
                    "format": img.format,
                    "mode": img.mode,
                    "width": img.width,
                    "height": img.height,
                }
        except Exception as e:
            snapshot["image_error"] = str(e)
        return snapshot

    def _build_instagram_publish_context(self, video_url: str, thumb_url: str, caption: str) -> dict:
        return {
            "asset_source": self.asset_source,
            "media_mode": self.media_mode,
            "output_dir": str(self.output_dir),
            "caption_tail": (caption or "")[-500:],
            "local_media": {
                "card_final_mp4": self._probe_local_media_file(self.output_dir / "card_final.mp4", "video"),
                "card_final_png": self._probe_local_media_file(self.output_dir / "card_final.png", "image"),
            },
            "public_url_checks": self._ensure_public_urls_reachable(
                (("video", "video", video_url), ("image", "thumb", thumb_url)),
                capture_snapshots=True,
            ),
        }

    def _cache_bust_media_url(self, url: str, attempt: int) -> str:
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}ig_retry={self.run_date}-{self.run_token}-{attempt}"

    @staticmethod
    def _is_retryable_instagram_processing_error(error) -> bool:
        diagnostics = getattr(error, "diagnostics", None)
        if not isinstance(diagnostics, dict):
            return False
        return (
            getattr(error, "phase", None) == "poll_processing"
            and diagnostics.get("terminal_status_code") == "ERROR"
        )

    def _run_emergency_fallback(self, trigger_stage: str, reason: str) -> bool:
        self._last_emergency_fallback_error = None
        self._last_emergency_fallback_details = None
        self._last_emergency_fallback_classification = None
        self.in_emergency_fallback = True

        try:
            from database_manager import CardDatabase
            from emergency_fallback_manager import EmergencyFallbackManager, FallbackUnavailableError

            manager = EmergencyFallbackManager()
            try:
                manifest = manager.load_and_validate_manifest()
            except FallbackUnavailableError as e:
                self._set_emergency_fallback_failure('manifest_invalid', str(e), str(e))
                return False

            try:
                manager.verify_integrity()
            except FallbackUnavailableError as e:
                self._set_emergency_fallback_failure('asset_integrity_failed', str(e), str(e))
                return False

            self._active_emergency_fallback_version = manifest['version']

            self.asset_source = 'emergency_fallback'
            self._set_heartbeat_context(
                status='EMERGENCY_FALLBACK_RUNNING',
                note=f'Emergency post fallback active after {trigger_stage} failure.',
                pulse=True,
            )

            if self.daily_run.is_owner():
                self.daily_run.update_status(
                    status='EMERGENCY_FALLBACK_RUNNING',
                    note=f'Emergency post fallback active after {trigger_stage} failure.',
                    extra={
                        'asset_source': 'emergency_fallback',
                        'fallback_trigger_stage': trigger_stage,
                        'fallback_reason': reason,
                        'fallback_version': manifest['version'],
                    },
                )

            notifier = get_notifier()
            notifier.notify_emergency_fallback_activated(
                run_date=self.run_date,
                trigger_stage=trigger_stage,
                fallback_version=manifest['version'],
            )

            self._ensure_owner_runtime_dirs()
            try:
                copied_paths = manager.copy_to_run_output(self.output_dir)
            except Exception as e:
                self._set_emergency_fallback_failure(
                    'asset_integrity_failed',
                    'Failed to stage fallback media into run output.',
                    str(e),
                )
                return False

            publish_mode: str | None = None
            video_url: str | None = None
            thumb_url: str | None = None
            prehosted_failure: str | None = None

            try:
                strategy = manager.get_publish_strategy()
            except FallbackUnavailableError as e:
                self._set_emergency_fallback_failure('manifest_invalid', str(e), str(e))
                return False

            try:
                self._ensure_public_urls_reachable(
                    (
                        ('video', 'fallback video', strategy['video_url']),
                        ('image', 'fallback thumbnail', strategy['thumb_url']),
                    ),
                )
                publish_mode = strategy['mode']
                video_url = strategy['video_url']
                thumb_url = strategy['thumb_url']
            except Exception as e:
                prehosted_failure = str(e)
                try:
                    video_url, thumb_url = self.step_12_upload_vps(
                        copied_paths['mp4_path'],
                        copied_paths['png_path'],
                    )
                    publish_mode = 'runtime_vps_upload'
                except PipelineStageError as upload_error:
                    self._set_emergency_fallback_failure(
                        'runtime_upload_failed',
                        'Runtime VPS upload strategy failed.',
                        self._merge_details(
                            f'[prehosted_unreachable] {prehosted_failure}',
                            upload_error.message,
                            upload_error.details,
                        ),
                    )
                    return False
                except Exception as upload_error:
                    self._set_emergency_fallback_failure(
                        'runtime_upload_failed',
                        'Runtime VPS upload strategy failed.',
                        self._merge_details(
                            f'[prehosted_unreachable] {prehosted_failure}',
                            str(upload_error),
                        ),
                    )
                    return False

            self._active_emergency_fallback_publish_mode = publish_mode

            caption = manager.build_fallback_caption(self.run_date)
            try:
                post_result = self.step_14_post_instagram(
                    video_url,
                    thumb_url,
                    caption,
                    success_notification_mode='fallback',
                    fallback_eligible_on_publish_failure=False,
                )
            except PipelineStageError as e:
                classification = 'instagram_token_failed' if e.stage == 'Instagram Token' else 'instagram_publish_failed'
                publish_diagnostics = self._coerce_publish_diagnostics(e.details_obj, e.details)
                self._set_emergency_fallback_failure(
                    classification,
                    e.message,
                    self._merge_details(
                        e.details,
                        prehosted_failure and f'[prehosted_unreachable] {prehosted_failure}',
                    ),
                )
                self._write_emergency_fallback_publish_failure_log(
                    manager,
                    trigger_stage=trigger_stage,
                    reason=reason,
                    publish_mode=publish_mode,
                    video_url=video_url,
                    thumb_url=thumb_url,
                    publish_diagnostics=publish_diagnostics,
                )
                return False
            except Exception as e:
                self._set_emergency_fallback_failure(
                    'instagram_publish_failed',
                    str(e),
                    self._merge_details(
                        str(e),
                        prehosted_failure and f'[prehosted_unreachable] {prehosted_failure}',
                    ),
                )
                self._write_emergency_fallback_publish_failure_log(
                    manager,
                    trigger_stage=trigger_stage,
                    reason=reason,
                    publish_mode=publish_mode,
                    video_url=video_url,
                    thumb_url=thumb_url,
                    publish_diagnostics=str(e),
                )
                return False

            reused_existing_post = bool(post_result.get('already_posted'))
            post_id = post_result.get('post_id')
            instagram_permalink = post_result.get('permalink')

            try:
                manager.write_emergency_log(
                    self.output_dir,
                    trigger_stage=trigger_stage,
                    reason=reason,
                    publish_mode=publish_mode,
                    video_url=video_url,
                    thumb_url=thumb_url,
                    instagram_post_id=post_id,
                    instagram_permalink=instagram_permalink,
                    reused_existing_post=reused_existing_post,
                )
            except Exception as e:
                notifier.notify_warning(
                    run_date=self.run_date,
                    step='Emergency Fallback Archive',
                    message='Emergency fallback posted, but emergency_fallback_used.json could not be written.',
                    details_tail=str(e),
                )

            try:
                CardDatabase().insert_fallback_post(
                    {
                        'run_date': self.run_date,
                        'asset_source': 'emergency_fallback',
                        'fallback_version': manifest['version'],
                        'fallback_trigger_stage': trigger_stage,
                        'fallback_reason': reason,
                        'title': manifest['title'],
                        'scene_description': manifest['scene_description'],
                        'publish_mode': publish_mode,
                        'instagram_post_id': post_id,
                        'instagram_permalink': instagram_permalink,
                        'video_path_or_url': video_url,
                        'image_path_or_url': thumb_url,
                    }
                )
            except Exception as e:
                notifier.notify_warning(
                    run_date=self.run_date,
                    step='Emergency Fallback Database',
                    message='Emergency fallback posted, but fallback_posts insert failed.',
                    details_tail=str(e),
                )

            return True
        except FallbackUnavailableError as e:
            self._set_emergency_fallback_failure('manifest_invalid', f'Emergency fallback unavailable: {e}', str(e))
            return False
        except Exception as e:
            self._set_emergency_fallback_failure('instagram_publish_failed', f'Emergency fallback failed: {e}', str(e))
            return False
        finally:
            self.in_emergency_fallback = False
            self._active_emergency_fallback_version = None
            self._active_emergency_fallback_publish_mode = None

    def step_13_build_caption(self, metadata: dict, daily_data: dict) -> str:
        self._set_heartbeat_context(status='STARTING', note='Building Instagram caption.', pulse=True)
        date_str = daily_data.get('date', self.run_date)
        date_display = daily_data.get('date_display') or metadata.get('date_display') or date_str
        title = metadata.get('title', 'UNKNOWN TITLE')
        hashtags = _build_hashtags(date_str)
        caption = (
            f"{title} · {date_display}\n\n"
            "What if your daily health data could generate art?\n\n"
            "My daily WHOOP data, sleep, recovery, yesterday's strain, runs through a metrics engine "
            "I designed and I layered in Prana Dasha too, a Vedic astrology system that works at a "
            "daily level tuned to my natal chart. I'm skeptical, but it seeds real variation and it's "
            "personal enough that I kept it in.\n\n"
            "Not sure any of this means anything. That's kind of the point.\n\n"
            f"{hashtags}"
        )
        print(f"{Fore.CYAN}▶ Caption Built: {title}{Style.RESET_ALL}")
        self._set_heartbeat_context(status='STARTING', note='Instagram caption built.', pulse=True)
        return caption

    def step_14_post_instagram(
        self,
        video_url: str,
        thumb_url: str,
        caption: str,
        *,
        success_notification_mode: str = 'normal',
        fallback_eligible_on_publish_failure: bool = True,
    ):
        state = self.daily_run.load_state() or {}
        if (state.get('status') or '').strip().upper() == 'POSTED':
            existing_post_id = state.get('instagram_post_id')
            existing_permalink = state.get('instagram_permalink')
            print(f"{Fore.YELLOW}⚠ Daily state already POSTED. Skipping Instagram publish.{Style.RESET_ALL}")
            return {
                'already_posted': True,
                'post_id': existing_post_id,
                'permalink': existing_permalink,
                'mock': False,
            }

        from instagram_token_manager import get_instagram_token_manager

        try:
            token_manager = get_instagram_token_manager()
            access_token = token_manager.get_valid_token()
            user_id = token_manager.get_user_id()
        except Exception as e:
            if self.in_emergency_fallback:
                # Do not recurse into emergency fallback from inside the fallback publish path.
                # fallback_eligible is intentionally omitted here so token failures inside the
                # emergency fallback remain terminal and cannot trigger nested fallback attempts.
                raise PipelineStageError(
                    stage='Instagram Token',
                    message=f'Failed to fetch Instagram token during emergency fallback: {e}',
                    details=str(e),
                ) from e
            raise PipelineStageError(
                stage='Instagram Token',
                message=f'Failed to fetch Instagram token: {e}',
                details=str(e),
                fallback_eligible=True,
                failure_classification='instagram_main_post',
            ) from e

        if not self.post_to_instagram or not access_token or access_token == 'mock':
            print(f"{Fore.YELLOW}⚠ Running in MOCK/DRY mode. Skipping actual Instagram post.{Style.RESET_ALL}")
            print(f"{Fore.CYAN}▶ Posting to Instagram (Mock)...{Style.RESET_ALL}")
            # P3: Don't send success notification for dry runs (per contract)
            return {
                'already_posted': False,
                'post_id': 'mock_ig_12345',
                'permalink': None,
                'mock': True,
            }

        print(f"{Fore.CYAN}▶ Posting to Instagram (Real)...{Style.RESET_ALL}")
        self._set_heartbeat_context(status='POSTING', note='Posting media to Instagram.', pulse=True)
        from instagram_poster import InstagramPoster, InstagramPublishDiagnosticsError

        processing_max_attempts = max(1, int(os.getenv("INSTAGRAM_PROCESSING_MAX_ATTEMPTS", "2") or "2"))
        processing_retry_delay = max(0, int(os.getenv("INSTAGRAM_PROCESSING_RETRY_DELAY_SECONDS", "30") or "30"))

        try:
            for processing_attempt in range(1, processing_max_attempts + 1):
                attempt_video_url = video_url
                attempt_thumb_url = thumb_url
                if processing_attempt > 1:
                    attempt_video_url = self._cache_bust_media_url(video_url, processing_attempt)
                    attempt_thumb_url = self._cache_bust_media_url(thumb_url, processing_attempt)

                try:
                    publish_context = self._build_instagram_publish_context(
                        attempt_video_url,
                        attempt_thumb_url,
                        caption,
                    )
                except Exception as e:
                    if fallback_eligible_on_publish_failure and not self.in_emergency_fallback:
                        raise PipelineStageError(
                            stage='Instagram Media Preflight',
                            message=str(e),
                            details=str(e),
                            fallback_eligible=True,
                        ) from e
                    raise PipelineStageError(
                        stage='Instagram Media Preflight',
                        message=str(e),
                        details=str(e),
                        fallback_eligible=False,
                    ) from e

                publish_context["processing_attempt"] = processing_attempt
                publish_context["processing_max_attempts"] = processing_max_attempts

                poster = InstagramPoster(access_token, user_id)
                poster.diagnostics_output_dir = self.output_dir
                poster.run_date = self.run_date
                if hasattr(poster, "set_publish_context"):
                    poster.set_publish_context(publish_context)

                try:
                    creation_id = poster.create_media_container(attempt_video_url, attempt_thumb_url, caption)
                    if not poster.poll_processing_status(creation_id):
                        raise poster.build_processing_timeout_error(creation_id)
                except InstagramPublishDiagnosticsError as e:
                    if (
                        self._is_retryable_instagram_processing_error(e)
                        and processing_attempt < processing_max_attempts
                    ):
                        print(
                            f"{Fore.YELLOW}⚠ Instagram processing failed for creation_id. "
                            f"Retrying with cache-busted media URL "
                            f"({processing_attempt + 1}/{processing_max_attempts}).{Style.RESET_ALL}"
                        )
                        if processing_retry_delay > 0:
                            time.sleep(processing_retry_delay)
                        continue
                    raise

                post_id = poster.publish_media(creation_id)

                # Get permalink
                permalink = poster.get_permalink(post_id)

                print(f"{Fore.GREEN}✅ Post published! ID: {post_id}{Style.RESET_ALL}")
                success_note = (
                    'Emergency fallback Instagram publish succeeded.'
                    if success_notification_mode == 'fallback'
                    else 'Instagram publish succeeded.'
                )
                self._mark_posted_terminal_success(
                    post_id=post_id,
                    permalink=permalink,
                    note=success_note,
                )

                # Send success notification with MP4 and permalink
                final_mp4 = self.output_dir / 'card_final.mp4'
                notifier = get_notifier()
                if success_notification_mode == 'fallback':
                    notifier.notify_emergency_fallback_posted(
                        run_date=self.run_date,
                        final_mp4_path=final_mp4,
                        instagram_permalink=permalink or 'Permalink unavailable',
                        fallback_version=self._active_emergency_fallback_version or 'error_404_v1',
                    )
                else:
                    notifier.notify_success_posted(
                        run_date=self.run_date,
                        final_mp4_path=final_mp4,
                        instagram_permalink=permalink or 'Permalink unavailable'
                    )

                # Return both post_id and permalink as dict for archive step
                return {
                    'already_posted': False,
                    'post_id': post_id,
                    'permalink': permalink,
                    'mock': False,
                }
        except InstagramPublishDiagnosticsError as e:
            if fallback_eligible_on_publish_failure and not self.in_emergency_fallback:
                raise PipelineStageError(
                    stage='Instagram Posting',
                    message=str(e),
                    details=e.details_tail(),
                    details_obj=e.diagnostics,
                    fallback_eligible=True,
                ) from e
            raise PipelineStageError(
                stage='Instagram Posting',
                message=str(e),
                details=e.details_tail(),
                details_obj=e.diagnostics,
                fallback_eligible=False,
            ) from e

        except PipelineStageError:
            raise
        except Exception as e:
            if fallback_eligible_on_publish_failure and not self.in_emergency_fallback:
                raise PipelineStageError(
                    stage='Instagram Posting',
                    message=f'Instagram posting failed: {str(e)}',
                    details=str(e),
                    fallback_eligible=True,
                ) from e
            raise PipelineStageError(
                stage='Instagram Posting',
                message=f'Instagram posting failed: {str(e)}',
                details=str(e),
                fallback_eligible=False,
            ) from e

    def step_15_archive(
        self,
        daily_data: dict,
        metadata: dict,
        final_png: Path,
        final_mp4: Path,
        image_json: dict,
        post_id: str,
        instagram_permalink: str = None,
        blend_option: str = None,
        creature: str = None,
        environment: str = None,
    ):
        self._set_heartbeat_context(status='STARTING', note='Archiving payload and inserting into database.', pulse=True)
        resolved_environment_path = self.output_dir / 'environment_selected.txt'
        resolved_environment_text = environment
        if resolved_environment_path.exists():
            resolved_environment_text = resolved_environment_path.read_text(encoding='utf-8').strip()
        environment_name, environment_reason = split_environment_output(resolved_environment_text or "")
        archive_payload = {
            'date': daily_data.get('date'),
            'title': metadata.get('title'),
            'scene_description': metadata.get('scene_description'),
            'environment': resolved_environment_text,
            'environment_name': environment_name or None,
            'environment_reason': environment_reason or None,
            'creature': creature,
            'blend_option': blend_option,
            'energy_zone': daily_data.get('energy_zone'),
            'recovery_pct': daily_data.get('recovery_pct'),
            'sleep_score_pct': daily_data.get('sleep_score_pct'),
            'strain': daily_data.get('strain'),
            'sleep_hours': daily_data.get('sleep_hours'),
            'depth_level': daily_data.get('depth_level'),
            'dasha_maha': daily_data.get('dasha', {}).get('maha'),
            'dasha_antar': daily_data.get('dasha', {}).get('antar'),
            'dasha_pratyantar': daily_data.get('dasha', {}).get('pratyantar'),
            'dasha_sookshma': daily_data.get('dasha', {}).get('sookshma'),
            'dasha_prana': daily_data.get('dasha', {}).get('prana'),
            'image_path': str(final_png),
            'video_path': str(final_mp4),
            'image_prompt_json': json.dumps(image_json),
            'instagram_post_id': post_id,
            'instagram_permalink': instagram_permalink,
        }

        payload_path = self.output_dir / 'last_archived_payload.json'
        self._write_json_atomic(payload_path, archive_payload)

        if self.post_to_instagram:
            cmd = [
                sys.executable,
                str(self.base_dir / 'src/scripts/database_manager.py'),
                '--insert',
                '--file',
                str(payload_path),
            ]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ.copy())
            except Exception as e:
                self._notify_post_success_cleanup_warning(
                    'Database Archive',
                    'Instagram post succeeded, but archive/database insert could not be started. Leaving daily state as POSTED.',
                    str(e),
                )
                return
            if result.stdout:
                print(f"{Fore.LIGHTBLACK_EX}   STDOUT: {result.stdout.strip()}{Style.RESET_ALL}")
            if result.returncode != 0:
                details_tail = self._build_subprocess_details_tail(result)
                self._notify_post_success_cleanup_warning(
                    'Database Archive',
                    'Instagram post succeeded, but archive/database insert failed. Leaving daily state as POSTED.',
                    details_tail,
                )
                return
            print(f"{Fore.GREEN}✅ Archive completed → {payload_path}{Style.RESET_ALL}")
            self._set_heartbeat_context(status='STARTING', note='Archive and database insert completed.', pulse=True)
        else:
            print(f"{Fore.YELLOW}⚠ Dry-run mode: skipping database archive insert.{Style.RESET_ALL}")


if __name__ == '__main__':
    # P1: Install early exception handler before anything runs
    # This catches errors during pipeline initialization
    def _early_exception_handler(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        
        import traceback
        tb_str = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        
        # Try to notify, but don't crash if notification fails
        try:
            notifier = get_notifier()
            # Use a generic run_date since we don't have access to pipeline yet
            run_date = get_pipeline_run_date_str()
            notifier.notify_error(
                run_date=run_date,
                step='PIPELINE_INIT',
                error_type=exc_type.__name__,
                message=str(exc_value),
                details_tail=tb_str[-2000:],
                fatal=True
            )
        except Exception:
            pass  # Best effort notification
        
        print(f"{Fore.RED}❌ EARLY FAILURE: {exc_type.__name__}: {exc_value}{Style.RESET_ALL}")
    
    sys.excepthook = _early_exception_handler
    
    # P1: Also set thread exception handler
    def _thread_exception_handler(args):
        try:
            notifier = get_notifier()
            run_date = get_pipeline_run_date_str()
            notifier.notify_error(
                run_date=run_date,
                step='THREAD_EXCEPTION',
                error_type=args.exc_type.__name__ if args.exc_type else 'ThreadError',
                message=str(args.exc_value),
                fatal=True
            )
        except Exception:
            pass
    
    threading.excepthook = _thread_exception_handler
    
    # Now run the pipeline normally
    pipeline = WHOOPPipeline()
    pipeline.run()

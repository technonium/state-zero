import json
import os
import random
import shutil
import subprocess
import sys
import time
import uuid
import shlex
import threading
from datetime import date as date_cls, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from colorama import Fore, Style, init
from dotenv import load_dotenv
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
)
from notifier import get_notifier, safe_send_telegram_message, safe_notify_status
from daily_run_state import DailyRunStateManager, OwnershipLostError

load_dotenv(dotenv_path=get_project_root() / '.env', override=True)
init()


HASHTAGS_CORE = [
    '#GenerativeArt',
    '#AIArt',
    '#DigitalArt',
    '#DataArt',
    '#AIGenerated',
    '#DailyArt',
]

HASHTAGS_POOL = [
    '#CreativeCoding',
    '#AlgorithmicArt',
    '#MotionDesign',
    '#QuantifiedSelf',
    '#BuildingInPublic',
    '#IndieCreator',
    '#DataVisualization',
    '#GenerativeAI',
    '#ArtEveryDay',
    '#ProcessingArt',
    '#MotionArt',
    '#AIArtwork',
    '#ComputationalArt',
    '#SelfTracking',
    '#WHOOPData',
    '#ExperimentalArt',
]


def _build_hashtags(date_str: str) -> str:
    rng = random.Random(date_str)
    rotating = rng.sample(HASHTAGS_POOL, 9)
    return ' '.join(HASHTAGS_CORE + rotating)


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

    def _normalize_mode(self, raw_mode: str) -> str:
        """Normalize mode - only 'automatic' and 'telegram' are valid."""
        if raw_mode not in ('automatic', 'telegram'):
            print(f"{Fore.YELLOW}⚠ Unknown PIPELINE_MODE={raw_mode}. Use 'automatic' or 'telegram'.{Style.RESET_ALL}")
            sys.exit(1)
        return raw_mode

    def _build_deadline_dt(self) -> tuple[datetime, str]:
        try:
            run_day = date_cls.fromisoformat(self.run_date)
        except ValueError:
            run_day = date_cls.fromisoformat(get_pipeline_run_date_str())

        if self.manual_deadline_mode == 'from_now':
            now = self._now()
            minutes = max(1, self.manual_window_minutes)
            return now + timedelta(minutes=minutes), f'from_now(+{minutes}m)'

        if self.manual_deadline_mode != 'run_date':
            print(
                f"{Fore.YELLOW}⚠ Invalid PIPELINE_MANUAL_DEADLINE_MODE={self.manual_deadline_mode}. "
                f"Using run_date mode.{Style.RESET_ALL}"
            )
            self.manual_deadline_mode = 'run_date'

        try:
            hour_str, minute_str = self.manual_deadline_local.split(':', 1)
            hour = int(hour_str)
            minute = int(minute_str)
        except Exception:
            hour = 14
            minute = 0

        deadline = datetime(run_day.year, run_day.month, run_day.day, hour, minute, tzinfo=self.tz)
        return deadline, f'run_date({self.manual_deadline_local})'

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

    def _mark_posted_terminal_success(self, post_id: str | None, permalink: str | None, note: str):
        if not self.daily_run.is_owner():
            return
        with self._heartbeat_lock:
            self._heartbeat_status = 'POSTED'
            self._heartbeat_note = note
        try:
            self.daily_run.mark_posted(post_id=post_id, permalink=permalink, note=note)
        except OwnershipLostError as e:
            self.log_error('Daily Ownership', str(e))

    def _handle_retryable_lookup_not_ready(self, details_tail: str | None = None):
        self._stop_heartbeat_thread()
        message = 'WHOOP recovery entry for today is not ready yet. Releasing claim for next cron retry.'
        retry_cleanup_notes: list[str] = []
        try:
            self.daily_run.mark_retryable_failure(
                step='Data Retrieve & Dasha Lookups',
                message=message,
                details_tail=details_tail,
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
            step='WHOOPRecoveryNotReady',
            message='WHOOP recovery not ready yet. This run was released for the next cron retry.',
            details_tail=warning_details,
        )
        if retry_cleanup_notes:
            print(f"{Fore.YELLOW}⚠ {' | '.join(retry_cleanup_notes)}{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}⚠ {message}{Style.RESET_ALL}")
        raise SystemExit(0)

    def log_error(self, step_name: str, error_msg: str, details_tail: str = None):
        if self.post_to_instagram and self.daily_run.is_owner():
            try:
                self.daily_run.mark_fatal_failure(
                    step=step_name,
                    message=error_msg,
                    details_tail=details_tail,
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

    def safe_step(self, step_name: str, script_path: str, args: list = None, status: str = 'STARTING'):
        try:
            print(f"{Fore.CYAN}▶ Running {step_name}...{Style.RESET_ALL}")
            self._set_heartbeat_context(status=status, note=f'Running {step_name}.', pulse=True)
            cmd = [sys.executable, str(self.base_dir / script_path)]
            if args:
                cmd.extend(args)
            env = os.environ.copy()
            result = subprocess.run(cmd, capture_output=True, text=True, env=env)
            if result.stdout:
                print(f"{Fore.LIGHTBLACK_EX}   STDOUT: {result.stdout.strip()}{Style.RESET_ALL}")
            if result.returncode != 0:
                print(f"{Fore.RED}Script Error STDOUT:\n{result.stdout}{Style.RESET_ALL}")
                print(f"{Fore.RED}Script Error STDERR:\n{result.stderr}{Style.RESET_ALL}")

                details_tail = self._build_subprocess_details_tail(result)
                self.log_error(step_name, f'Script exited with code {result.returncode}', details_tail)
            print(f"{Fore.GREEN}✅ {step_name} completed{Style.RESET_ALL}")
            self._set_heartbeat_context(status=status, note=f'Completed {step_name}.', pulse=True)
            return result.stdout
        except Exception as e:
            # Call log_error which will send the notification
            self.log_error(step_name, str(e), str(e))

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

            interpretation = (self.output_dir / 'interpretation.txt').read_text(encoding='utf-8').strip()
            caption = self.step_13_build_caption(metadata, daily_data, interpretation)

            # Only post to Instagram when enabled
            if self.post_to_instagram:
                post_result = self.step_14_post_instagram(video_url, thumb_url, caption)
            else:
                print(f"{Fore.YELLOW}⚠ Dry-run mode: skipping Instagram post.{Style.RESET_ALL}")
                post_result = None

            if isinstance(post_result, dict) and post_result.get('already_posted'):
                print(f"{Fore.YELLOW}⚠ State already marked POSTED. Skipping archive/database work.{Style.RESET_ALL}")
                return

            # Handle both dict (new) and string (legacy) return types
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
        finally:
            self._stop_heartbeat_thread()
            self._cleanup_non_authoritative_daily_state()

    def _load_required_json(self, path: Path, label: str) -> dict:
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            self.log_error(label, f'{label} not found - prompts.py failed')
        except json.JSONDecodeError as e:
            self.log_error(label, f'{label} is invalid JSON: {e}')

    def _load_required_text_outputs(self) -> tuple[str, str, str]:
        try:
            blend_option = (self.output_dir / 'blend_option.txt').read_text(encoding='utf-8').strip()
            creature = (self.output_dir / 'creature.txt').read_text(encoding='utf-8').strip()
            environment = (self.output_dir / 'environment.txt').read_text(encoding='utf-8').strip()
            return blend_option, creature, environment
        except FileNotFoundError as e:
            self.log_error('Prompt Outputs', f'Missing prompt output file: {e.filename}')

    def _load_or_init_manual_session(self) -> dict:
        if self.session_file.exists():
            try:
                session = json.loads(self.session_file.read_text(encoding='utf-8'))
                session = self._migrate_manual_session(session)
                if self._is_session_reusable(session):
                    return session
                print(f"{Fore.YELLOW}⚠ Existing manual session is stale/terminal. Starting a fresh session.{Style.RESET_ALL}")
            except Exception:
                pass

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
        tg = session.setdefault('telegram', {})
        tg.setdefault('last_update_id', 0)
        tg.setdefault('prompt_message_id', None)
        reply_ids = tg.get('accepted_reply_message_ids')
        if not isinstance(reply_ids, list):
            reply_ids = []
        if tg.get('prompt_message_id'):
            try:
                reply_ids.append(int(tg['prompt_message_id']))
            except Exception:
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

    def _save_manual_session(self, session: dict):
        self.session_file.write_text(json.dumps(session, indent=2), encoding='utf-8')

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
        if not raw:
            return self.deadline_dt
        try:
            return datetime.fromisoformat(raw)
        except Exception:
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
        self.safe_step('Validation', 'src/scripts/validate.py')

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
            self.log_error('Instagram Token Preflight', str(e))

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
        elif result.returncode != 0:
            print(f"{Fore.RED}Script Error STDOUT:\n{result.stdout}{Style.RESET_ALL}")
            print(f"{Fore.RED}Script Error STDERR:\n{result.stderr}{Style.RESET_ALL}")
            details_tail = self._build_subprocess_details_tail(result)
            self.log_error(step_name, f'Script exited with code {result.returncode}', details_tail)
        print(f"{Fore.GREEN}✅ {step_name} completed{Style.RESET_ALL}")
        self._set_heartbeat_context(status='STARTING', note=f'Completed {step_name}.', pulse=True)

        with open(self.output_dir / 'daily_data.json', encoding='utf-8') as f:
            return json.load(f)

    def step_4_6_prompts(self):
        self.safe_step('LLM Prompts (Interpretation -> Video)', 'src/scripts/prompts.py', ['--step', 'all'])

    def step_7_generate_image(self, image_json: dict) -> Path:
        args = ['--json', str(self.output_dir / 'image_prompt.json'), '--out', str(self.output_dir / 'generated_art.png')]
        self.safe_step('Image Generation', 'src/scripts/image_gen.py', args, status=self._current_generation_status())
        art_path = self.output_dir / 'generated_art.png'
        if not art_path.exists():
            self.log_error('Image Generation', f'Expected output missing: {art_path}')
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
            self.log_error('Video Generation', str(e))

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
        self.safe_step('Render Static Card', 'src/scripts/composite.py', args)

        print(f"{Fore.GREEN}✅ Render Static Card completed{Style.RESET_ALL}")
        return final_png

    def step_10b_render_video(self, video_path: Path, daily_data: dict, metadata: dict) -> Path:
        print(f"{Fore.CYAN}▶ Rendering Animated Card...{Style.RESET_ALL}")
        self._set_heartbeat_context(status='STARTING', note='Rendering animated card.', pulse=True)
        final_mp4 = self.output_dir / 'card_final.mp4'

        if self.asset_source != 'manual_telegram':
            art_path = self.output_dir / 'generated_art.png'
            if video_path.stat().st_mtime < art_path.stat().st_mtime:
                self.log_error(
                    'Render Animated Card',
                    f'Video file ({video_path.name}) is older than art — stale data from a previous run. Aborting.',
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
        self.safe_step('Render Animated Card', 'src/scripts/composite.py', args)

        if not final_mp4.exists():
            self.log_error('Render Animated Card', f'Expected output missing: {final_mp4}')

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
                if not (ssh_host and ssh_user and ssh_path):
                    self.log_error(
                        'VPS Upload',
                        'VPS_SSH_HOST/VPS_SSH_USER/VPS_SSH_PATH are required for live_vps media mode.',
                    )
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
                        self.log_error('VPS Upload', 'Failed to create remote VPS upload directory.', details)

                    for local_path, remote_name in uploads:
                        remote_target = f'{target}:{ssh_path.rstrip("/")}/{remote_name}'
                        scp_cmd = ['scp', *ssh_opts, str(local_path), remote_target]
                        scp_result = subprocess.run(scp_cmd, capture_output=True, text=True)
                        if scp_result.returncode != 0:
                            details = self._build_subprocess_details_tail(scp_result)
                            self.log_error('VPS Upload', f'Failed to upload {local_path.name} to VPS.', details)

        vps_base = vps_base.rstrip("/")
        video_url = f'{vps_base}/{remote_video_name}'
        thumb_url = f'{vps_base}/{remote_thumb_name}'

        if 'mock' not in vps_base and self.post_to_instagram:
            for label, url in (('video', video_url), ('thumbnail', thumb_url)):
                reachable = False
                last_err = None
                for attempt in range(3):
                    try:
                        head = requests.head(url, timeout=20, allow_redirects=True)
                        if head.status_code in (405, 403):
                            # Some static servers block HEAD; fallback to GET probe.
                            get_resp = requests.get(url, timeout=20, stream=True)
                            get_resp.close()
                            if get_resp.status_code < 400:
                                reachable = True
                                break
                        elif head.status_code < 400:
                            reachable = True
                            break
                        last_err = f"HTTP {head.status_code}"
                    except Exception as e:
                        last_err = str(e)
                    if attempt < 2:
                        time.sleep(2)
                if not reachable:
                    self.log_error('VPS Upload', f'Public {label} URL is not reachable: {url}', last_err)

        print(f"{Fore.GREEN}✅ VPS assets ready: {video_url}{Style.RESET_ALL}")
        self._set_heartbeat_context(status='STARTING', note='VPS media upload completed.', pulse=True)
        return video_url, thumb_url

    def step_13_build_caption(self, metadata: dict, daily_data: dict, _interpretation: str) -> str:
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

    def step_14_post_instagram(self, video_url: str, thumb_url: str, caption: str):
        state = self.daily_run.load_state() or {}
        if (state.get('status') or '').strip().upper() == 'POSTED':
            existing_post_id = state.get('instagram_post_id')
            existing_permalink = state.get('instagram_permalink')
            print(f"{Fore.YELLOW}⚠ Daily state already POSTED. Skipping Instagram publish.{Style.RESET_ALL}")
            return {
                'already_posted': True,
                'post_id': existing_post_id,
                'permalink': existing_permalink,
            }

        from instagram_token_manager import get_instagram_token_manager

        try:
            token_manager = get_instagram_token_manager()
            access_token = token_manager.get_valid_token()
            user_id = token_manager.get_user_id()
        except Exception as e:
            self.log_error('Instagram Token', str(e))

        if not self.post_to_instagram or not access_token or access_token == 'mock':
            print(f"{Fore.YELLOW}⚠ Running in MOCK/DRY mode. Skipping actual Instagram post.{Style.RESET_ALL}")
            print(f"{Fore.CYAN}▶ Posting to Instagram (Mock)...{Style.RESET_ALL}")
            # P3: Don't send success notification for dry runs (per contract)
            return 'mock_ig_12345'

        print(f"{Fore.CYAN}▶ Posting to Instagram (Real)...{Style.RESET_ALL}")
        self._set_heartbeat_context(status='POSTING', note='Posting media to Instagram.', pulse=True)
        from instagram_poster import InstagramPoster

        try:
            poster = InstagramPoster(access_token, user_id)

            creation_id = poster.create_media_container(video_url, thumb_url, caption)
            if poster.poll_processing_status(creation_id):
                post_id = poster.publish_media(creation_id)
                
                # Get permalink
                permalink = poster.get_permalink(post_id)
                
                print(f"{Fore.GREEN}✅ Post published! ID: {post_id}{Style.RESET_ALL}")
                self._mark_posted_terminal_success(
                    post_id=post_id,
                    permalink=permalink,
                    note='Instagram publish succeeded.',
                )
                
                # Send success notification with MP4 and permalink
                final_mp4 = self.output_dir / 'card_final.mp4'
                notifier = get_notifier()
                notifier.notify_success_posted(
                    run_date=self.run_date,
                    final_mp4_path=final_mp4,
                    instagram_permalink=permalink or 'Permalink unavailable'
                )
                
                # Return both post_id and permalink as dict for archive step
                return {'post_id': post_id, 'permalink': permalink}
            
            # Processing failed
            self.log_error('Instagram Posting', 'Media processing failed on Instagram side.')
            
        except Exception as e:
            # P0: Catch exceptions from instagram_poster and send notification before exiting
            self.log_error('InstagramPosting', f'Instagram posting failed: {str(e)}')

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
        archive_payload = {
            'date': daily_data.get('date'),
            'title': metadata.get('title'),
            'scene_description': metadata.get('scene_description'),
            'environment': environment,
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
        with open(payload_path, 'w', encoding='utf-8') as f:
            json.dump(archive_payload, f, indent=2)

        if self.post_to_instagram:
            self.safe_step('Database Archive', 'src/scripts/database_manager.py', ['--insert', '--file', str(payload_path)])
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

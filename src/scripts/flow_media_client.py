"""Narrow adapter from State Zero's media files to the pinned gflow CLI."""

import json
import fcntl
import hashlib
import math
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from google_image_client import GoogleImageClient
from google_video_client import GoogleVideoClient
from utils import run_bounded, sanitize_error, OwnedProcessCleanupError, process_cleanup_verified


# Exact decoded enums, including the pinned CLI's PUBLIC_ERROR_* safety reasons.
POLICY_VERDICTS = frozenset({
    'PUBLIC_ERROR_UNSAFE_CONTENT', 'PUBLIC_ERROR_UNSAFE_GENERATION',
    'PUBLIC_ERROR_UNSAFE_IDENTITY', 'PUBLIC_ERROR_UNSAFE_FACE', 'PUBLIC_ERROR_UNUSUAL_ACTIVITY',
    'SAFETY', 'SAFETY_REJECTION', 'ACCOUNT_RESTRICTED', 'RECAPTCHA_FAILED',
})
CREDIT_VERDICTS = frozenset({'QUOTA', 'QUOTA_EXCEEDED', 'INSUFFICIENT_CREDITS'})
MEDIA_RECORDS = {'flow_image_diagnostics.json', 'flow_video_diagnostics.json',
                 'google_image_diagnostics.json', 'google_video_diagnostics.json'}


class FlowCommandError(RuntimeError):
    def __init__(self, stage: str, category: str, *, error_class: str = "", media_id: str = "", retryable: bool = False, detail: str = "", operation_id: str = "", submission_state: str = ""):
        self.stage = stage
        self.detail = detail
        self.operation_id = operation_id
        self.submission_state = submission_state
        self.category = category
        self.error_class = error_class
        self.media_id = media_id
        self.retryable = retryable
        super().__init__(f"Flow {stage} {category} ({error_class or 'unclassified'})" + (f": {detail}" if detail else ""))


class FlowMediaClient:
    def __init__(self, profile: str | None = None):
        self.profile = profile or os.getenv("GFLOW_PROFILE", "shadow")

    @staticmethod
    def _input_settings() -> dict:
        return {name: os.getenv(name, default) for name, default in (
            ('GFLOW_PROFILE', 'shadow'), ('FLOW_PREFLIGHT_PROJECT_ID', ''),
            ('FLOW_IMAGE_UPSCALE_2K', 'false'), ('FLOW_VIDEO_UPSCALE_1080P', 'true'),
            ('GOOGLE_IMAGE_MODEL', 'gemini-nano-banana-2.1'),
            ('GOOGLE_VIDEO_MODEL', 'veo-3.1-fast-generate-preview'))}

    @staticmethod
    def _input_names(directory: Path) -> list[str]:
        names = ['daily_data.json', 'whoop_snapshot.json', 'card_metadata.json',
                 'image_prompt.json', 'video_prompt.txt', 'blend_option.txt']
        return names + [next((n for n in (f'{kind}_selected.txt', f'{kind}.txt') if (directory / n).is_file()),
                             f'{kind}_selected.txt') for kind in ('creature', 'environment')]

    @staticmethod
    def validate_inputs(directory: Path) -> bool:
        path = directory / 'flow_inputs.json'
        if not path.exists():
            return False
        try:
            saved = json.loads(path.read_text())
            files = saved['files']
            if set(files) != set(FlowMediaClient._input_names(directory)) or saved['settings'] != FlowMediaClient._input_settings():
                raise ValueError('Generation settings changed')
            for name, digest in files.items():
                if Path(name).name != name or hashlib.sha256((directory / name).read_bytes()).hexdigest() != digest:
                    raise ValueError('Frozen input changed')
            return True
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            raise FlowCommandError('inputs', 'request_identity_unverified',
                                   detail='Saved generation inputs require reconciliation; they were not rewritten.') from None

    @staticmethod
    def freeze_inputs(directory: Path) -> None:
        if FlowMediaClient.validate_inputs(directory):
            return
        names = FlowMediaClient._input_names(directory)
        files = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in names}
        FlowMediaClient._write_diagnostics(directory / 'flow_inputs.json',
                                          {'files': files, 'settings': FlowMediaClient._input_settings()})

    @staticmethod
    def _generation_seconds() -> float:
        from utils import get_pipeline_deadline
        deadline, _ = get_pipeline_deadline()
        return deadline.timestamp() - time.time() - 1800

    @staticmethod
    def _stops_replacement(error: FlowCommandError) -> bool:
        return (error.category in {'request_identity_unverified', 'original_asset_changed', 'execution_busy', 'validation_failed', 'provider_rejected', 'pre_submit_terminal'} or
                error.category == 'generation_failed' and not error.retryable)

    def _notify_transition(self, diagnostics: Path, status: str, message: str) -> None:
        saved = FlowMediaClient._read_diagnostics(diagnostics)
        if saved.get('last_notification') == status:
            return
        from notifier import safe_notify_status
        from utils import get_pipeline_run_date_str
        if safe_notify_status(get_pipeline_run_date_str(), status, message):
            self._write_diagnostics(diagnostics, {'last_notification': status})

    @staticmethod
    def _check_browser_ownership(diagnostics: Path, stage: str) -> None:
        for path in diagnostics.parent.glob('flow_*_diagnostics.json'):
            saved=FlowMediaClient._read_diagnostics(path)
            for evidence in (saved.get('last_generation_process', {}),
                             saved.get('last_worker', {}).get('processes', {})):
                if evidence.get('cleanup_verified') is False and not process_cleanup_verified(evidence):
                    raise FlowCommandError(stage,'execution_busy',submission_state='not_attempted',
                        detail='Recorded browser ownership is still active.')

    @staticmethod
    def _pending_submission(directory: Path) -> FlowCommandError | None:
        """Check before upstream inputs can be overwritten by a scheduled retry."""
        for stage, name in (("image", "generated_art.png"), ("video", "generated_video.mp4")):
            path = directory / f"flow_{stage}_diagnostics.json"
            markers = list(directory.glob(f"flow_{stage}_attempt_*.submit"))
            if not path.exists() and not markers:
                continue
            saved, failure = {}, {}
            try:
                saved = FlowMediaClient._read_diagnostics(path)
                if not isinstance(saved, dict):
                    saved = {}
                    raise ValueError("Invalid diagnostics")
                failure = saved.get("failure") or {}
                if not isinstance(failure, dict):
                    failure = {}
                    raise ValueError("Invalid failure")
                states = [json.loads(marker.read_text())["state"] for marker in markers]
                canonical = directory / name
                if (saved.get("selected_sha256") and canonical.is_file() and not failure
                        and hashlib.sha256(canonical.read_bytes()).hexdigest() == saved["selected_sha256"]):
                    continue
                if not markers and not saved.get('attempts') and not saved.get('pending_attempt'):
                    continue
                if (states and all(state == "blocked_before_submission" for state in states)
                        and failure.get("category") in {"pre_submit_transient", "pre_submit_timeout", "auth_required", "blocked_before_submission"}):
                    continue
            except FlowCommandError as exc:
                return exc
            except (OSError, ValueError, KeyError, TypeError):
                pass  # A broken marker must not erase a readable policy verdict.
            action = "Recover the recorded media with saved inputs" if saved.get("result") else "Inspect the original Flow project and reconcile the submitted request"
            reason = str(failure.get("detail") or "")[:500]
            return FlowCommandError(stage, "submission_uncertain", submission_state="uncertain",
                                    error_class=failure.get("error_class", ""),
                                    operation_id=failure.get("operation_id", ""),
                                    detail=f"{action}; automatic generation and API fallback are blocked." +
                                           (f" Original failure: {reason}" if reason else ""))
        return None

    def generate_image(self, prompt_json: dict, output_path: Path) -> Path:
        return self._generate_guarded("image", prompt_json, output_path,
                                      lambda result: self._generate_image(prompt_json, output_path, result))

    def generate_video(self, prompt_text: str, image_path: Path, output_path: Path) -> Path:
        request = {"prompt": prompt_text, "start_frame_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest()}
        return self._generate_guarded("video", request, output_path,
                                      lambda result: self._generate_video(prompt_text, image_path, output_path, result), image_path)

    def generate_api_stage(self, stage: str, request, output: Path, image_path: Path | None = None) -> Path:
        """API-default uses the same saved-input and atomic-asset boundaries as Flow."""
        if self.validate_inputs(output.parent):
            expected = (json.loads((output.parent / 'image_prompt.json').read_text()) if stage == 'image'
                        else (output.parent / 'video_prompt.txt').read_text().strip())
            if request != expected:
                raise FlowCommandError(stage, 'request_identity_unverified')
        identity = {'request': request, 'settings': self._input_settings(),
                    'image_sha256': hashlib.sha256(image_path.read_bytes()).hexdigest() if image_path else None}
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        diagnostics = output.with_name(f'google_{stage}_diagnostics.json')
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.with_name('.flow-generation.lock').open('a') as lock:
            os.chmod(lock.name, 0o600)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise FlowCommandError(stage, 'execution_busy') from None
            other = output.with_name(f'flow_{stage}_diagnostics.json')
            if other.exists() or any(output.parent.glob(f'flow_{stage}_attempt_*.submit')):
                raise FlowCommandError(stage, 'submission_uncertain', submission_state='api_attempted',
                    detail='The Flow path has existing media evidence; resume that path or reconcile before changing providers.')
            saved = FlowMediaClient._read_diagnostics(diagnostics)
            if not isinstance(saved, dict):
                raise FlowCommandError(stage, 'request_identity_unverified')
            if saved and saved.get('request_hash') != fingerprint:
                raise FlowCommandError(stage, 'request_identity_unverified')
            candidate = output.with_name(f'.{output.stem}.api{output.suffix}')
            digest = saved.get('selected_sha256')
            if digest:
                for path in (output, candidate):
                    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
                        if path != output:
                            path.replace(output)
                        output.touch()
                        return output
                raise FlowCommandError(stage, 'original_asset_changed')
            timeout = 180 if stage == 'image' else int(os.getenv('GOOGLE_VIDEO_TIMEOUT_SECONDS', '900')) + 480
            following_video = int(os.getenv('GOOGLE_VIDEO_TIMEOUT_SECONDS', '900')) + 480 if stage == 'image' else 0
            if self._generation_seconds() < timeout + following_video:
                raise FlowCommandError(stage, 'generation_window_exhausted')
            client = GoogleImageClient() if stage == 'image' else GoogleVideoClient()
            client.router.retry_ambiguous_calls = False
            client.generation_deadline = time.time() + max(0, self._generation_seconds())
            if saved.get('logical_attempt'):
                if stage != 'video' or not saved.get('operation_name'):
                    raise FlowCommandError(stage, 'submission_uncertain', submission_state='api_attempted')
                client.resume_operation(candidate)
            else:
                self._write_diagnostics(diagnostics, {'request_hash': fingerprint, 'logical_attempt': 'reserved'})
                if stage == 'image':
                    client.generate_from_json(request, candidate)
                else:
                    client.generate_from_image(request, image_path, candidate)
            self._validate_original(stage, candidate)
            if stage == 'video' and self._probe_video(candidate)[:2] != (1080, 1920):
                raise FlowCommandError(stage, 'validation_failed')
            self._write_diagnostics(diagnostics, {'selected_sha256': hashlib.sha256(candidate.read_bytes()).hexdigest(),
                'logical_attempt': 'reserved'})
            candidate.replace(output)
            output.touch()
            return output

    def _generate_guarded(self, stage: str, request: dict, output: Path, generate, image_path: Path | None = None) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        diagnostics = output.with_name(f"flow_{stage}_diagnostics.json")
        identity = {"request": request, "profile": self.profile,
                    "project": os.getenv("FLOW_PREFLIGHT_PROJECT_ID", ""),
                    "settings": "Nano Banana 2.1/3:4/x1" if stage == "image" else "veo-fast/i2v/9:16/x1/8s"}
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        with output.with_name(".flow-generation.lock").open("a") as lock:
            os.chmod(lock.name, 0o600)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise FlowCommandError(stage, "execution_busy") from None
            previous = FlowMediaClient._read_diagnostics(diagnostics)
            if not isinstance(previous, dict):
                raise FlowCommandError(stage, 'request_identity_unverified')
            api_record = output.with_name(f'google_{stage}_diagnostics.json')
            if api_record.exists() and (json.loads(api_record.read_text()).get('logical_attempt') or not previous.get('api_reserved')):
                raise FlowCommandError(stage, 'submission_uncertain', submission_state='api_attempted',
                    detail='The API path already reserved this date’s API attempt; reconcile its existing result.')
            frozen = self.validate_inputs(output.parent)
            if not frozen:
                raise FlowCommandError(stage, 'request_identity_unverified',
                    detail='Freeze the complete generation inputs before entering the Flow stage.')
            expected = (json.loads((output.parent / 'image_prompt.json').read_text()) if stage == 'image'
                        else (output.parent / 'video_prompt.txt').read_text().strip())
            if (request if stage == 'image' else request['prompt']) != expected:
                raise FlowCommandError(stage, 'request_identity_unverified')
            markers = list(output.parent.glob(f"flow_{stage}_attempt_*.submit"))
            if (previous or markers or output.exists()) and previous.get("request_hash") != fingerprint:
                raise FlowCommandError(stage, "request_identity_unverified")
            if previous.get('selected_sha256'):
                name = previous.get('selected_file', '')
                candidates = [output] + ([output.parent / name] if Path(name).name == name else [])
                for candidate in candidates:
                    if candidate.is_file() and hashlib.sha256(candidate.read_bytes()).hexdigest() == previous['selected_sha256']:
                        return self._finish_daily(stage, output, diagnostics, previous['provider'], candidate)
            self._write_diagnostics(diagnostics, {'request_hash': fingerprint})
            return self._generate_daily(stage, request, output, generate, image_path)

    @staticmethod
    def _daily_attempts(stage: str, diagnostics: Path) -> list[dict]:
        saved = FlowMediaClient._read_diagnostics(diagnostics)
        failure = saved.get('failure') or {}
        invalid = FlowCommandError(stage, 'request_identity_unverified',
            **{key: failure[key] for key in ('error_class', 'detail', 'operation_id', 'media_id') if key in failure})
        attempts = saved.get('attempts', [])
        if not isinstance(attempts, list) or any(not isinstance(a, dict) or type(a.get('number')) is not int or a['number'] < 1 for a in attempts):
            raise FlowCommandError(stage, 'request_identity_unverified')
        by_number = {a['number']: dict(a) for a in attempts}
        if len(by_number) != len(attempts):
            raise FlowCommandError(stage, 'request_identity_unverified')
        pending = saved.get('pending_attempt')
        if pending is not None:
            if not isinstance(pending, dict) or type(pending.get('number')) is not int or pending['number'] < 1:
                raise FlowCommandError(stage, 'request_identity_unverified')
            by_number[pending['number']] = pending | by_number.get(pending['number'], {})
        if sorted(by_number) != list(range(1, len(by_number) + 1)):
            raise invalid
        for marker in diagnostics.parent.glob(f'flow_{stage}_attempt_*.submit'):
            try:
                number = int(marker.stem.rsplit('_', 1)[1])
                attempt = by_number[number]
            except (ValueError, KeyError):
                raise FlowCommandError(stage, 'request_identity_unverified') from None
            try:
                raw = marker.read_text()
                evidence = {'state': 'attempted'} if raw == 'submit_attempted\n' else json.loads(raw)
                if not isinstance(evidence, dict) or raw != 'submit_attempted\n' and evidence.get('state') not in {'forwarded', 'blocked_before_submission'}:
                    raise invalid
                blocked = evidence['state'] == 'blocked_before_submission'
                if blocked and (not isinstance(evidence.get('reason'), str) or not evidence['reason']):
                    raise invalid
                if stage == 'image' and evidence['state'] == 'forwarded' and (
                        evidence.get('expected_model') != 'NARWHAL' or evidence.get('actual_model') != 'BELUGA' or
                        evidence.get('selected_model') != 'Nano Banana 2.1'):
                    raise invalid
                if stage == 'video' and raw != 'submit_attempted\n':
                    from flow_readback import identity_hash
                    context=json.loads(marker.with_suffix('.context.json').read_text())
                    if (evidence.get('actual_model') != 'veo_3_1_i2v_s_fast_portrait'
                            or evidence.get('readback_hash') != identity_hash(context)):
                        raise invalid
            except (OSError, ValueError, AttributeError, KeyError, TypeError):
                raise invalid from None
            attempt['submission'] = evidence['state']
        if sum(a.get('submission') != 'blocked_before_submission' for a in by_number.values()) > 2 or sum(a.get('submission') == 'blocked_before_submission' for a in by_number.values()) > 2:
            raise FlowCommandError(stage, 'request_identity_unverified')
        return sorted(by_number.values(), key=lambda a: a['number'])

    def _save_attempt(self, diagnostics: Path, attempt: dict) -> None:
        attempt = {k:v for k,v in attempt.items() if k != 'submission'}
        saved = FlowMediaClient._read_diagnostics(diagnostics)
        attempts = [a for a in saved.get('attempts', []) if a['number'] != attempt['number']]
        attempts.append(attempt)
        self._write_diagnostics(diagnostics, {'attempts': attempts, 'pending_attempt': None,
                                              **({'failure': attempt['failure']} if attempt.get('failure') else {})})

    def local_pair_available(self, directory: Path, *, manual_assets=None) -> bool:
        if not self.validate_inputs(directory):
            return False
        for stage,name in (('image','generated_art.png'),('video','generated_video.mp4')):
            if stage in (manual_assets or {}):
                continue
            output=directory/name
            flow=directory/f'flow_{stage}_diagnostics.json'
            api=directory/f'google_{stage}_diagnostics.json'
            path=flow if flow.exists() else api
            if not path.exists():
                return False
            saved=FlowMediaClient._read_diagnostics(path)
            failure=saved.get('failure') or {}
            if failure and self._stops_replacement(FlowCommandError(stage,**failure)):
                return False
            digest=saved.get('selected_sha256')
            if digest and output.is_file():
                self._validate_original(stage,output,digest)
                continue
            if path==api or saved.get('api_reserved') or not saved.get('original_sha256'):
                return False
            attempts=self._daily_attempts(stage,path)
            if not attempts:
                return False
            receipt=(self._image_receipt if stage=='image' else self._video_receipt)(path,attempts[-1])
            local=self._local_result(stage,saved.get('result'),output,saved['original_sha256'])
            if not receipt or not local:
                return False
            key='media_name' if stage=='image' else 'media_id'
            if (receipt['images'][0][key] if stage=='image' else receipt[key]) != (local['images'][0][key] if stage=='image' else local[key]):
                raise FlowCommandError(stage,'request_identity_unverified')
        return True

    def _generate_daily(self, stage: str, request: dict, output: Path, generate, image_path: Path | None) -> Path:
        diagnostics = output.with_name(f'flow_{stage}_diagnostics.json')
        flow_timeout = 600 if stage == 'image' else 1500
        api_timeout = 180 if stage == 'image' else int(os.getenv('GOOGLE_VIDEO_TIMEOUT_SECONDS', '900')) + 480
        following_video = (min(1500, int(os.getenv('GOOGLE_VIDEO_TIMEOUT_SECONDS', '900')) + 480)
                           if os.getenv('FLOW_API_FALLBACK_ENABLED', 'false').lower() == 'true' else 1500) if stage == 'image' else 0
        while True:
            saved = FlowMediaClient._read_diagnostics(diagnostics)
            attempts = self._daily_attempts(stage, diagnostics)
            if any(a.get('submission') == 'blocked_before_submission' for a in attempts):
                raise FlowCommandError(stage, 'provider_rejected', detail='Recorded Flow SETTINGS validation failed before forwarding.')
            last = attempts[-1] if attempts else {}
            failure = saved.get('processing_failure') or saved.get('failure') or last.get('failure') or {}
            error = FlowCommandError(stage, **failure) if failure else None
            if error and error.category=='execution_busy':
                self._check_browser_ownership(diagnostics,stage)
                error=FlowCommandError(stage,'post_submit_error' if last.get('submission') != 'blocked_before_submission'
                    else 'pre_submit_transient')
            if error and self._stops_replacement(error):
                raise error
            if os.getenv('STATE_ZERO_LOCAL_ONLY_DEADLINE'):
                if time.time() >= float(os.environ['STATE_ZERO_LOCAL_ONLY_DEADLINE']) or saved.get('api_reserved'):
                    raise FlowCommandError(stage, 'local_completion_unavailable', retryable=True)
                local = self._local_result(stage, saved.get('result'), output, saved.get('original_sha256'))
                if not local:
                    raise FlowCommandError(stage, 'local_completion_unavailable', retryable=True)
                generate(local)
                return self._finish_daily(stage, output, diagnostics, 'flow')
            if saved.get('api_reserved'):
                if stage != 'video':
                    raise FlowCommandError(stage, 'submission_uncertain', submission_state='api_attempted')
                client = GoogleVideoClient()
                client.generation_deadline = time.time() + max(0, self._generation_seconds())
                candidate = output.with_name(f'.{output.stem}.api{output.suffix}')
                client.resume_operation(candidate)
                return self._finish_daily(stage, output, diagnostics, 'google_api', candidate)
            if error and error.category == 'local_processing_failed':
                local = self._local_result(stage, saved.get('result'), output, saved.get('original_sha256'))
                if not local:
                    raise error
                generate(local)
                return self._finish_daily(stage, output, diagnostics, 'flow')
            if attempts and attempts[-1].get('submission') != 'blocked_before_submission':
                attempt = attempts[-1]
                try:
                    recovered = self._recover_pending(stage, diagnostics, attempt, output)
                except FlowCommandError as exc:
                    if exc.category != 'generation_failed' or not exc.retryable:
                        raise
                    error = exc
                    failure = {k: getattr(exc, k) for k in ('category', 'error_class', 'media_id', 'retryable',
                                                          'detail', 'operation_id')}
                    attempt['failure'] = failure
                    self._save_attempt(diagnostics, attempt)
                    self._write_diagnostics(diagnostics, {'failure': failure, 'retrieval': {}})
                    recovered = None
                if recovered:
                    try:
                        generate(recovered)
                        return self._finish_daily(stage, output, diagnostics, 'flow')
                    except FlowCommandError as exc:
                        if self._stops_replacement(exc) or exc.category == 'local_processing_failed':
                            raise
            used = sum(a.get('submission') != 'blocked_before_submission' for a in attempts)
            current=FlowMediaClient._read_diagnostics(diagnostics)
            identified=bool(current.get('retrieval',{}).get('candidate'))
            confirmed_failure=bool(error and error.category=='generation_failed' and error.retryable)
            unresolved=bool(used and not identified and not confirmed_failure)
            from utils import get_pipeline_now
            if unresolved and get_pipeline_now().hour < 12:
                raise FlowCommandError(stage,'request_identity_unverified',
                    detail='Submitted result remains unresolved; retry read-only search next slot. Paid API is eligible from 12:00 IST.')
            skip_flow = identified or unresolved or error and error.category in {'auth_required', 'credits_exhausted'}
            if used < 2 and not skip_flow and self._generation_seconds() >= flow_timeout + following_video:
                number = max([a['number'] for a in attempts], default=0) + 1
                attempt = {'number': number, 'reserved_at': time.time()}
                self._write_diagnostics(diagnostics, {'pending_attempt': attempt, 'result': None,
                    'failure': None, 'original_sha256': None})
                if used:
                    self._notify_transition(diagnostics, f'FLOW_{stage.upper()}_REPLACEMENT',
                                            f'Flow {stage}: using replacement submission 2 of 2 with saved inputs.')
                try:
                    generate(None)
                    self._save_attempt(diagnostics, attempt)
                    return self._finish_daily(stage, output, diagnostics, 'flow')
                except FlowCommandError as exc:
                    failure = {k: getattr(exc, k) for k in ('category', 'error_class', 'media_id', 'retryable',
                                                          'detail', 'operation_id')}
                    attempt['failure'] = failure
                    self._save_attempt(diagnostics, attempt)
                    self._write_diagnostics(diagnostics, {'failure': failure})
                    if self._stops_replacement(exc) or exc.category == 'local_processing_failed':
                        raise
                    continue
                except Exception as exc:
                    failure = {'category': 'validation_failed', 'error_class': type(exc).__name__}
                    self._save_attempt(diagnostics, attempt | {'failure': failure})
                    self._write_diagnostics(diagnostics, {'failure': failure})
                    raise FlowCommandError(stage, **failure) from exc
            if os.getenv('FLOW_API_FALLBACK_ENABLED', 'false').lower() != 'true' or self._generation_seconds() < api_timeout + following_video:
                raise FlowCommandError(stage, 'generation_window_exhausted' if self._generation_seconds() < api_timeout else 'attempts_exhausted',
                                       detail='Fresh generation budget exhausted; existing emergency rescue remains available.')
            self._write_diagnostics(diagnostics, {'api_reserved': True,
                'fallback_reason': 'unresolved_at_noon' if unresolved else error.category if error else 'generation_window'})
            self._notify_transition(diagnostics, f'FLOW_{stage.upper()}_API_FALLBACK',
                                    f'Flow {stage}: using paid Google API fallback with saved inputs.')
            client = GoogleImageClient() if stage == 'image' else GoogleVideoClient()
            client.router.retry_ambiguous_calls = False
            client.generation_deadline = time.time() + max(0, self._generation_seconds())
            candidate = output.with_name(f'.{output.stem}.api{output.suffix}')
            if stage == 'image':
                client.generate_from_json(request, candidate)
            else:
                client.generate_from_image(request['prompt'], image_path, candidate)
            return self._finish_daily(stage, output, diagnostics, 'google_api', candidate)

    def _finish_daily(self, stage: str, output: Path, diagnostics: Path, provider: str, candidate: Path | None = None) -> Path:
        candidate = candidate or output
        if stage == 'image':
            with Image.open(candidate) as image:
                image.load()
                if image.width < 512 or image.height < 512 or abs(image.width / image.height - .75) > .015:
                    raise FlowCommandError(stage, 'validation_failed')
        else:
            width, height, duration, audio = self._probe_video(candidate)
            if (width, height) != (1080, 1920) or not 7.5 <= duration <= 8.5 or not audio:
                raise FlowCommandError(stage, 'validation_failed')
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        self._write_diagnostics(diagnostics, {'selected_sha256': digest, 'selected_file': candidate.name, 'provider': provider})
        if candidate != output:
            candidate.replace(output)
        output.touch()
        self._write_diagnostics(diagnostics, {'failure': None, 'processing_failure': None})
        if provider == 'google_api':
            self._notify_transition(diagnostics, f'FLOW_{stage.upper()}_API_COMPLETE',
                f'Paid Google API {stage} completed and validated; continuing the remaining stages.')
        return output

    def _local_result(self, stage: str, result: dict | None, output: Path, expected_hash: str | None) -> dict | None:
        if result is None:
            return None
        try:
            item = result['images'][0] if stage == 'image' else result
            local = item.get('local_path')
        except (KeyError, IndexError, TypeError, AttributeError):
            raise FlowCommandError(stage, 'request_identity_unverified') from None
        if local:
            try:
                path = self._owned_path(local, output.parent)
                self._validate_original(stage, path, expected_hash or result.get('sha256'))
                if stage == 'video':
                    diagnostic = output.with_name('flow_video_diagnostics.json')
                    attempts = self._daily_attempts(stage, diagnostic)
                    receipt = self._video_receipt(diagnostic, attempts[-1]) if attempts else None
                    if not receipt or any(result.get(k,receipt[k]) != receipt[k] for k in
                            ('media_id', 'workflow_id', 'request_hash', 'profile', 'project_id', 'request')):
                        raise FlowCommandError(stage, 'request_identity_unverified')
                    result = result | receipt
                return result
            except FlowCommandError as exc:
                if self._stops_replacement(exc) or exc.category == 'local_processing_failed':
                    raise
            except (RuntimeError, TypeError, ValueError):
                raise FlowCommandError(stage, 'request_identity_unverified') from None
            except OSError:
                pass
        return None

    def _recover_pending(self, stage: str, diagnostics: Path, attempt: dict, output: Path) -> dict | None:
        saved = FlowMediaClient._read_diagnostics(diagnostics)
        # A local file can disappear during handoff; never give its downloader a fresh budget.
        now = time.time()
        image_receipt = self._image_receipt(diagnostics, attempt) if stage == 'image' else self._video_receipt(diagnostics, attempt)
        exact_result = saved.get('result') or image_receipt
        if image_receipt and saved.get('result'):
            try:
                if (saved['result']['images'][0]['media_name'] if stage == 'image' else saved['result']['media_id']) != (image_receipt['images'][0]['media_name'] if stage == 'image' else image_receipt['media_id']):
                    raise ValueError('Conflicting image identities')
            except (KeyError, IndexError, TypeError, ValueError):
                raise FlowCommandError(stage, 'request_identity_unverified') from None
        local = self._local_result(stage, exact_result, output, saved.get('original_sha256'))
        if local:
            return local
        retrieval=saved.get('retrieval',{})
        if retrieval.get('candidate'):
            return self._readback_media(diagnostics.with_name(f'flow_{stage}_attempt_{attempt["number"]}.context.json'),
                saved['request_hash'],output,attempt.get('recovery_deadline',now),stage=stage)
        if (attempt.get('failure') or {}).get('category')=='generation_failed':
            return None
        if 'recovery_deadline' not in attempt:
            attempt['recovery_deadline'] = time.time() + min(300, max(0, self._generation_seconds()))
            self._save_attempt(diagnostics, attempt)
        deadline = min(attempt['recovery_deadline'], time.time() + max(0, self._generation_seconds()))
        context_file = diagnostics.with_name(f'flow_{stage}_attempt_{attempt["number"]}.context.json')
        if not context_file.exists():
            return None
        has_identity = bool(saved.get('result') or (attempt.get('failure') or {}).get('media_id') or
                            (attempt.get('failure') or {}).get('operation_id') or
                            diagnostics.with_name(f'flow_{stage}_attempt_{attempt["number"]}.result.json').exists())
        self._notify_transition(diagnostics, f'FLOW_{stage.upper()}_RECOVERY_{attempt["number"]}',
            f'Flow {stage}: ' + ('recovering the exact recorded result' if has_identity else
                'checking the original project for the saved request') + ' within the saved five-minute search budget.')
        recovered = self._readback_media(context_file, saved['request_hash'], output, deadline,
                                         stage=stage, known=exact_result or attempt.get('failure'))
        if recovered:
            self._notify_transition(diagnostics, f'FLOW_{stage.upper()}_RECOVERED_{attempt["number"]}',
                                    f'Flow {stage}: recovered the exact saved result; no replacement was submitted.')
        else:
            self._notify_transition(diagnostics, f'FLOW_{stage.upper()}_RECOVERY_UNAVAILABLE_{attempt["number"]}',
                                    f'Flow {stage}: exact-result readback unavailable; checking the remaining allowance.')
        return recovered

    def _readback_media(self, context_file: Path, request_hash: str, output: Path, deadline: float, *, stage='image', known=None) -> dict | None:
        recovery = output.parent / 'flow_recovered'
        recovery.mkdir(exist_ok=True)
        script = Path(__file__).resolve().parents[2] / 'ops/flow_media_recover.py'
        command = [sys.executable, str(script), os.getenv('FLOW_PREFLIGHT_PROJECT_ID', ''), '--context',
                   str(context_file), str(recovery / (context_file.stem + ('.mp4' if stage == 'video' else '.png'))), str(deadline), request_hash]
        if known:
            media = known.get('media_id') if stage == 'video' else (known.get('images') or [{}])[0].get('media_name')
            if media:
                command.append(media)
                if stage == 'video' and known.get('workflow_id'):
                    command.append(known['workflow_id'])
        diagnostic=output.with_name(f'flow_{stage}_diagnostics.json')
        saved=FlowMediaClient._read_diagnostics(diagnostic)
        workers=saved.get('recovery_workers',{'version':1,'attempts':{}})
        if workers.get('version')!=1:
            raise FlowCommandError(stage,'request_identity_unverified')
        count=workers['attempts'].setdefault(context_file.stem,{'invocations':0,'technical_failures':0})
        technical_errors={'TargetClosedError','BrowserSessionClosedError',
            'NetworkError','TimeoutError','ConnectionError','OSError','ReadbackUnavailable','RecoveryWorkerTimeout'}
        retrieval=saved.get('retrieval',{})
        cutoff=time.time()+max(0,self._generation_seconds())
        slot=int(time.time()//1800)
        if count.get('search_slot')!=slot:
            count.update(search_slot=slot,search_launches=0,
                search_deadline=min(time.time()+300,cutoff,deadline if deadline>time.time() else cutoff))
        if retrieval and (retrieval.get('version')!=1 or retrieval.get('context_file') not in (None,context_file.name)):
            raise FlowCommandError(stage,'request_identity_unverified')
        if retrieval and (type(retrieval.get('attempts')) is not int or not 0 <= retrieval['attempts'] <= 3
                or type(retrieval.get('deadline')) not in (int,float) or not math.isfinite(retrieval['deadline'])):
            raise FlowCommandError(stage,'request_identity_unverified')
        while True:
            candidate=retrieval.get('candidate')
            if candidate:
                if candidate.get('context_sha256')!=hashlib.sha256(context_file.read_bytes()).hexdigest():
                    raise FlowCommandError(stage,'request_identity_unverified')
                if retrieval.get('reply'):
                    self._check_browser_ownership(diagnostic,stage)
                    result=retrieval['reply']
                    try:
                        identity={k:v for k,v in result.items() if k not in {'local_path','sha256'}}
                        if stage=='image':
                            identity['images']=[{k:v for k,v in image.items() if k!='local_path'} for image in identity['images']]
                        if identity!=candidate['result']:
                            raise ValueError('Retrieved result differs')
                    except (AttributeError,KeyError,TypeError,ValueError):
                        raise FlowCommandError(stage,'request_identity_unverified') from None
                    if not re.fullmatch('[0-9a-f]{64}',str(result.get('sha256',''))):
                        raise FlowCommandError(stage,'request_identity_unverified')
                    if not self._local_result(stage,result,output,result['sha256']):
                        raise FlowCommandError(stage,'validation_failed')
                    retrieval.pop('reply')
                    self._write_diagnostics(diagnostic,{'result':result,'retrieval':retrieval})
                    return result
                if retrieval.get('attempts',0)>=3 or min(cutoff,retrieval['deadline'])<=time.time():
                    self._notify_transition(diagnostic,f'FLOW_{stage.upper()}_IDENTIFIED_NOT_RETRIEVED',
                        f'Flow {stage}: identified but not retrieved; no Flow replacement will be submitted.')
                    return None
                mode='retrieve'
                timeout=min(90,min(cutoff,retrieval['deadline'])-time.time())
                self._check_browser_ownership(diagnostic,stage)
                retrieval['attempts']=retrieval.get('attempts',0)+1
                self._write_diagnostics(diagnostic,{'retrieval':retrieval})
            else:
                if count.get('search_launches',0)>=2 or min(count['search_deadline'],cutoff)<=time.time():
                    return None
                mode='search';timeout=min(count['search_deadline'],cutoff)-time.time()
                self._check_browser_ownership(diagnostic,stage)
                count['search_launches']=count.get('search_launches',0)+1
            count['invocations']+=1
            self._write_diagnostics(diagnostic,{'recovery_workers':workers})
            worker_command=command.copy()
            worker_command[6]=str(time.time()+timeout)
            worker_command.extend(['--mode',mode,'--record',str(diagnostic)])
            code, payload = self._recovery_command(worker_command,timeout)
            if payload.get('worker'):
                count['runs']=(count.get('runs',[])+[{'mode':mode,**payload['worker']}])[-5:]
                self._write_diagnostics(diagnostic,{'last_worker':payload['worker'],'recovery_workers':workers})
            if mode=='retrieve' and (code==0 or payload.get('status')=='downloaded') and payload.get('result'):
                retrieval['reply']=payload['result']
                self._write_diagnostics(diagnostic,{'retrieval':retrieval})
            if payload.get('status') == 'identity_unverified':
                raise FlowCommandError(stage, 'request_identity_unverified', detail=f'Saved {stage} readback evidence requires reconciliation.')
            if payload.get('status') == 'generation_failed':
                expected_media = candidate['result']['media_id'] if candidate else media if known else None
                if (stage != 'video' or payload.get('context_sha256') != hashlib.sha256(context_file.read_bytes()).hexdigest()
                        or not payload.get('media_id') or expected_media and payload['media_id'] != expected_media):
                    raise FlowCommandError(stage, 'request_identity_unverified')
                if payload.get('worker', {}).get('processes', {}).get('cleanup_verified') is not True:
                    raise FlowCommandError(stage, 'execution_busy', detail='Failed clip identified; browser cleanup remains unverified.')
                raise FlowCommandError(stage, 'generation_failed', media_id=payload['media_id'],
                    operation_id=payload.get('operation_id', ''), retryable=True, submission_state='forwarded')
            if code == 0:
                if mode=='search':
                    candidate=payload.get('candidate')
                    try:
                        result=candidate['result']
                        observed=candidate.get('observed_at',time.time())
                        if type(observed) not in (int,float) or not math.isfinite(observed) or observed>time.time()+1:
                            raise ValueError('Invalid candidate time')
                        if (candidate['kind']!=stage or result['request_hash']!=request_hash or
                                result['profile']!=self.profile or result['project_id']!=os.getenv('FLOW_PREFLIGHT_PROJECT_ID','')
                                or candidate['context_sha256']!=hashlib.sha256(context_file.read_bytes()).hexdigest()):
                            raise ValueError('Candidate identity differs')
                    except (KeyError,TypeError,ValueError):
                        raise FlowCommandError(stage,'request_identity_unverified') from None
                    retrieval={'version':1,'context_file':context_file.name,'candidate':candidate,
                               'deadline':min(observed+270,cutoff),'attempts':0}
                    self._write_diagnostics(diagnostic,{'retrieval':retrieval})
                    continue
                if not retrieval.get('reply'):
                    raise FlowCommandError(stage,'request_identity_unverified')
                continue
            if code == -1 or payload.get('error_class') in technical_errors:
                count['technical_failures']+=1
            self._write_diagnostics(diagnostic,
                {'recovery_workers':workers,'last_recovery_error':{'class':payload.get('error_class','RecoveryWorkerTimeout' if code == -1 else 'RecoveryWorkerError'),
                                        'phase':'project_readback_download'}})
            if payload.get('error_class') in {'OwnedProcessCleanupError','ProfileLockedError'}:
                raise FlowCommandError(stage,'execution_busy',detail='Browser ownership could not be verified; another launch is blocked.')
            if payload.get('error_class') in {'ContentPolicyError', 'WafRejectionError',
                    'AccountRestrictedError', 'RecaptchaError'}:
                raise FlowCommandError(stage, 'provider_rejected', error_class=payload['error_class'],
                                       detail='Flow recovery encountered a provider restriction; replacement is blocked.')
            if payload.get('error_class') not in technical_errors:
                raise FlowCommandError(stage,'request_identity_unverified')

    @staticmethod
    def _recovery_command(command: list[str], timeout: float) -> tuple[int, dict]:
        process=None
        try:
            process = run_bounded(command, timeout=max(.01, timeout-6))
            code=process.returncode
        except (subprocess.TimeoutExpired,OwnedProcessCleanupError) as exc:
            code=-1 if isinstance(exc,subprocess.TimeoutExpired) else 3
            process=exc
        stderr=getattr(process,'stderr','') or ''
        if isinstance(stderr,bytes):
            stderr=stderr.decode('utf-8','replace')
        phases=[]
        for line in stderr.splitlines():
            if line.startswith('STATE_ZERO_PHASE '):
                try:
                    event=json.loads(line.removeprefix('STATE_ZERO_PHASE '))
                    if event.get('phase') in {'launch','lease','browser','listing','match','editor','download','hash'} and type(event.get('at')) in (int,float):
                        phases.append({'phase':event['phase'],'at':event['at']})
                except (ValueError,TypeError,AttributeError):
                    pass
        category=next((name for name in ('TargetClosedError','TimeoutError','ProfileLockedError','ConnectionError','OSError')
                       if re.search(r'\b'+name+r'\b',stderr)), 'OTHER' if stderr else 'EMPTY')
        payload={}
        stdout=getattr(process,'stdout','') or ''
        if isinstance(stdout,bytes):stdout=stdout.decode('utf-8','replace')
        for line in stdout.splitlines():
            try:
                value=json.loads(line)
                if isinstance(value,dict):
                    payload=value
                    if value.get('status') in {'candidate','downloaded'}:break
            except ValueError:pass
        if code==-1:payload['error_class']='RecoveryWorkerTimeout'
        if isinstance(process,OwnedProcessCleanupError):payload['error_class']='OwnedProcessCleanupError'
        payload['worker']={'phases':phases[-24:],'stderr_category':category,
                          'processes':getattr(process,'process_evidence',{})}
        if (payload.get('status') in {'candidate','downloaded'} and
                payload['worker']['processes'].get('cleanup_verified') is True):
            code=0
        return code,payload

    def _image_receipt(self, diagnostics: Path, attempt: dict) -> dict | None:
        receipt = diagnostics.with_name(f'flow_image_attempt_{attempt["number"]}.result.json')
        if not receipt.exists():
            return None
        try:
            result = json.loads(receipt.read_text())
            saved = FlowMediaClient._read_diagnostics(diagnostics)
            marker = json.loads(diagnostics.with_name(f'flow_image_attempt_{attempt["number"]}.submit').read_text())
            if (result['request_hash'] != saved['request_hash'] or result['profile'] != self.profile or
                    result['project_id'] != os.getenv('FLOW_PREFLIGHT_PROJECT_ID', '') or
                    result['count'] != 1 or result['model'] != 'NARWHAL' or len(result['images']) != 1 or
                    not result['images'][0]['media_name'] or
                    result['images'][0]['model_name_type'] not in (None, 'BELUGA') or
                    result['images'][0]['aspect_ratio'] != 'IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR' or
                    marker.get('state') != 'forwarded' or marker.get('actual_model') != 'BELUGA' or
                    marker.get('expected_model') != 'NARWHAL' or marker.get('selected_model') != 'Nano Banana 2.1'):
                raise ValueError('Receipt identity differs')
            return result
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise FlowCommandError('image', 'request_identity_unverified') from None

    def _video_receipt(self, diagnostics: Path, attempt: dict) -> dict | None:
        receipt=diagnostics.with_name(f'flow_video_attempt_{attempt["number"]}.result.json')
        if not receipt.exists():
            return None
        try:
            from flow_readback import identity_hash
            result=json.loads(receipt.read_text());saved=FlowMediaClient._read_diagnostics(diagnostics)
            context=json.loads(receipt.with_name(receipt.name.replace('.result.json','.context.json')).read_text())
            marker=json.loads(
                receipt.with_name(receipt.name.replace('.result.json','.submit')).read_text())
            if (result['request_hash'] != saved['request_hash'] or result['profile'] != self.profile
                    or result['project_id'] != os.getenv('FLOW_PREFLIGHT_PROJECT_ID','')
                    or not re.fullmatch(r'[0-9a-f-]{36}',result['media_id']) or not re.fullmatch(r'[0-9a-f-]{36}',result['workflow_id'])
                    or result.get('succeeded') is not True
                    or result['request'] != {'count':1,'model':'veo_3_1_fast','mode':'i2v','aspect':'portrait'}
                    or marker['state'] != 'forwarded' or marker['readback_hash'] != identity_hash(context)):
                raise ValueError('Video receipt identity differs')
            return result
        except (OSError,ValueError,KeyError,TypeError,AttributeError):
            raise FlowCommandError('video','request_identity_unverified') from None

    def _validate_original(self, stage: str, path: Path, expected_hash: str | None = None) -> tuple:
        if expected_hash and hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise FlowCommandError(stage, 'original_asset_changed')
        try:
            if stage == 'image':
                with Image.open(path) as image:
                    image.load()
                    if image.width < 512 or image.height < 512 or abs(image.width / image.height - .75) > .015:
                        raise ValueError('Invalid image dimensions')
                    return image.size
            width, height, duration, audio = self._probe_video(path)
            if (width, height) not in ((720, 1280), (1080, 1920)) or not 7.5 <= duration <= 8.5 or not audio:
                raise ValueError('Invalid video properties')
            return width, height, duration, audio
        except (OSError, ValueError, KeyError, IndexError, StopIteration, subprocess.SubprocessError) as exc:
            raise FlowCommandError(stage, 'asset_download_failed', error_class=type(exc).__name__,
                                   submission_state='attempted') from exc

    def image_command(self, prompt_json: dict, raw_path: Path) -> list[str]:
        command = [
            "gflow", "image", "t2i", GoogleImageClient._build_prompt_from_json(prompt_json),
            "--model", "nano2", "--aspect", "3:4", "--count", "1",
            "--output", str(raw_path), "--profile", self.profile, "--json",
        ]
        if project_id := os.getenv("FLOW_PREFLIGHT_PROJECT_ID", "").strip():
            command.extend(("--project", project_id))
        return command

    def video_command(self, prompt: str, start_frame: Path, raw_path: Path) -> list[str]:
        command = [
            "gflow", "video", "i2v", "--initial-frame", str(start_frame), prompt,
            "--model", "veo-fast", "--aspect", "9:16", "--count", "1",
        ]
        if project_id := os.getenv("FLOW_PREFLIGHT_PROJECT_ID", "").strip():
            command.extend(("--project", project_id))
        if os.getenv("FLOW_VIDEO_EXPLICIT_DURATION", "false").lower() == "true":
            command.extend(("--duration", "8"))
        # The CLI renames its download to --output; keep both on the same volume.
        command.extend(("--out-dir", str(raw_path.parent), "--output", str(raw_path),
                        "--profile", self.profile, "--json"))
        return command

    @staticmethod
    def _run_json(command: list[str], timeout: int, marker: Path | None = None) -> dict:
        env = os.environ.copy()
        client = FlowMediaClient(command[command.index('--profile') + 1] if '--profile' in command else None)
        if marker is not None:
            FlowMediaClient._check_browser_ownership(marker.with_name(f'flow_{command[1]}_diagnostics.json'),command[1])
            env["GFLOW_SHADOW_SUBMIT_MARKER"] = str(marker)
            env['GFLOW_PROFILE'] = client.profile
            env['PYTHONPATH'] = str(Path(__file__).resolve().parent) + os.pathsep + env.get('PYTHONPATH', '')
            if (marker.parent / 'flow_inputs.json').is_file():
                env['GFLOW_SHADOW_REQUEST_HASH'] = json.loads(marker.with_name(f'flow_{command[1]}_diagnostics.json').read_text())['request_hash']
                timeout = min(timeout, max(.01, FlowMediaClient._generation_seconds()))
        try:
            result = run_bounded(command, timeout=timeout, env=env)
        except OwnedProcessCleanupError as exc:
            if marker is not None:
                FlowMediaClient._write_diagnostics(marker.with_name(f'flow_{command[1]}_diagnostics.json'),
                    {'last_generation_process':exc.process_evidence})
            result=exc
        except subprocess.TimeoutExpired as exc:
            if marker is not None:
                FlowMediaClient._write_diagnostics(marker.with_name(f'flow_{command[1]}_diagnostics.json'),
                    {'last_generation_process':getattr(exc,'process_evidence',{})})
            submitted = bool(marker and marker.exists())
            raise FlowCommandError(command[1], "submission_uncertain" if submitted else "pre_submit_timeout",
                                   submission_state="uncertain" if submitted else "not_attempted") from None
        if marker is not None and isinstance(getattr(result,'process_evidence',None),dict):
            FlowMediaClient._write_diagnostics(marker.with_name(f'flow_{command[1]}_diagnostics.json'),
                {'last_generation_process':result.process_evidence})
        try:
            payload = json.loads(result.stdout)
        except (ValueError,TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if isinstance(result,OwnedProcessCleanupError):
            if result.returncode!=0 or payload.get('status')!='ok':
                raise FlowCommandError(command[1],'execution_busy',detail='Owned browser cleanup could not be verified.')
            if marker is not None:
                client._notify_transition(marker.with_name(f'flow_{command[1]}_diagnostics.json'),
                    'FLOW_BROWSER_CLEANUP_BLOCKED',
                    'Flow result retained; browser cleanup is uncertain. The next browser launch is blocked until ownership is safe.')
        if result.returncode or payload.get("status") != "ok":
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            error_class = re.sub(r"[^A-Za-z0-9_]", "", str(error.get("class", "")))[:80]
            media_id = str(payload.get("media_id") or "")
            failed = payload.get("generation_status") == "MEDIA_GENERATION_STATUS_FAILED" and bool(media_id)
            # Only decoded provider verdicts may restrict spending, never exception prose.
            reasons = payload.get("failure_reasons", [])
            malformed = not isinstance(reasons, list) or any(not isinstance(v, str) for v in reasons)
            verdicts = {v for v in reasons if isinstance(v, str)} if isinstance(reasons, list) else set()
            # Malformed evidence cannot authorize another generation or API call.
            failed = failed and not malformed
            unknown_verdict = bool(verdicts - POLICY_VERDICTS - CREDIT_VERDICTS) or error_class == 'UnknownFlowRefusalError'
            credits = error_class == 'InsufficientCreditsError' or bool(verdicts) and verdicts <= CREDIT_VERDICTS
            restricted = bool(verdicts & POLICY_VERDICTS) or error_class in {
                'ContentPolicyError', 'WafRejectionError', 'AccountRestrictedError', 'RecaptchaError'}
            marker_exists = bool(marker and marker.exists())
            clicked = marker_exists or bool(media_id)
            submission_state = "attempted" if marker_exists else "uncertain" if media_id else "not_attempted"
            if marker_exists:
                try:
                    submission_state = json.loads(marker.read_text()).get("state", "attempted")
                except (ValueError, OSError):
                    pass
            detail = str(error.get("detail") or "")
            if verdicts:
                detail += ' [provider reason: decoded verdict requires reconciliation]'
            for value in command[2:]:
                if len(value) > 10 and not value.startswith("--"):
                    detail = detail.replace(value, "[redacted]")
            detail = re.sub(r"https?://\S+|Bearer\s+\S+|(?:SAPISID|SID|Cookie)\s*[:=]\s*\S+|sk-or-v1-[\w-]+|AIza[\w-]+", "[redacted]", detail, flags=re.I)
            detail = sanitize_error(detail)[:500]
            remote_operation_id = str(payload.get("operation_id") or "")
            operation_id = ''
            auth_error = error_class in {
                "AuthExpiredError", "AisandboxAuthError", "AuthMissingError",
                "AuthLoginTimeoutError", "IdentityRecheckPendingError", "FlowAccountChooserError",
            }
            if restricted:
                category = "provider_rejected"
            elif submission_state == "blocked_before_submission" and not malformed:
                category = "blocked_before_submission"
            elif failed:
                category = "generation_failed"
            elif malformed:
                category = 'request_identity_unverified'
            elif unknown_verdict:
                category = 'post_submit_error' if clicked else 'pre_submit_transient'
            elif credits:
                category = 'credits_exhausted'
            elif error_class == 'UploadResponseUnavailableError' and not clicked:
                category = 'pre_submit_transient'
            elif auth_error:
                category = "auth_required"
            elif clicked:
                category = "post_submit_error"
            elif bool(error.get("retryable")):
                category = "pre_submit_transient"
            else:
                category = "pre_submit_terminal"
            if marker is not None:
                response = {}
                context_file = marker.with_suffix('.context.json')
                if context_file.exists():
                    try:
                        response = json.loads(context_file.read_text()).get('response', {})
                    except (ValueError, OSError):
                        raise FlowCommandError(command[1], 'request_identity_unverified') from None
                FlowMediaClient._write_diagnostics(marker.with_name(f'flow_{command[1]}_diagnostics.json'),
                    {'last_cli_error': {'phase': 'upload' if error_class in {'MediaUploadRejectedError','UploadResponseUnavailableError'} else 'submit_response',
                        'class': error_class, 'detail': detail, 'http_status': error.get('status') or response.get('http_status'),
                        'catalog_operation_id': operation_id, 'remote_operation_id': remote_operation_id,
                        'rpc': re.sub(r'[^A-Za-z0-9:._-]', '', str(error.get('route') or response.get('rpc') or ''))[:80],
                        'response': response}})
            raise FlowCommandError(command[1], category, error_class=error_class,
                                   media_id=media_id, retryable=category == 'generation_failed',
                                   detail=detail, operation_id=operation_id, submission_state=submission_state)
        return payload

    @staticmethod
    def _owned_path(raw: str, directory: Path) -> Path:
        path = Path(raw).resolve(strict=True)
        if not path.is_relative_to(directory.resolve(strict=True)) or not path.is_file():
            raise RuntimeError("gflow returned an output outside the run directory")
        return path

    @staticmethod
    def _read_diagnostics(path: Path) -> dict:
        if not path.exists():
            return {}
        if path.name not in MEDIA_RECORDS:
            return json.loads(path.read_text())
        try:
            saved = json.loads(path.read_text())
        except (OSError, ValueError):
            saved = None
        if not isinstance(saved, dict) or type(saved.get('record_version')) is not int or saved['record_version'] != 2:
            raise FlowCommandError('video' if 'video' in path.name else 'image', 'request_identity_unverified',
                detail='Existing media record requires completed-date cutover; stop and reconcile before resuming.')
        return saved

    @staticmethod
    def _write_diagnostics(path: Path, data: dict) -> None:
        if path.name in MEDIA_RECORDS:
            previous = FlowMediaClient._read_diagnostics(path)
            data = data | {'record_version': 2}
            if isinstance(data.get('result'), dict):
                data['result'] = {k:v for k,v in data['result'].items() if k != 'succeeded'}
        else:
            previous = json.loads(path.read_text()) if path.exists() else {}
        staged = path.with_name(path.name + ".tmp")
        with os.fdopen(os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
            json.dump(previous | data, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        staged.replace(path)

    def _generate_image(self, prompt_json: dict, output_path: Path, recovered: dict | None = None) -> Path:
        started_at = datetime.now(timezone.utc).isoformat()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        diagnostics = output_path.with_name("flow_image_diagnostics.json")
        saved = FlowMediaClient._read_diagnostics(diagnostics)
        previous = list(output_path.parent.glob("flow_image_attempt_*.submit"))
        marker = max(previous, key=lambda p: int(p.stem.rsplit("_", 1)[-1])) if previous else None
        payload = recovered
        if recovered is None:
            number = saved['pending_attempt']['number']
            marker = output_path.with_name(f"flow_image_attempt_{number}.submit")
            self._write_diagnostics(diagnostics, {'provider':'flow', 'started_at':started_at})
            try:
                payload = self._run_json(self.image_command(prompt_json,
                    output_path.with_name(f'flow_art_attempt_{number}.png')), 600, marker)
                self._write_diagnostics(diagnostics, {"result": {
                    "count": payload.get("count"), "model": payload.get("model"), "project_id": payload.get("project_id"),
                    "images": [{k: image.get(k) for k in ("local_path", "media_name", "model_name_type")} for image in payload.get("images", [])]}})
            except FlowCommandError as exc:
                if exc.category == "post_submit_error" and exc.media_id:
                    self._write_diagnostics(diagnostics, {"result": {"count": 1, "model": "NARWHAL",
                        "project_id": os.getenv("FLOW_PREFLIGHT_PROJECT_ID", ""),
                        "images": [{"local_path": None, "media_name": exc.media_id, "model_name_type": None}]}})
                raise
        images = payload.get("images") or []
        if payload.get("count") != 1 or len(images) != 1:
            raise RuntimeError("Flow did not return exactly one image")
        item = images[0]
        wire_model = item.get("model_name_type")
        try:
            submission = json.loads(marker.read_text()) if marker and marker.exists() else {}
        except (OSError, ValueError):
            submission = {}
        beluga_confirmed = (submission.get("state") == "forwarded" and
                            submission.get("expected_model") == "NARWHAL" and
                            submission.get("actual_model") == "BELUGA" and
                            submission.get("selected_model") == "Nano Banana 2.1")
        if (payload.get("model") != "NARWHAL" or
                wire_model not in (None, "BELUGA") or
                not beluga_confirmed):
            raise FlowCommandError('image', 'provider_rejected', detail='Flow image MODEL differs from requested settings')
        try:
            if not item.get("local_path"):
                raise FileNotFoundError("Image download missing")
            source = self._owned_path(item["local_path"], output_path.parent)
        except FileNotFoundError:
            raise FlowCommandError('image', 'post_submit_error', media_id=item.get('media_name') or '',
                                   submission_state='forwarded') from None
        source = self._owned_path(str(source), output_path.parent)
        original = source.name
        width, height = self._validate_original('image', source, saved.get('original_sha256'))
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        self._write_diagnostics(diagnostics, {"original_sha256": source_hash, "result": payload})
        try:
            upscaled = False
            if not recovered and os.getenv("FLOW_IMAGE_UPSCALE_2K", "false").lower() == "true" and self._generation_seconds() >= 300:
                media_id, project_id = item.get("media_name"), payload.get("project_id")
                if media_id and project_id:
                    upscale_dir = output_path.parent / "flow_upscale"
                    upscale_dir.mkdir(exist_ok=True)
                    command = [
                        "gflow", "image", "upscale", media_id, "--scale", "2k",
                        "--project", project_id, "--out", str(upscale_dir), "--profile", self.profile,
                    ]
                    try:
                        self._check_browser_ownership(diagnostics,'image')
                        result = run_bounded(command, timeout=300)
                        matches = list(upscale_dir.rglob(f"{media_id}_2k.*")) if result.returncode == 0 else []
                        if len(matches) == 1:
                            candidate = self._owned_path(str(matches[0]), output_path.parent)
                            self._validate_original('image', candidate)
                            source = candidate
                            upscaled = True
                    except OwnedProcessCleanupError as exc:
                        self._write_diagnostics(diagnostics,{'last_generation_process':exc.process_evidence})
                        self._notify_transition(diagnostics,'FLOW_BROWSER_CLEANUP_BLOCKED',
                            'Original image retained; the next browser launch requires verified cleanup.')
                    except (FlowCommandError, OSError, subprocess.SubprocessError):
                        pass  # Keep the original image; the resolution gap stays visible in diagnostics.
            with Image.open(source) as image:
                image.load()
                width, height = image.size
                if width < 512 or height < 512 or abs(width / height - 0.75) > 0.015:
                    raise RuntimeError(f"Flow image has invalid dimensions: {width}x{height}")
                staged = output_path.with_name('.generated_art.staged.png')
                image.convert("RGB").save(staged, "PNG")
                staged.replace(output_path)
            self._write_diagnostics(output_path.with_name("flow_image_diagnostics.json"), {
                "provider": "flow", "started_at": started_at,
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "model": "Nano Banana 2.1", "selected_model": submission.get("selected_model"), "wire_model": submission.get("actual_model"), "response_model": wire_model, "model_attribution_confirmed": beluga_confirmed,
                "aspect": "3:4", "source_width": width,
                "source_height": height, "flow_2k_upscaled": upscaled,
                "media_id": item.get("media_name"), "project_id": payload.get("project_id"),
                "original_file": original, "source_file": str(source.relative_to(output_path.parent.resolve())),
            })
        except (OSError, subprocess.SubprocessError) as exc:
            try:
                self._write_diagnostics(diagnostics, {'processing_failure': {'category':'local_processing_failed',
                    'error_class':type(exc).__name__,'retryable':True}})
            except OSError:
                pass  # Preserve the local failure class even when the failing disk cannot record it.
            raise FlowCommandError('image', 'local_processing_failed',
                error_class=type(exc).__name__, submission_state='attempted', retryable=True) from exc
        return output_path

    @staticmethod
    def _probe_video(path: Path, timeout: float = 30) -> tuple[int, int, float, bool]:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,width,height:format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=timeout, check=True,
        )
        payload = json.loads(result.stdout)
        stream = next(item for item in payload["streams"] if item["codec_type"] == "video")
        return (int(stream["width"]), int(stream["height"]), float(payload["format"]["duration"]),
                any(item["codec_type"] == "audio" for item in payload["streams"]))

    def _generate_video(self, prompt_text: str, image_path: Path, output_path: Path, recovered: dict | None = None) -> Path:
        started_at = datetime.now(timezone.utc).isoformat()
        if not prompt_text.strip():
            raise ValueError("Video prompt is empty")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        start_frame = output_path.with_name("flow_start_frame.png")
        if recovered is None:
            framed, _ = GoogleVideoClient._to_letterboxed_png_bytes(image_path)
            start_frame.write_bytes(framed)
        raw_path = output_path.with_name("flow_raw_video.mp4")
        diagnostics = output_path.with_name("flow_video_diagnostics.json")
        saved = FlowMediaClient._read_diagnostics(diagnostics)
        payload = recovered or {}
        if recovered is None:
            number = saved['pending_attempt']['number']
            marker = output_path.with_name(f"flow_video_attempt_{number}.submit")
            self._write_diagnostics(diagnostics, {'provider':'flow', 'started_at':started_at})
            payload = self._run_json(self.video_command(prompt_text, start_frame,
                output_path.with_name(f'flow_video_attempt_{number}.mp4')), 1500, marker)
            self._write_diagnostics(diagnostics, {"result": {k: payload.get(k) for k in ("media_id", "workflow_id", "local_path")} | {
                "request": {k: (payload.get("request") or {}).get(k) for k in ("count", "model", "mode", "aspect")}}})
        try:
            if not payload.get('local_path'):
                raise FileNotFoundError('Video download missing')
            source = self._owned_path(payload['local_path'], output_path.parent)
        except FileNotFoundError:
            raise FlowCommandError('video', 'post_submit_error', media_id=payload.get('media_id') or '',
                                   submission_state='attempted') from None
        request = payload.get("request") or {}
        if (not payload.get("succeeded") or request.get("count") != 1 or
                request.get("model") != "veo_3_1_fast" or request.get("mode") != "i2v" or
                request.get("aspect") != "portrait"):
            raise FlowCommandError('video', 'provider_rejected', detail='Flow video MODEL or SETTINGS differ')
        width, height, duration, has_audio = self._validate_original('video', source, saved.get('original_sha256'))
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        payload = payload | {"local_path": str(source)}
        self._write_diagnostics(diagnostics, {"original_sha256": source_hash, "result": payload})
        try:
            if source.resolve() != raw_path.resolve():
                shutil.copyfile(source, raw_path)
            selected = source
            upscale_status = "not_needed" if width == 1080 else "disabled"
            if not recovered and width == 720 and os.getenv("FLOW_VIDEO_UPSCALE_1080P", "true").lower() == "true" and self._generation_seconds() >= 240:
                project_id = os.getenv("FLOW_PREFLIGHT_PROJECT_ID", "").strip()
                media_id = str(payload.get("media_id") or "")
                upscale_status = "unavailable"
                if project_id and media_id:
                    candidate = output_path.with_name("flow_1080p_download.mp4")
                    script = Path(__file__).resolve().parents[2] / "ops" / "flow_video_upscale.py"
                    try:
                        self._check_browser_ownership(diagnostics,'video')
                        result = run_bounded([sys.executable, str(script), project_id, media_id, str(candidate),
                            '--workflow', str(payload.get('workflow_id') or ''), '--deadline', str(time.time()+240)], timeout=240)
                        upscale_status = json.loads(result.stdout).get("status", "unavailable")
                        if result.returncode == 0 and candidate.is_file():
                            up_width, up_height, up_duration, up_audio = self._probe_video(candidate)
                            if ((up_width, up_height) == (1080, 1920) and abs(up_duration - duration) <= 0.5
                                    and up_audio == has_audio):
                                selected = candidate
                                upscale_status = "validated"
                            else:
                                upscale_status = "invalid_media"
                    except OwnedProcessCleanupError as exc:
                        self._write_diagnostics(diagnostics,{'last_generation_process':exc.process_evidence})
                        self._notify_transition(diagnostics,'FLOW_BROWSER_CLEANUP_BLOCKED',
                            'Original video retained; the next browser launch requires verified cleanup.')
                        upscale_status='ownership_blocked'
                    except (FlowCommandError, OSError, ValueError, subprocess.SubprocessError, KeyError, IndexError, StopIteration):
                        upscale_status = "invalid_media" if candidate.exists() else "unavailable"
            staged = output_path.with_name(".generated_video.staged.mp4")
            if selected == source and (width, height) == (720, 1280):
                result = run_bounded(
                    ["ffmpeg", "-nostdin", "-y", "-i", str(source), "-vf",
                     "scale=1080:1920:flags=lanczos", "-c:v", "libx264", "-preset", "medium",
                     "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags",
                     "+faststart", str(staged)],
                    timeout=min(600, max(.001,float(os.getenv("STATE_ZERO_LOCAL_ONLY_DEADLINE", str(time.time()+600)))-time.time())),
                )
                result.check_returncode()
            else:
                shutil.copyfile(selected, staged)
            staged.replace(output_path)
            self._write_diagnostics(output_path.with_name("flow_video_diagnostics.json"), {
                "provider": "flow", "started_at": started_at,
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "model": "veo-fast", "aspect": "9:16", "media_id": payload.get("media_id"),
                "source_width": width, "source_height": height, "duration_seconds": duration,
                "source_has_audio": has_audio, "locally_scaled_to_1080p": selected == source and width == 720,
                "upscale_status": upscale_status, "selected_video_file": selected.name,
                "start_frame": start_frame.name,
            })
        except (OSError, subprocess.SubprocessError) as exc:
            try:
                self._write_diagnostics(diagnostics, {'processing_failure': {'category':'local_processing_failed',
                    'error_class':type(exc).__name__,'retryable':True}})
            except OSError:
                pass  # Preserve the local failure class even when the failing disk cannot record it.
            raise FlowCommandError('video', 'local_processing_failed',
                error_class=type(exc).__name__, submission_state='attempted', retryable=True) from exc
        return output_path

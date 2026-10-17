"""Narrow adapter from State Zero's media files to the pinned gflow CLI."""

import json
import fcntl
import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from google_image_client import GoogleImageClient
from google_video_client import GoogleVideoClient


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
        reason = (error.error_class + ' ' + error.detail).upper()
        return (error.category in {'request_identity_unverified', 'original_asset_changed', 'execution_busy', 'validation_failed', 'provider_rejected', 'pre_submit_terminal'} or
                error.category == 'generation_failed' and not error.retryable or
                any(word in reason for word in ('SAFETY', 'POLICY', 'RESTRICT', 'REFUSAL', 'MODEL', 'SETTING')) or
                'RECAPTCHA' in reason and error.category not in {'pre_submit_transient', 'pre_submit_timeout'})

    def _notify_transition(self, diagnostics: Path, status: str, message: str) -> None:
        saved = json.loads(diagnostics.read_text())
        if saved.get('last_notification') == status:
            return
        from notifier import safe_notify_status
        from utils import get_pipeline_run_date_str
        if safe_notify_status(get_pipeline_run_date_str(), status, message):
            self._write_diagnostics(diagnostics, {'last_notification': status})

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
                saved = json.loads(path.read_text()) if path.exists() else {}
                if not isinstance(saved, dict):
                    saved = {}
                    raise ValueError("Invalid diagnostics")
                failure = saved.get("failure") or {}
                if not isinstance(failure, dict):
                    failure = {}
                    raise ValueError("Invalid failure")
                states = [json.loads(marker.read_text())["state"] for marker in markers]
                canonical = directory / name
                if (saved.get("output_sha256") and canonical.is_file() and not failure
                        and hashlib.sha256(canonical.read_bytes()).hexdigest() == saved["output_sha256"]):
                    continue
                if (all(state == "blocked_before_submission" for state in states)
                        and failure.get("category") in {"pre_submit_transient", "pre_submit_timeout", "auth_required", "blocked_before_submission"}
                        and failure.get("submission_state") in {"not_attempted", "blocked_before_submission"}):
                    continue
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

    @staticmethod
    def _api_fallback_allowed(error: Exception) -> bool:
        if not isinstance(error, FlowCommandError):
            return False
        reason = (error.error_class + " " + error.detail).upper()
        if any(word in reason for word in ("SAFETY", "POLICY", "REJECT", "REFUSAL", "QUOTA", "CREDIT", "RESTRICT", "MODEL", "SETTING", "RECAPTCHA")):
            return False
        if error.category == "generation_failed":
            return error.retryable
        return (error.submission_state in ("", "not_attempted") and
                error.category in {"pre_submit_transient", "pre_submit_timeout", "auth_required"})

    def generate_image(self, prompt_json: dict, output_path: Path) -> Path:
        return self._generate_guarded("image", prompt_json, output_path,
                                      lambda result: self._generate_image(prompt_json, output_path, result))

    def generate_video(self, prompt_text: str, image_path: Path, output_path: Path) -> Path:
        request = {"prompt": prompt_text, "start_frame_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest()}
        return self._generate_guarded("video", request, output_path,
                                      lambda result: self._generate_video(prompt_text, image_path, output_path, result), image_path)

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
            previous = json.loads(diagnostics.read_text()) if diagnostics.exists() else {}
            if not isinstance(previous, dict):
                raise FlowCommandError(stage, 'request_identity_unverified')
            frozen = self.validate_inputs(output.parent)
            if frozen:
                expected = (json.loads((output.parent / 'image_prompt.json').read_text()) if stage == 'image'
                            else (output.parent / 'video_prompt.txt').read_text().strip())
                if (request if stage == 'image' else request['prompt']) != expected:
                    raise FlowCommandError(stage, 'request_identity_unverified')
            markers = list(output.parent.glob(f"flow_{stage}_attempt_*.submit"))
            if (previous or markers or output.exists()) and previous.get("request_hash") != fingerprint:
                raise FlowCommandError(stage, "request_identity_unverified")
            if frozen and previous.get('selected_sha256'):
                name = previous.get('selected_file', '')
                candidates = [output] + ([output.parent / name] if Path(name).name == name else [])
                for candidate in candidates:
                    if candidate.is_file() and hashlib.sha256(candidate.read_bytes()).hexdigest() == previous['selected_sha256']:
                        return self._finish_daily(stage, output, diagnostics, previous['provider'], candidate)
            if previous.get("output_sha256") and output.is_file():
                if hashlib.sha256(output.read_bytes()).hexdigest() == previous["output_sha256"]:
                    # Downstream stale-file checks use timestamps; identity was checked above.
                    output.touch()
                    if previous.get("fallback_alert_pending"):
                        self._notify_api_fallback(stage, diagnostics)
                    return output
            if frozen:
                self._write_diagnostics(diagnostics, {'request_hash': fingerprint, 'daily_policy': True})
                return self._generate_daily(stage, request, output, generate, image_path)
            # Legacy evidence has no full-input manifest; keep its stricter reconciliation rule.
            if previous.get("api_fallback_state"):
                raise FlowCommandError(stage, "submission_uncertain", submission_state="api_attempted")
            result = previous.get("result")
            self._write_diagnostics(diagnostics, {"request_hash": fingerprint})
            try:
                try:
                    if result is None and (previous.get("failure") or previous.get("generation_started") or markers):
                        failure = previous.get("failure") or {"category": "submission_uncertain"}
                        resume = (os.getenv("FLOW_SHADOW_RECOVERY_LOCKED") == "true" and not markers
                                  and failure.get("submission_state") == "not_attempted"
                                  and failure.get("category") in {"auth_required", "pre_submit_transient", "pre_submit_timeout"}
                                  and len(previous.get("attempts", [])) < 2)
                        if not resume:
                            raise FlowCommandError(stage, **failure)
                    self._write_diagnostics(diagnostics, {"generation_started": True})
                    generate(result)
                except Exception as error:
                    if not (os.getenv("FLOW_API_FALLBACK_ENABLED", "false").lower() == "true" and self._api_fallback_allowed(error)):
                        raise
                    self._write_diagnostics(diagnostics, {"api_fallback_state": "started", "fallback_reason": error.category})
                    client = GoogleImageClient() if stage == "image" else GoogleVideoClient()
                    client.router.retry_ambiguous_calls = False
                    if stage == "image":
                        client.generate_from_json(request, output)
                    else:
                        client.generate_from_image(request["prompt"], image_path, output)
                    self._write_diagnostics(diagnostics, {"provider": "google_api", "api_fallback_state": "complete"})
                    self._notify_api_fallback(stage, diagnostics)
                if stage == "image":
                    with Image.open(output) as image:
                        image.load()
                        if image.width < 512 or image.height < 512:
                            raise ValueError("Generated image is too small")
                else:
                    width, height, duration, audio = self._probe_video(output)
                    if (width, height) != (1080, 1920) or not 7.5 <= duration <= 8.5 or not audio:
                        raise ValueError("Canonical video has invalid dimensions, duration or audio")
                self._write_diagnostics(diagnostics, {"output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "failure": None})
                return output
            except Exception as error:
                failure = {"category": "validation_failed"}
                if isinstance(error, FlowCommandError):
                    failure = {key: getattr(error, key) for key in ("category", "error_class", "media_id", "retryable", "submission_state", "detail", "operation_id")}
                self._write_diagnostics(diagnostics, {"failure": failure})
                raise

    def _notify_api_fallback(self, stage: str, diagnostics: Path) -> None:
        from notifier import notify_warning
        from utils import get_pipeline_run_date_str
        try:
            sent = notify_warning(get_pipeline_run_date_str(), f"flow_{stage}_fallback",
                                  f"💸 Flow {stage} failed; paid Google API {stage} generation was used.")
        except Exception:
            sent = False
        self._write_diagnostics(diagnostics, {"fallback_alert_pending": not sent})

    @staticmethod
    def _daily_attempts(stage: str, diagnostics: Path) -> list[dict]:
        saved = json.loads(diagnostics.read_text())
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
                prior_state = (attempt.get('failure') or {}).get('submission_state')
                if blocked and prior_state in {'forwarded', 'attempted', 'uncertain'} or not blocked and prior_state in {'not_attempted', 'blocked_before_submission'}:
                    raise invalid
                attempt['validation_block'] = blocked and bool(evidence.get('reason'))
            except (OSError, ValueError, AttributeError):
                raise invalid from None
            attempt['submit_attempted'] = not blocked
        if sum(a.get('submit_attempted', True) for a in by_number.values()) > 2 or sum(not a.get('submit_attempted', True) for a in by_number.values()) > 2:
            raise FlowCommandError(stage, 'request_identity_unverified')
        return sorted(by_number.values(), key=lambda a: a['number'])

    def _save_attempt(self, diagnostics: Path, attempt: dict) -> None:
        saved = json.loads(diagnostics.read_text())
        attempts = [a for a in saved.get('attempts', []) if a['number'] != attempt['number']]
        attempts.append(attempt)
        self._write_diagnostics(diagnostics, {'attempts': attempts, 'pending_attempt': None,
                                              **({'failure': attempt['failure']} if attempt.get('failure') else {})})

    def _generate_daily(self, stage: str, request: dict, output: Path, generate, image_path: Path | None) -> Path:
        diagnostics = output.with_name(f'flow_{stage}_diagnostics.json')
        flow_timeout = 600 if stage == 'image' else 1500
        api_timeout = 180 if stage == 'image' else int(os.getenv('GOOGLE_VIDEO_TIMEOUT_SECONDS', '900')) + 480
        while True:
            saved = json.loads(diagnostics.read_text())
            attempts = self._daily_attempts(stage, diagnostics)
            if any(a.get('validation_block') for a in attempts):
                raise FlowCommandError(stage, 'provider_rejected', detail='Recorded Flow SETTINGS validation failed before forwarding.')
            last = attempts[-1] if attempts else {}
            failure = saved.get('failure') or last.get('failure') or {}
            if not failure and last.get('status') not in (None, 'reserved', 'complete', 'recovered_existing_media'):
                failure = {'category': last['status']} | {k: last[k] for k in
                    ('error_class', 'detail', 'operation_id', 'submission_state', 'retryable') if k in last}
            error = FlowCommandError(stage, **failure) if failure else None
            if error and self._stops_replacement(error):
                raise error
            if saved.get('api_fallback_state'):
                if stage != 'video':
                    raise FlowCommandError(stage, 'submission_uncertain', submission_state='api_attempted')
                client = GoogleVideoClient()
                client.generation_deadline = time.time() + max(0, self._generation_seconds())
                candidate = output.with_name(f'.{output.stem}.api{output.suffix}')
                client.resume_operation(candidate)
                return self._finish_daily(stage, output, diagnostics, 'google_api', candidate)
            if attempts and attempts[-1].get('submit_attempted', True) and not attempts[-1].get('recovery_exhausted'):
                attempt = attempts[-1]
                recovered = self._recover_pending(stage, diagnostics, attempt, output)
                if recovered:
                    try:
                        generate(recovered)
                        return self._finish_daily(stage, output, diagnostics, 'flow')
                    except FlowCommandError as exc:
                        if self._stops_replacement(exc):
                            raise
                attempt['recovery_exhausted'] = True
                self._save_attempt(diagnostics, attempt)
                continue
            used = sum(a.get('submit_attempted', True) for a in attempts)
            pre_submit = sum(not a.get('submit_attempted', True) for a in attempts)
            skip_flow = error and (error.category in {'auth_required', 'credits_exhausted'} or pre_submit >= 2)
            if used < 2 and not skip_flow and self._generation_seconds() >= flow_timeout:
                number = max([a['number'] for a in attempts], default=0) + 1
                attempt = {'number': number, 'status': 'reserved', 'submit_attempted': True,
                           'reserved_at': time.time()}
                self._write_diagnostics(diagnostics, {'pending_attempt': attempt, 'result': None,
                    'failure': None, 'original_sha256': None, 'generation_started': True})
                if used:
                    self._notify_transition(diagnostics, f'FLOW_{stage.upper()}_REPLACEMENT',
                                            f'Flow {stage}: using replacement submission 2 of 2 with saved inputs.')
                try:
                    generate(None)
                    self._save_attempt(diagnostics, attempt | {'status': 'complete'})
                    return self._finish_daily(stage, output, diagnostics, 'flow')
                except FlowCommandError as exc:
                    submitted = exc.submission_state not in {'not_attempted', 'blocked_before_submission'}
                    failure = {k: getattr(exc, k) for k in ('category', 'error_class', 'media_id', 'retryable',
                                                          'detail', 'operation_id', 'submission_state')}
                    attempt.update(status=exc.category, submit_attempted=submitted, failure=failure,
                                   recovery_exhausted=exc.category == 'generation_failed' and exc.retryable)
                    self._save_attempt(diagnostics, attempt)
                    self._write_diagnostics(diagnostics, {'failure': failure})
                    if self._stops_replacement(exc):
                        raise
                    if not submitted and exc.category not in {'pre_submit_transient', 'pre_submit_timeout',
                                                               'blocked_before_submission', 'auth_required', 'credits_exhausted'}:
                        raise
                    continue
                except Exception as exc:
                    failure = {'category': 'validation_failed', 'error_class': type(exc).__name__}
                    self._save_attempt(diagnostics, attempt | {'failure': failure})
                    self._write_diagnostics(diagnostics, {'failure': failure})
                    raise FlowCommandError(stage, **failure) from exc
            if os.getenv('FLOW_API_FALLBACK_ENABLED', 'false').lower() != 'true' or self._generation_seconds() < api_timeout:
                raise FlowCommandError(stage, 'generation_window_exhausted' if self._generation_seconds() < api_timeout else 'attempts_exhausted',
                                       detail='Fresh generation budget exhausted; existing emergency rescue remains available.')
            self._write_diagnostics(diagnostics, {'api_fallback_state': 'started',
                                                  'fallback_reason': error.category if error else 'generation_window'})
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
        self._write_diagnostics(diagnostics, {'output_sha256': digest,
            'failure': None, 'provider': provider, **({'api_fallback_state': 'complete'} if provider == 'google_api' else {})})
        return output

    def _recover_pending(self, stage: str, diagnostics: Path, attempt: dict, output: Path) -> dict | None:
        saved = json.loads(diagnostics.read_text())
        if 'recovery_deadline' not in attempt:
            attempt['recovery_deadline'] = time.time() + min(300, max(0, self._generation_seconds()))
            self._save_attempt(diagnostics, attempt)
        deadline = min(attempt['recovery_deadline'], time.time() + max(0, self._generation_seconds()))
        self._recovery_deadline = deadline
        self._notify_transition(diagnostics, f'FLOW_{stage.upper()}_RECOVERY_{attempt["number"]}',
                                f'Flow {stage}: checking the existing result for up to five minutes before replacement.')
        while time.time() < deadline:
            result = saved.get('result') or (self._image_receipt(diagnostics, attempt) if stage == 'image' else None) or self._catalog_result(stage, attempt)
            if result:
                try:
                    item = result['images'][0] if stage == 'image' else result
                    local = item.get('local_path')
                    if local:
                        try:
                            path = self._owned_path(local, output.parent)
                            self._validate_original(stage, path, saved.get('original_sha256'))
                            return result
                        except FlowCommandError as exc:
                            if self._stops_replacement(exc):
                                raise
                        except (OSError, RuntimeError):
                            pass
                    if stage == 'image':
                        media_id = result['images'][0]['media_name']
                        path = self._recover_image(media_id, output.parent)
                        result['images'][0]['local_path'] = str(path)
                    else:
                        result['local_path'] = str(self._recover_video(result['media_id'], output.parent))
                    path = self._owned_path(item.get('local_path') if stage == 'image' else result['local_path'], output.parent)
                    self._validate_original(stage, path, saved.get('original_sha256'))
                    return result
                except FlowCommandError as exc:
                    if self._stops_replacement(exc):
                        raise
                except (OSError, ValueError, KeyError, IndexError):
                    pass
            time.sleep(min(15, max(0, deadline - time.time())))
        return None

    def _image_receipt(self, diagnostics: Path, attempt: dict) -> dict | None:
        receipt = diagnostics.with_name(f'flow_image_attempt_{attempt["number"]}.result.json')
        if not receipt.exists():
            return None
        try:
            result = json.loads(receipt.read_text())
            saved = json.loads(diagnostics.read_text())
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

    def _catalog_result(self, stage: str, attempt: dict) -> dict | None:
        try:
            from gflow_cli.config import get_settings
            db = get_settings().resolved_db_path()
            operation = (attempt.get('failure') or {}).get('operation_id')
            media_id = (attempt.get('failure') or {}).get('media_id')
            if not operation and not media_id:
                return None
            with sqlite3.connect(f'{db.as_uri()}?mode=ro', uri=True) as connection:
                rows = connection.execute(
                    'SELECT a.flow_media_id, a.model, a.aspect_ratio FROM operations o '
                    'JOIN operation_assets oa ON oa.operation_id=o.id AND oa.role="output" '
                    'JOIN assets a ON a.id=oa.asset_id WHERE o.profile_name=? AND o.flow_project_id=? '
                    'AND o.command=? AND a.profile_name=o.profile_name AND a.flow_project_id=o.flow_project_id '
                    'AND a.kind=? AND (? IS NULL OR o.id=?) AND (? IS NULL OR a.flow_media_id=?)',
                    (self.profile, os.getenv('FLOW_PREFLIGHT_PROJECT_ID', ''),
                     'image t2i' if stage == 'image' else 'video i2v', stage, operation, operation,
                     media_id, media_id)).fetchall()
            if len(rows) != 1:
                return None
            media, model, aspect = rows[0]
            if stage == 'image' and model in {'NARWHAL', 'BELUGA'} and aspect in {'3:4', 'IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR'}:
                return {'count': 1, 'model': 'NARWHAL', 'images': [{'media_name': media}],
                        'project_id': os.getenv('FLOW_PREFLIGHT_PROJECT_ID', '')}
            if stage == 'video' and model == 'veo_3_1_fast' and aspect in {'9:16', 'portrait'}:
                return {'succeeded': True, 'media_id': media,
                        'request': {'count': 1, 'model': model, 'mode': 'i2v', 'aspect': 'portrait'}}
        except (ImportError, OSError, sqlite3.Error, KeyError):
            pass
        return None

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
        if marker is not None:
            env["GFLOW_SHADOW_SUBMIT_MARKER"] = str(marker)
            if (marker.parent / 'flow_inputs.json').is_file():
                env['GFLOW_SHADOW_REQUEST_HASH'] = json.loads(marker.with_name(f'flow_{command[1]}_diagnostics.json').read_text())['request_hash']
                timeout = min(timeout, max(.01, FlowMediaClient._generation_seconds()))
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False, env=env)
        except subprocess.TimeoutExpired:
            submitted = bool(marker and marker.exists())
            raise FlowCommandError(command[1], "submission_uncertain" if submitted else "pre_submit_timeout",
                                   submission_state="uncertain" if submitted else "not_attempted") from None
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if result.returncode or payload.get("status") != "ok":
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            error_class = re.sub(r"[^A-Za-z0-9_]", "", str(error.get("class", "")))[:80]
            media_id = str(payload.get("media_id") or "")
            failed = payload.get("generation_status") == "MEDIA_GENERATION_STATUS_FAILED" and bool(media_id)
            reasons = (" ".join(map(str, payload.get("failure_reasons") or [])) + " " + str(error.get("class", "")) + " " + str(error.get("detail", ""))).upper()
            policy_or_quota = (any(word in reasons for word in ("SAFETY", "POLICY", "REJECT", "REFUSAL", "QUOTA", "CREDIT", "RESTRICT")) or
                               any('RECAPTCHA' in str(reason).upper() for reason in payload.get('failure_reasons') or []))
            clicked = bool(marker and marker.exists())
            submission_state = "attempted" if clicked else "not_attempted"
            if clicked:
                try:
                    submission_state = json.loads(marker.read_text()).get("state", "attempted")
                except (ValueError, OSError):
                    pass
            detail = str(error.get("detail") or "")
            tags = [tag for tag in ('SAFETY', 'POLICY', 'RESTRICT', 'REJECT', 'REFUSAL', 'RECAPTCHA', 'QUOTA', 'CREDIT') if tag in reasons]
            if tags:
                detail += ' [provider reason: ' + ', '.join(tags) + ']'
            for value in command[2:]:
                if len(value) > 10 and not value.startswith("--"):
                    detail = detail.replace(value, "[redacted]")
            detail = re.sub(r"https?://\S+|Bearer\s+\S+|(?:SAPISID|SID|Cookie)\s*[:=]\s*\S+|sk-or-v1-[\w-]+|AIza[\w-]+", "[redacted]", detail, flags=re.I)[:500]
            for key, value in env.items():
                if any(word in key.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD")) and len(value) >= 8:
                    detail = detail.replace(value, "[redacted]")
            operation_id = str(payload.get("operation_id") or "")
            auth_error = error_class in {
                "AuthExpiredError", "AisandboxAuthError", "AuthMissingError",
                "AuthLoginTimeoutError", "IdentityRecheckPendingError", "FlowAccountChooserError",
            }
            if (error_class == 'InsufficientCreditsError' or 'QUOTA' in reasons or 'CREDIT' in reasons) and not any(w in reasons for w in ('SAFETY', 'POLICY', 'RESTRICT', 'REJECT', 'REFUSAL', 'RECAPTCHA')):
                category = 'credits_exhausted'
            elif policy_or_quota:
                category = "provider_rejected"
            elif failed:
                category = "generation_failed"
            elif auth_error:
                category = "auth_required"
            elif submission_state == "blocked_before_submission":
                category = "blocked_before_submission"
            elif detail.startswith("submitted_request_uncertain:"):
                category = "submission_uncertain"
            elif clicked:
                category = "post_submit_error"
            elif bool(error.get("retryable")):
                category = "pre_submit_transient"
            else:
                category = "pre_submit_terminal"
            raise FlowCommandError(command[1], category, error_class=error_class,
                                   media_id=media_id, retryable=failed and not policy_or_quota and "RECAPTCHA" not in reasons,
                                   detail=detail, operation_id=operation_id, submission_state=submission_state)
        return payload

    @staticmethod
    def _owned_path(raw: str, directory: Path) -> Path:
        path = Path(raw).resolve(strict=True)
        if not path.is_relative_to(directory.resolve(strict=True)) or not path.is_file():
            raise RuntimeError("gflow returned an output outside the run directory")
        return path

    @staticmethod
    def _write_diagnostics(path: Path, data: dict) -> None:
        previous = json.loads(path.read_text()) if path.exists() else {}
        staged = path.with_name(path.name + ".tmp")
        with os.fdopen(os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
            json.dump(previous | data, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        staged.replace(path)

    def _catalog_position(self) -> int:
        try:
            from gflow_cli.config import get_settings
            db = get_settings().resolved_db_path()
            if not db.is_file():
                return 0
            with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True) as connection:
                return int(connection.execute("SELECT COALESCE(MAX(rowid), 0) FROM operations").fetchone()[0])
        except (ImportError, OSError, sqlite3.Error):
            return 0

    def _catalog_video_since(self, position: int) -> dict:
        try:
            from gflow_cli.config import get_settings
            db = get_settings().resolved_db_path()
            with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True) as connection:
                row = connection.execute(
                    "SELECT o.status, o.error_type, o.flow_project_id, a.flow_media_id, a.status, a.model, a.aspect_ratio "
                    "FROM operations o LEFT JOIN operation_assets oa ON oa.operation_id=o.id AND oa.role='output' "
                    "LEFT JOIN assets a ON a.id=oa.asset_id "
                    "WHERE o.rowid>? AND o.profile_name=? AND o.command='video i2v' "
                    "ORDER BY o.rowid DESC LIMIT 1", (position, self.profile),
                ).fetchone()
            if row:
                return dict(zip(("operation_status", "error_type", "project_id", "media_id",
                                 "asset_status", "model", "aspect"), row))
        except (ImportError, OSError, sqlite3.Error):
            pass
        return {}

    def _generate_image(self, prompt_json: dict, output_path: Path, recovered: dict | None = None) -> Path:
        started_at = datetime.now(timezone.utc).isoformat()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        diagnostics = output_path.with_name("flow_image_diagnostics.json")
        saved = json.loads(diagnostics.read_text()) if diagnostics.exists() else {}
        daily = saved.get('daily_policy', False)
        attempts = saved.get("attempts", [])
        previous = list(output_path.parent.glob("flow_image_attempt_*.submit"))
        marker = max(previous, key=lambda p: int(p.stem.rsplit("_", 1)[-1])) if previous else None
        payload = recovered
        first = 1 + max([int(p.stem.rsplit("_", 1)[-1]) for p in previous] +
                        [item["number"] for item in attempts], default=0)
        for number in range(first, first + 1 if daily else 3) if recovered is None else ():
            marker = output_path.with_name(f"flow_image_attempt_{number}.submit")
            try:
                raw_name = f'flow_art_attempt_{number}.png' if daily else 'flow_art.png'
                payload = self._run_json(self.image_command(prompt_json, output_path.with_name(raw_name)), 600, marker)
                self._write_diagnostics(diagnostics, {"result": {
                    "count": payload.get("count"), "model": payload.get("model"), "project_id": payload.get("project_id"),
                    "images": [{k: image.get(k) for k in ("local_path", "media_name", "model_name_type")} for image in payload.get("images", [])]}})
                attempts.append({"number": number, "submit_attempted": marker.exists(), "status": "complete"})
                self._write_diagnostics(output_path.with_name("flow_image_diagnostics.json"),
                                        {"provider": "flow", "started_at": started_at, "attempts": attempts})
                break
            except FlowCommandError as exc:
                attempts.append({"number": number, "submit_attempted": marker.exists(), "status": exc.category,
                                 "ended_at": datetime.now(timezone.utc).isoformat(),
                                 "error_class": exc.error_class, "media_id": exc.media_id,
                                 "detail": exc.detail, "operation_id": exc.operation_id,
                                 "expected_model": "NARWHAL", "submission_state": exc.submission_state})
                self._write_diagnostics(output_path.with_name("flow_image_diagnostics.json"),
                                        {"provider": "flow", "started_at": started_at, "attempts": attempts})
                if exc.category == "post_submit_error" and exc.media_id:
                    payload = {"count": 1, "model": "NARWHAL", "project_id": os.getenv("FLOW_PREFLIGHT_PROJECT_ID", ""),
                               "images": [{"local_path": None, "media_name": exc.media_id, "model_name_type": None}]}
                    self._write_diagnostics(diagnostics, {"result": payload})
                    if daily:
                        raise
                    break
                if daily or number == 2 or exc.category not in {"pre_submit_transient", "pre_submit_timeout", "blocked_before_submission"}:
                    raise
                if marker.exists() and exc.submission_state != "blocked_before_submission":
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
            if not item.get("media_name"):
                raise
            if daily and not recovered:
                raise FlowCommandError('image', 'post_submit_error', media_id=item['media_name'], submission_state='forwarded')
            source = self._recover_image(item["media_name"], output_path.parent)
        source = self._owned_path(str(source), output_path.parent)
        original = source.name
        width, height = self._validate_original('image', source, saved.get('original_sha256'))
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        self._write_diagnostics(diagnostics, {"original_sha256": source_hash})
        upscaled = False
        if not recovered and os.getenv("FLOW_IMAGE_UPSCALE_2K", "false").lower() == "true" and (not daily or self._generation_seconds() >= 300):
            media_id, project_id = item.get("media_name"), payload.get("project_id")
            if media_id and project_id:
                upscale_dir = output_path.parent / "flow_upscale"
                upscale_dir.mkdir(exist_ok=True)
                command = [
                    "gflow", "image", "upscale", media_id, "--scale", "2k",
                    "--project", project_id, "--out", str(upscale_dir), "--profile", self.profile,
                ]
                try:
                    result = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
                    matches = list(upscale_dir.rglob(f"{media_id}_2k.*")) if result.returncode == 0 else []
                    if len(matches) == 1:
                        candidate = self._owned_path(str(matches[0]), output_path.parent)
                        self._validate_original('image', candidate)
                        source = candidate
                        upscaled = True
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
            "ended_at": datetime.now(timezone.utc).isoformat(), "retry_count": len(attempts) - 1,
            "model": "Nano Banana 2.1", "selected_model": submission.get("selected_model"), "wire_model": submission.get("actual_model"), "response_model": wire_model, "model_attribution_confirmed": beluga_confirmed,
            "aspect": "3:4", "source_width": width,
            "source_height": height, "flow_2k_upscaled": upscaled,
            "media_id": item.get("media_name"), "project_id": payload.get("project_id"),
            "original_file": original, "source_file": str(source.relative_to(output_path.parent.resolve())),
            "attempts": attempts,
        })
        return output_path

    @staticmethod
    def _probe_video(path: Path) -> tuple[int, int, float, bool]:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,width,height:format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, check=True,
        )
        payload = json.loads(result.stdout)
        stream = next(item for item in payload["streams"] if item["codec_type"] == "video")
        return (int(stream["width"]), int(stream["height"]), float(payload["format"]["duration"]),
                any(item["codec_type"] == "audio" for item in payload["streams"]))

    def _recover_image(self, media_id: str, output_dir: Path) -> Path:
        try:
            from gflow_cli.config import get_settings
            db = get_settings().resolved_db_path()
            with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True) as connection:
                rows = connection.execute(
                    "SELECT l.path, l.sha256 FROM local_files l JOIN assets a ON a.id=l.asset_id "
                    "WHERE a.profile_name=? AND a.flow_media_id=? AND a.kind='image'",
                    (self.profile, media_id)).fetchall()
            for raw, digest in rows:
                if not raw or not digest:
                    continue
                try:
                    path = self._owned_path(raw, output_dir)
                    if hashlib.sha256(path.read_bytes()).hexdigest() == digest:
                        self._validate_original('image', path)
                        return path
                except (OSError, RuntimeError):
                    continue
        except (ImportError, OSError, sqlite3.Error):
            pass
        diagnostics = output_dir / 'flow_image_diagnostics.json'
        if diagnostics.exists() and json.loads(diagnostics.read_text()).get('daily_policy'):
            recovery = output_dir / 'flow_recovered'
            recovery.mkdir(exist_ok=True)
            if re.fullmatch(r'[A-Za-z0-9_-]+', media_id):
                target = recovery / f'{media_id}.png'
                script = Path(__file__).resolve().parents[2] / 'ops/flow_image_recover.py'
                remaining = getattr(self, '_recovery_deadline', time.time() + 120) - time.time()
                if remaining > 0:
                    try:
                        result = subprocess.run([sys.executable, str(script),
                            os.getenv('FLOW_PREFLIGHT_PROJECT_ID', ''), media_id, str(target)],
                            capture_output=True, text=True, timeout=min(120, remaining), check=False)
                        candidates = list(recovery.glob(f'{media_id}.*')) if result.returncode == 0 else []
                        if len(candidates) == 1:
                            return self._owned_path(str(candidates[0]), output_dir)
                    except subprocess.SubprocessError:
                        pass
        # Pinned gflow data download supports videos only; never regenerate to recover an image.
        raise FlowCommandError("image", "image_recovery_unavailable", media_id=media_id)

    def _recover_video(self, media_id: str, output_dir: Path) -> Path:
        recovery_dir = output_dir / "flow_recovered"
        recovery_dir.mkdir(exist_ok=True)
        for _ in (1, 2):
            try:
                result = subprocess.run(["gflow", "data", "download", media_id, "--out", str(recovery_dir),
                                         "--profile", self.profile, "--json"], capture_output=True, text=True,
                                        timeout=max(.01, min(300, getattr(self, '_recovery_deadline', time.time() + 300) - time.time())), check=False)
                if result.returncode:
                    continue
                payload = json.loads(result.stdout)
                return self._owned_path(payload["path"], output_dir)
            except (KeyError, OSError, ValueError, subprocess.TimeoutExpired):
                continue
        raise FlowCommandError("video", "existing_media_download_failed", media_id=media_id)

    def _generate_video(self, prompt_text: str, image_path: Path, output_path: Path, recovered: dict | None = None) -> Path:
        started_at = datetime.now(timezone.utc).isoformat()
        if not prompt_text.strip():
            raise ValueError("Video prompt is empty")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        start_frame = output_path.with_name("flow_start_frame.png")
        framed, _ = GoogleVideoClient._to_letterboxed_png_bytes(image_path)
        start_frame.write_bytes(framed)
        raw_path = output_path.with_name("flow_raw_video.mp4")
        diagnostics = output_path.with_name("flow_video_diagnostics.json")
        saved = json.loads(diagnostics.read_text()) if diagnostics.exists() else {}
        daily = saved.get('daily_policy', False)
        attempts = saved.get("attempts", [])
        previous = list(output_path.parent.glob("flow_video_attempt_*.submit"))
        submitted = len(previous)
        first = 1 + max([int(p.stem.rsplit("_", 1)[-1]) for p in previous] +
                        [item["number"] for item in attempts], default=0)
        pre_submit_retries = sum(item.get("status") in {"auth_required", "pre_submit_transient", "pre_submit_timeout"}
                                 and not item.get("submit_attempted") for item in attempts)
        payload = recovered or {}
        source = None
        if recovered:
            try:
                source = self._owned_path(payload.get("local_path") or "", output_path.parent)
            except (FileNotFoundError, RuntimeError):
                source = self._recover_video(payload["media_id"], output_path.parent)
        for number in range(first, first + 1 if daily else 4) if recovered is None else ():
            marker = output_path.with_name(f"flow_video_attempt_{number}.submit")
            attempt_raw = output_path.with_name(f'flow_video_attempt_{number}.mp4') if daily else raw_path
            before = self._catalog_position()
            try:
                payload = self._run_json(self.video_command(prompt_text, start_frame, attempt_raw), 1500, marker)
                self._write_diagnostics(diagnostics, {"result": {k: payload.get(k) for k in ("succeeded", "media_id", "local_path") } | {
                    "request": {k: (payload.get("request") or {}).get(k) for k in ("count", "model", "mode", "aspect")}}})
                submitted = len(list(output_path.parent.glob("flow_video_attempt_*.submit")))
                attempts.append({"number": number, "status": "complete", "submit_attempted": marker.exists(),
                                 "media_id": payload.get("media_id"),
                                 "ended_at": datetime.now(timezone.utc).isoformat()})
                self._write_diagnostics(output_path.with_name("flow_video_diagnostics.json"),
                                        {"provider": "flow", "started_at": started_at,
                                         "attempts": attempts, "submissions_estimated": submitted})
                if payload.get("local_path"):
                    try:
                        source = self._owned_path(payload["local_path"], output_path.parent)
                    except FileNotFoundError:
                        source = None
                if source is None and payload.get("media_id"):
                    if daily:
                        raise FlowCommandError('video', 'post_submit_error', media_id=payload['media_id'], submission_state='attempted')
                    source = self._recover_video(str(payload["media_id"]), output_path.parent)
                    attempts[-1]["status"] = "recovered_existing_media"
                break
            except FlowCommandError as exc:
                submitted = len(list(output_path.parent.glob("flow_video_attempt_*.submit")))
                catalog = self._catalog_video_since(before) if marker.exists() and not daily else {}
                media_id = exc.media_id or catalog.get("media_id") or ""
                attempts.append({"number": number, "status": exc.category, "submit_attempted": marker.exists(),
                                 "media_id": media_id, "error_class": exc.error_class,
                                 "detail": exc.detail, "operation_id": exc.operation_id,
                                 "submission_state": exc.submission_state, "retryable": exc.retryable,
                                 "ended_at": datetime.now(timezone.utc).isoformat()})
                self._write_diagnostics(output_path.with_name("flow_video_diagnostics.json"),
                                        {"provider": "flow", "started_at": started_at,
                                         "attempts": attempts, "submissions_estimated": submitted})
                if daily:
                    raise
                if (exc.category in {"pre_submit_transient", "pre_submit_timeout"} and not marker.exists()
                        and pre_submit_retries < 1 and number < 3):
                    pre_submit_retries += 1
                    continue
                if exc.category == "generation_failed" and exc.retryable and submitted < 2 and number < 3:
                    time.sleep(60)
                    continue
                if exc.category == "post_submit_error" and media_id:
                    payload = {"succeeded": True, "media_id": media_id,
                               "request": {"count": 1, "model": "veo_3_1_fast", "mode": "i2v", "aspect": "portrait"}}
                    self._write_diagnostics(diagnostics, {"result": payload})
                    source = self._recover_video(media_id, output_path.parent)
                    attempts[-1]["status"] = "recovered_existing_media"
                    break
                raise
        if source is None:
            raise FlowCommandError("video", "submission_uncertain")
        request = payload.get("request") or {}
        if (not payload.get("succeeded") or request.get("count") != 1 or
                request.get("model") != "veo_3_1_fast" or request.get("mode") != "i2v" or
                request.get("aspect") != "portrait"):
            raise FlowCommandError('video', 'provider_rejected', detail='Flow video MODEL or SETTINGS differ')
        width, height, duration, has_audio = self._validate_original('video', source, saved.get('original_sha256'))
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        self._write_diagnostics(diagnostics, {"original_sha256": source_hash})
        if source.resolve() != raw_path.resolve():
            shutil.copyfile(source, raw_path)
        selected = source
        upscale_status = "not_needed" if width == 1080 else "disabled"
        if not recovered and width == 720 and os.getenv("FLOW_VIDEO_UPSCALE_1080P", "true").lower() == "true" and (not daily or self._generation_seconds() >= 240):
            project_id = os.getenv("FLOW_PREFLIGHT_PROJECT_ID", "").strip()
            media_id = str(payload.get("media_id") or "")
            upscale_status = "unavailable"
            if project_id and media_id:
                candidate = output_path.with_name("flow_1080p_download.mp4")
                script = Path(__file__).resolve().parents[2] / "ops" / "flow_video_upscale.py"
                try:
                    result = subprocess.run([sys.executable, str(script), project_id, media_id, str(candidate)],
                                            capture_output=True, text=True, timeout=240, check=False)
                    upscale_status = json.loads(result.stdout).get("status", "unavailable")
                    if result.returncode == 0 and candidate.is_file():
                        up_width, up_height, up_duration, up_audio = self._probe_video(candidate)
                        if ((up_width, up_height) == (1080, 1920) and abs(up_duration - duration) <= 0.5
                                and up_audio == has_audio):
                            selected = candidate
                            upscale_status = "validated"
                        else:
                            upscale_status = "invalid_media"
                except (OSError, ValueError, subprocess.SubprocessError, KeyError, IndexError, StopIteration):
                    upscale_status = "invalid_media" if candidate.exists() else "unavailable"
        staged = output_path.with_name(".generated_video.staged.mp4")
        if selected == source and (width, height) == (720, 1280):
            subprocess.run(
                ["ffmpeg", "-nostdin", "-y", "-i", str(source), "-vf",
                 "scale=1080:1920:flags=lanczos", "-c:v", "libx264", "-preset", "medium",
                 "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags",
                 "+faststart", str(staged)],
                capture_output=True, timeout=600, check=True,
            )
        else:
            shutil.copyfile(selected, staged)
        staged.replace(output_path)
        self._write_diagnostics(output_path.with_name("flow_video_diagnostics.json"), {
            "provider": "flow", "started_at": started_at,
            "ended_at": datetime.now(timezone.utc).isoformat(), "retry_count": len(attempts) - 1,
            "model": "veo-fast", "aspect": "9:16", "media_id": payload.get("media_id"),
            "source_width": width, "source_height": height, "duration_seconds": duration,
            "source_has_audio": has_audio, "locally_scaled_to_1080p": selected == source and width == 720,
            "upscale_status": upscale_status, "selected_video_file": selected.name,
            "start_frame": start_frame.name, "attempts": attempts, "submissions_estimated": submitted,
        })
        return output_path

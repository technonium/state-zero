"""One reserved Omni video creation; all subsequent recovery is read-only."""
import base64
import hashlib
import io
import json
import math
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse

import requests
from PIL import Image

from google_key_router import GoogleAPIError, GoogleKeyRouter
from state_zero_models import GOOGLE_VIDEO_MODEL
from utils import get_project_root, probe_video, run_bounded, video_1080_command
from dotenv import load_dotenv

load_dotenv(dotenv_path=get_project_root() / '.env')


class GoogleVideoClient:
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self):
        self.router = GoogleKeyRouter(os.getenv('GOOGLE_API_KEY_PRIMARY', ''),
                                      os.getenv('GOOGLE_API_KEY_FALLBACK', ''))
        self.router.retry_ambiguous_calls = False
        self.default_model = os.getenv('GOOGLE_VIDEO_MODEL', GOOGLE_VIDEO_MODEL)
        self.timeout_seconds = int(os.getenv('GOOGLE_VIDEO_TIMEOUT_SECONDS', '900'))
        self.poll_seconds = int(os.getenv('GOOGLE_VIDEO_POLL_SECONDS', '10'))

    @staticmethod
    def _to_letterboxed_png_bytes(image_path: Path, target_width: int = 1080, target_height: int = 1920) -> tuple[bytes, tuple[int, int]]:
        with Image.open(image_path) as source:
            source = source.convert("RGB")
            src_width, src_height = source.size

            scale = min(target_width / src_width, target_height / src_height)
            resized_width = max(1, int(round(src_width * scale)))
            resized_height = max(1, int(round(src_height * scale)))

            resized = source.resize((resized_width, resized_height), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (target_width, target_height), (0, 0, 0))
            offset_x = (target_width - resized_width) // 2
            offset_y = (target_height - resized_height) // 2
            canvas.paste(resized, (offset_x, offset_y))

            out = io.BytesIO()
            canvas.save(out, format="PNG")
            return out.getvalue(), (src_width, src_height)

    @staticmethod
    def _diagnostics_path(output_path: Path) -> Path:
        return output_path.with_name('google_video_diagnostics.json')

    def _read_diagnostics(self, output_path: Path) -> dict:
        path = self._diagnostics_path(output_path)
        if not path.exists():
            return {}
        try:
            saved = json.loads(path.read_text())
            if not isinstance(saved, dict) or type(saved.get('record_version')) is not int or saved['record_version'] != 2:
                raise ValueError('Unrecognized record')
            return saved
        except (ValueError, OSError) as exc:
            raise GoogleAPIError(None, 'API video record is unreadable; reconcile before continuing') from exc

    def _write_diagnostics(self, output_path: Path, update: dict):
        path = self._diagnostics_path(output_path)
        saved = self._read_diagnostics(output_path)
        saved.update(update)
        saved.update(record_version=2, updated_at=datetime.now(timezone.utc).isoformat())
        path.parent.mkdir(parents=True, exist_ok=True)
        staged = path.with_suffix('.json.tmp')
        with os.fdopen(os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as handle:
            json.dump(saved, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        staged.replace(path)

    def _request_timeout(self, maximum: float) -> float:
        deadline = getattr(self, 'generation_deadline', None)
        remaining = maximum if deadline is None else min(maximum, deadline - time.time())
        if remaining <= 0:
            raise GoogleAPIError(504, 'Video deadline exhausted; existing reservation is preserved')
        return remaining

    def generate_from_image(self, prompt_text: str, image_path: Path, output_path: Path) -> Path:
        saved = self._read_diagnostics(output_path)
        if saved.get('submission_started') or saved.get('interaction_id') or saved.get('operation_name'):
            raise GoogleAPIError(None, 'API video already dispatched; resume its saved interaction only')
        if self.default_model.startswith('veo-'):
            raise GoogleAPIError(400, 'Retired Veo API model configured; set GOOGLE_VIDEO_MODEL to Omni')
        frame, source_size = self._to_letterboxed_png_bytes(image_path)
        payload = {
            'model': self.default_model, 'background': True, 'store': True,
            'input': [{'type': 'image', 'mime_type': 'image/png', 'data': base64.b64encode(frame).decode()},
                      {'type': 'text', 'text': prompt_text}],
            'generation_config': {'video_config': {'task': 'image_to_video'}},
            'response_format': {'type': 'video', 'aspect_ratio': '9:16', 'resolution': '720p',
                                'duration': '8s', 'delivery': 'inline'},
        }
        self.generation_deadline = min(getattr(self, 'generation_deadline', float('inf')),
                                       time.time() + self.timeout_seconds)
        label, key = self.router.keys[0]
        self._request_timeout(120)
        # Dispatch intent precedes HTTP: a lost response can never authorize another POST.
        self._write_diagnostics(output_path, {
            'protocol': 'interactions', 'model': self.default_model, 'key_label': label,
            'key_sha256': hashlib.sha256(key.encode()).hexdigest(),
            'frame_sha256': hashlib.sha256(frame).hexdigest(),
            'prompt_sha256': hashlib.sha256(prompt_text.encode()).hexdigest(),
            'source_size': list(source_size), 'generation_deadline': self.generation_deadline,
            'submission_started': True, 'status': 'starting',
        })
        self.router.submission_started = True
        response = requests.post(f'{self.BASE_URL}/interactions',
            headers={'x-goog-api-key': key}, json=payload,
            timeout=self._request_timeout(120), allow_redirects=False)
        try:
            if response.status_code not in (200, 201, 202):
                self._write_diagnostics(output_path, {'start_http_status': response.status_code})
                raise GoogleAPIError(response.status_code, 'Omni video creation rejected; no paid retry')
            data = response.json()
            identity = data.get('id') if isinstance(data, dict) else None
            if not isinstance(identity, str) or not identity.strip():
                raise GoogleAPIError(None, 'Creation returned no interaction ID; reservation remains consumed')
            self._write_diagnostics(output_path, {'interaction_id': identity, 'status': 'accepted', 'start_http_status': response.status_code})
        finally:
            response.close()
        return self._finish_interaction(data, key, output_path)

    def resume_operation(self, output_path: Path) -> Path:
        saved = self._read_diagnostics(output_path)
        if saved.get('protocol') != 'interactions' or not isinstance(saved.get('interaction_id'), str) or not saved['interaction_id'].strip():
            raise GoogleAPIError(None, 'No verified Omni interaction ID; no new creation is safe')
        key = next((key for label, key in self.router.keys if label == saved.get('key_label')), None)
        if not key or hashlib.sha256(key.encode()).hexdigest() != saved.get('key_sha256'):
            raise GoogleAPIError(None, 'Original interaction key is unavailable')
        deadline = saved.get('generation_deadline')
        if isinstance(deadline, bool) or not isinstance(deadline, (float, int)) or not math.isfinite(deadline):
            raise GoogleAPIError(None, 'Original interaction deadline is unavailable')
        self.generation_deadline = min(deadline, getattr(self, 'generation_deadline', float('inf')))
        self.router.submission_started = True
        original = output_path.with_suffix('.original.mp4')
        if saved.get('original_sha256') and original.is_file():
            if hashlib.sha256(original.read_bytes()).hexdigest() != saved['original_sha256']:
                raise GoogleAPIError(None, 'Saved original video bytes changed')
            return self._process_original(original, output_path)
        data = self._get_interaction(saved['interaction_id'], key)
        return self._finish_interaction(data, key, output_path)

    def _get_interaction(self, identity: str, key: str) -> dict:
        response = requests.get(f'{self.BASE_URL}/interactions/{quote(identity, safe="")}',
            headers={'x-goog-api-key': key}, timeout=self._request_timeout(60), allow_redirects=False)
        try:
            if response.status_code != 200:
                raise GoogleAPIError(response.status_code, 'Interaction retrieval failed; resume by ID only')
            return response.json()
        finally:
            response.close()

    def _finish_interaction(self, data: dict, key: str, output_path: Path) -> Path:
        identity = self._read_diagnostics(output_path)['interaction_id']
        while True:
            if not isinstance(data, dict) or data.get('id') != identity:
                raise GoogleAPIError(None, 'Interaction response identity differs')
            status = data.get('status')
            if status not in {'in_progress', 'completed'}:
                raise GoogleAPIError(None, 'Interaction did not complete; no paid retry')
            self._write_diagnostics(output_path, {'status': status})
            if status == 'completed':
                break
            time.sleep(self._request_timeout(self.poll_seconds))
            data = self._get_interaction(identity, key)
        videos = [part for step in data.get('steps', []) if step.get('type') == 'model_output'
                  for part in step.get('content', []) if part.get('type') == 'video']
        if len(videos) != 1 or videos[0].get('mime_type') != 'video/mp4':
            raise GoogleAPIError(None, 'Interaction has no single MP4 result')
        original = output_path.with_suffix('.original.mp4')
        part = videos[0]
        if part.get('data'):
            original.write_bytes(base64.b64decode(part['data'], validate=True))
        elif part.get('uri'):
            self._download_video(self._active_file_uri(part['uri'], key), key, original)
        else:
            raise GoogleAPIError(None, 'Interaction has no video bytes or delivery URI')
        self._write_diagnostics(output_path, {'original_sha256': hashlib.sha256(original.read_bytes()).hexdigest()})
        return self._process_original(original, output_path)

    def _active_file_uri(self, uri: str, key: str) -> str:
        parsed = urlparse(uri)
        # A Files resource may still be processing when its video part is returned.
        if parsed.hostname == 'generativelanguage.googleapis.com' and parsed.path.startswith('/v1beta/files/'):
            if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443)
                    or not re.fullmatch(r'/v1beta/files/[A-Za-z0-9_-]+(?::download)?', parsed.path)):
                raise GoogleAPIError(400, 'Invalid video file resource')
            name = parsed.path[len('/v1beta/'):].split(':', 1)[0]
            while True:
                response = requests.get(f'{self.BASE_URL}/{name}', headers={'x-goog-api-key': key},
                    timeout=self._request_timeout(60), allow_redirects=False)
                try:
                    if response.status_code != 200:
                        raise GoogleAPIError(response.status_code, 'Video file status unavailable')
                    data = response.json()
                finally:
                    response.close()
                if data.get('state') == 'ACTIVE':
                    return data.get('downloadUri') or f'{self.BASE_URL}/{name}:download?alt=media'
                if data.get('state') != 'PROCESSING':
                    raise GoogleAPIError(None, 'Video file failed processing')
                time.sleep(self._request_timeout(self.poll_seconds))
        return uri

    def _download_video(self, uri: str, api_key: str, output_path: Path):
        for hop in range(6):
            destination = urlparse(uri)
            google = (destination.hostname or '').endswith('.googleapis.com')
            if (destination.scheme != 'https' or not destination.hostname or destination.username or destination.password
                    or destination.port not in (None, 443) or not google and api_key in unquote(uri)):
                raise GoogleAPIError(400, 'Rejected video download destination')
            headers = {'x-goog-api-key': api_key} if google else {}
            response = requests.get(uri, headers=headers, timeout=self._request_timeout(180), allow_redirects=False)
            try:
                if response.status_code in (301, 302, 303, 307, 308):
                    if hop == 5 or not response.headers.get('Location'):
                        raise GoogleAPIError(400, 'Video download redirect limit or missing destination')
                    uri = urljoin(uri, response.headers['Location'])
                    continue
                if response.status_code != 200:
                    raise GoogleAPIError(response.status_code, 'Video download failed')
                output_path.write_bytes(response.content)
                return
            finally:
                response.close()

    def _process_original(self, original: Path, output_path: Path) -> Path:
        width, height, duration, audio = probe_video(original)
        if (width, height) != (720, 1280) or not 7.5 <= duration <= 8.5 or not audio:
            raise GoogleAPIError(None, 'Omni original must be portrait 720p, 7.5–8.5 seconds, with audio')
        staged = output_path.with_suffix('.scaled.mp4')
        result = run_bounded(video_1080_command(original, staged), timeout=min(600,
            max(.001, float(os.getenv('STATE_ZERO_LOCAL_ONLY_DEADLINE', str(time.time()+600))) - time.time())))
        result.check_returncode()
        final = probe_video(staged)
        if final[:2] != (1080, 1920) or not 7.5 <= final[2] <= 8.5 or not final[3]:
            raise GoogleAPIError(None, 'Scaled video validation failed; original is preserved')
        staged.replace(output_path)
        return output_path

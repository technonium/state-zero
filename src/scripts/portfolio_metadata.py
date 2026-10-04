"""Public portfolio feed: validated fields and atomic destination writes."""
import json
import os
import re
import shlex
import subprocess
import tempfile
import uuid
from datetime import date as calendar_date
from pathlib import Path
from urllib.parse import urlsplit


def build_metadata(*, date, title, post_result, base_url):
    if calendar_date.fromisoformat(date).isoformat() != date:
        raise ValueError('Invalid portfolio date')
    if not isinstance(title, str) or not title.strip():
        raise ValueError('Portfolio title unavailable')
    if not isinstance(post_result, dict) or not post_result.get('post_id') or post_result.get('mock'):
        raise ValueError('Confirmed Instagram publication unavailable')
    permalink = post_result.get('permalink') or ''
    link = urlsplit(permalink)
    if link.scheme != 'https' or link.hostname not in {'instagram.com', 'www.instagram.com'} or not re.fullmatch(r'/(?:p|reel|tv)/[A-Za-z0-9_-]+/?', link.path) or link.port is not None or link.username or link.password or link.query or link.fragment:
        raise ValueError('Valid Instagram permalink unavailable')
    base_url = (base_url or '').strip().rstrip('/')
    base = urlsplit(base_url)
    if base.scheme not in {'http', 'https'} or not base.hostname or 'mock' in base.hostname or base.username or base.password or base.query or base.fragment:
        raise ValueError('Valid public media base URL unavailable')
    return dict(schemaVersion=1, date=date, title=title.strip(), instagramUrl=permalink,
                lightVideoUrl=f'{base_url}/portfolio/{date}/light.mp4',
                darkVideoUrl=f'{base_url}/portfolio/{date}/dark.mp4')


def publish_metadata(payload, root, *, target=None, ssh_opts=()):
    """Commit the dated record before the latest pointer; never truncate in place."""
    content = (json.dumps(payload, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    for suffix in (payload['date'], 'latest'):
        directory = root / 'portfolio' / suffix
        destination = directory / 'metadata.json'
        if target is None:
            directory.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix='.metadata-', suffix='.json', dir=directory)
            try:
                with os.fdopen(fd, 'wb') as f:
                    f.write(content); f.flush(); os.fsync(f.fileno())
                os.chmod(temporary, 0o644)
                os.replace(temporary, destination)
            finally:
                if os.path.exists(temporary): os.unlink(temporary)
        else:
            temporary = directory / f'.metadata-{uuid.uuid4().hex}.json'
            def run(command):
                result = subprocess.run(command, capture_output=True, text=True)
                if result.returncode: raise RuntimeError('Portfolio metadata transfer failed')
            with tempfile.TemporaryDirectory() as staging:
                local = Path(staging) / 'metadata.json'
                local.write_bytes(content)
                run(['ssh', *ssh_opts, target, f'mkdir -p {shlex.quote(str(directory))}'])
                try:
                    run(['scp', *ssh_opts, str(local), f'{target}:{shlex.quote(str(temporary))}'])
                    run(['ssh', *ssh_opts, target, f'chmod 644 {shlex.quote(str(temporary))} && mv -f {shlex.quote(str(temporary))} {shlex.quote(str(destination))}'])
                except Exception:
                    subprocess.run(['ssh', *ssh_opts, target, f'rm -f {shlex.quote(str(temporary))}'], capture_output=True, text=True)
                    raise

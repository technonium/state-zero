"""Public portfolio feed: validated fields and atomic destination writes."""
import json
import fcntl
import hashlib
import os
import re
import shlex
import subprocess
import tempfile
import sys
from datetime import date as calendar_date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


def media_revision(directory):
    digest = hashlib.sha256()
    for theme in ('light', 'dark'):
        path = Path(directory) / f'{theme}.mp4'
        if not path.is_file() or not path.stat().st_size:
            raise ValueError('Both portfolio videos are required')
        digest.update(theme.encode())
        digest.update(path.stat().st_size.to_bytes(8, 'big'))
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
    return digest.hexdigest()


def build_metadata(*, date, title, post_result, base_url, revision=None):
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
    if revision is not None and not re.fullmatch(r'[a-f0-9]{64}', revision):
        raise ValueError('Invalid portfolio revision')
    directory = f'{base_url}/portfolio/{date}' + (f'/{revision}' if revision else '')
    return dict(schemaVersion=1, date=date, title=title.strip(), instagramUrl=permalink,
                lightVideoUrl=f'{directory}/light.mp4', darkVideoUrl=f'{directory}/dark.mp4')


def _atomic_json(destination, value, mode=0o644):
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.metadata-', dir=destination.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def _utc(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None: raise ValueError('Generation start must include its timezone')
    return parsed.astimezone(timezone.utc).isoformat(timespec='microseconds')


def publish_metadata(payload, root, *, generation_started_at=None, target=None, ssh_opts=()):
    """Serialize ordering and atomic publication on the destination filesystem."""
    # Legacy callers are dated at the start of their day; the pipeline always
    # supplies its persisted generation-attempt start, never completion time.
    started = _utc(generation_started_at or f"{payload['date']}T00:00:00Z")
    if target is not None:
        # Run the same stdlib-only publisher on the destination, in one SSH
        # process, so the lock also covers every compare and replacement.
        script = Path(__file__).read_text(encoding='utf-8')
        command = f'python3 -c {shlex.quote(script)} {shlex.quote(str(root))}'
        result = subprocess.run(['ssh', *ssh_opts, target, command], input=json.dumps({'payload': payload, 'started': started}), capture_output=True, text=True, timeout=120)
        if result.returncode: raise RuntimeError('Portfolio metadata publication failed')
        return
    root = Path(root)
    private = root / '.portfolio-publication'
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    revision = hashlib.sha256(json.dumps([payload['lightVideoUrl'], payload['darkVideoUrl']]).encode()).hexdigest()
    candidate = dict(date=payload['date'], started=started, revision=revision)
    key = (candidate['date'], candidate['started'])
    with (private / 'publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        accepted = []
        for suffix in (payload['date'], 'latest'):
            state_path = private / f'{suffix}.json'
            destination = root / 'portfolio' / suffix / 'metadata.json'
            previous = None
            if state_path.exists():
                previous = json.loads(state_path.read_text())
            elif destination.exists():
                legacy = json.loads(destination.read_text())
                previous = dict(date=legacy['date'], started=datetime.fromtimestamp(destination.stat().st_mtime, timezone.utc).isoformat(timespec='microseconds'), revision=None)
            if previous:
                prior_key = (previous['date'], previous['started'])
                if key < prior_key: continue
                if key == prior_key and previous.get('revision') not in (None, revision):
                    raise ValueError('Portfolio publication has conflicting revisions for the same generation start')
            accepted.append((state_path, destination))
        for state_path, destination in accepted:
            # Save the ordering guard first. If the public write fails, a retry
            # with the same key finishes it; an older worker stays excluded.
            _atomic_json(state_path, candidate, 0o600)
            _atomic_json(destination, payload)


if __name__ == '__main__':
    envelope = json.load(sys.stdin)
    publish_metadata(envelope['payload'], Path(sys.argv[1]), generation_started_at=envelope['started'])

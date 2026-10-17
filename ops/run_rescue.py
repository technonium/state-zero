#!/usr/bin/env python3
"""The existing 14:00 job waits briefly for ownership; never interrupts a publisher."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))


def acquire_execution_lock(lock, wait_seconds=600):
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(1, remaining))


def main():
    from flow_media_client import FlowMediaClient
    from notifier import safe_notify_status
    from utils import get_state_root, get_pipeline_run_date_str
    root = get_state_root()
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'pipeline-execution.lock').open('a') as lock:
        os.chmod(lock.name, 0o600)
        if not acquire_execution_lock(lock):
            day = get_pipeline_run_date_str()
            FlowMediaClient._write_diagnostics(root / f'rescue-lock-{day}.json',
                {'status': 'skipped_execution_busy', 'wait_seconds': 600})
            safe_notify_status(day, 'RESCUE_EXECUTION_BUSY',
                '14:00 rescue waited ten minutes for the active pipeline; no process was interrupted. Human review required.')
            return 75
        env = os.environ.copy()
        env['PIPELINE_TERMINAL_RESCUE_RUN'] = 'true'
        # The ordinary entrypoint performs posted/uncertain-publication checks.
        return subprocess.run(['xvfb-run', '-a', sys.executable, '-u',
            str(Path(__file__).resolve().parents[1] / 'src/scripts/pipeline.py')], env=env).returncode


if __name__ == '__main__':
    sys.exit(main())

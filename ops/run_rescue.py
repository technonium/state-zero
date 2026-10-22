#!/usr/bin/env python3
"""The existing 14:00 job waits briefly for ownership; never interrupts a publisher."""
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))


def main():
    if not os.getenv('DISPLAY'):
        return subprocess.run(['xvfb-run','-a',sys.executable,'-u',str(Path(__file__).resolve())]).returncode
    from flow_media_client import FlowMediaClient
    from notifier import safe_notify_status
    from utils import execution_lock, execution_lock_path, ExecutionBusy, get_pipeline_run_date_str
    try:
        with execution_lock(wait_seconds=600) as lock:
            env = os.environ.copy()
            env['PIPELINE_TERMINAL_RESCUE_RUN'] = 'true'
            env['STATE_ZERO_EXECUTION_FD'] = str(lock.fileno())
            env['STATE_ZERO_EXECUTION_ACQUIRED_AT'] = str(lock.acquired_at)
            return subprocess.run([sys.executable, '-u',
                str(Path(__file__).resolve().parents[1] / 'src/scripts/pipeline.py')],
                env=env, pass_fds=(lock.fileno(),)).returncode
    except ExecutionBusy:
        day = get_pipeline_run_date_str()
        FlowMediaClient._write_diagnostics(execution_lock_path().parent / f'rescue-lock-{day}.json',
            {'status': 'skipped_execution_busy', 'wait_seconds': 600})
        safe_notify_status(day, 'RESCUE_EXECUTION_BUSY',
            '14:00 rescue waited ten minutes for the active pipeline; human review required.')
        return 75



if __name__ == '__main__':
    sys.exit(main())

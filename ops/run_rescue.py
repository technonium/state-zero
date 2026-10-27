#!/usr/bin/env python3
"""The existing 14:00 job waits briefly for ownership; never interrupts a publisher."""
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))


def _run():
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
        diagnostic=execution_lock_path().parent / f'rescue-lock-{day}.json'
        message='14:00 rescue waited ten minutes for the active pipeline; human review required.'
        try:
            FlowMediaClient._write_diagnostics(diagnostic,{'status':'skipped_execution_busy','wait_seconds':600})
            FlowMediaClient()._notify_transition(diagnostic,'RESCUE_EXECUTION_BUSY',message)
        except (OSError,ValueError,TypeError):
            safe_notify_status(day,'RESCUE_EXECUTION_BUSY',message)
        return 75



def main():
    try:
        return _run()
    except Exception as exc:
        message = f'14:00 rescue failed unexpectedly ({type(exc).__name__}).'
        try:
            from utils import sanitize_error
            message += ' ' + sanitize_error(str(exc))[:800]
        except Exception:
            pass
        print(message, file=sys.stderr)
        try:
            from notifier import safe_notify_status
            from utils import get_pipeline_run_date_str
            safe_notify_status(get_pipeline_run_date_str(), 'RESCUE_FAILED', message)
        except Exception:
            print('Rescue failure alert could not be delivered.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

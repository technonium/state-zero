#!/usr/bin/env python3
"""One supervised, non-publishing Flow video attempt in private probe storage."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
from flow_media_client import FlowMediaClient
from utils import execution_lock, get_runtime_root, probe_video, run_bounded


class ProbeClient(FlowMediaClient):
    def __init__(self):
        super().__init__()
        self.deadline = time.time() + 1500

    def _generation_seconds(self):
        return self.deadline - time.time()

    def _notify_transition(self, diagnostics, status, message):
        print(status)  # Probe diagnostics only; never notify Telegram.

    def _generate_daily(self, stage, request, output, generate, image_path):
        assert stage == 'video'
        diagnostics = output.with_name('flow_video_diagnostics.json')
        self._write_diagnostics(diagnostics, {'pending_attempt': {'number': 1}})
        generate(None)
        receipt = self._video_receipt(diagnostics, {'number': 1})
        if not receipt:
            raise ValueError('Verified video receipt missing')
        return self._finish_daily(stage, output, diagnostics, 'flow')


def run_probe(probe_id, frame, prompt_file, *, now=None):
    now = now or datetime.now(ZoneInfo('Asia/Kolkata'))
    minute = now.hour * 60 + now.minute
    if 570 <= minute <= 870:
        raise ValueError('Probe refused during 09:30–14:30 IST')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', probe_id):
        raise ValueError('Invalid probe ID')
    if os.getenv('STATE_ZERO_EXECUTION_FD'):
        raise ValueError('Probe requires its own canonical lock')
    folder = get_runtime_root() / 'probe' / probe_id
    if folder.resolve() != folder.absolute() or folder.is_symlink():
        raise ValueError('Probe storage must not use symlinks')
    with execution_lock():
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Consumed even after an unsent failure; a second attempt needs a new approval/id.
        with os.fdopen(os.open(folder / 'attempt.claim', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w') as claim:
            claim.write('one video attempt\n')
        report = {'probe_id': probe_id, 'status': 'failed', 'card_render': 'skipped: source data/metadata unavailable'}
        try:
            # Local process only. No daily state, publishing, notifications or API fallback.
            os.environ.update(STATE_ZERO_PRIVATE_ROOT=str(folder), PIPELINE_POST_TO_INSTAGRAM='false',
                MEDIA_GENERATION_PROVIDER='flow', FLOW_API_FALLBACK_ENABLED='false',
                GOOGLE_API_FALLBACK_ENABLED='false', FLOW_VIDEO_UPSCALE_1080P='false',
                TELEGRAM_BOT_TOKEN='', TELEGRAM_CHAT_ID='')
            source = folder / 'input_art.png'
            shutil.copyfile(frame, source)
            prompt = prompt_file.read_text().strip()
            for name in FlowMediaClient._input_names(folder):
                target = folder / name
                if not target.exists():
                    target.write_text('{}' if target.suffix == '.json' else '')
            (folder / 'video_prompt.txt').write_text(prompt)
            for name in ('daily_data.json', 'card_metadata.json'):
                if (frame.parent / name).is_file():
                    shutil.copyfile(frame.parent / name, folder / name)
            FlowMediaClient.freeze_inputs(folder)
            output = ProbeClient().generate_video(prompt, source, folder / 'generated_video.mp4')
            width, height, duration, audio = probe_video(output)
            report.update(status='complete', width=width, height=height, duration=duration, audio=audio,
                          output=str(output), sha256=hashlib.sha256(output.read_bytes()).hexdigest())
            if all((frame.parent / name).is_file() for name in ('daily_data.json', 'card_metadata.json')):
                card = folder / 'card_final.mp4'
                result = run_bounded([sys.executable, str(Path(__file__).resolve().parents[1] / 'src/scripts/composite.py'),
                    '--type', 'video', '--video', str(output), '--data', str(folder / 'daily_data.json'),
                    '--meta', str(folder / 'card_metadata.json'), '--output', str(card)], timeout=600)
                result.check_returncode()
                report.update(card_render='complete', card=str(card))
        except Exception as exc:
            report['error_class'] = type(exc).__name__
        finally:
            try:
                marker = FlowMediaClient._read_diagnostics(folder / 'flow_video_attempt_1.submit')
            except (OSError, ValueError):
                marker = {'state': 'uncertain'}
            diagnostics = FlowMediaClient._read_diagnostics(folder / 'flow_video_diagnostics.json')
            receipt = FlowMediaClient._read_diagnostics(folder / 'flow_video_attempt_1.result.json')
            report.update(actual_key=marker.get('actual_model'), check_result=marker.get('state', 'not_sent'),
                ui_readback=marker.get('ui_readback'), receipt_status=('verified' if report['status'] == 'complete' else 'written') if receipt else 'absent',
                cut_checked=diagnostics.get('cut_checked', False), cut_at_seconds=diagnostics.get('cut_at_seconds'),
                source_width=diagnostics.get('source_width'), source_height=diagnostics.get('source_height'),
                source_duration=diagnostics.get('duration_seconds'), source_audio=diagnostics.get('source_has_audio'),
                original=(diagnostics.get('result') or {}).get('local_path'),
                files=sorted(str(p) for p in folder.rglob('*') if p.is_file()))
            FlowMediaClient._write_diagnostics(folder / 'report.json', report)
        return report


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe-id', required=True)
    parser.add_argument('--frame', type=Path, required=True)
    parser.add_argument('--prompt-file', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run_probe(args.probe_id, args.frame.resolve(), args.prompt_file.resolve())
    except Exception as exc:
        print('Probe refused: ' + type(exc).__name__)
        return 1
    print('Probe ' + result['status'] + '; inspect its private report.json')
    return 0 if result['status'] == 'complete' else 1


if __name__ == '__main__':
    sys.exit(main())

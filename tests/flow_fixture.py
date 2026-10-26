"""Frozen synthetic inputs for adapters exercised through the daily contract."""
import json
from pathlib import Path


def freeze(directory, *, image=None, video='move'):
    from flow_media_client import FlowMediaClient
    directory = Path(directory)
    if (directory / 'flow_inputs.json').exists():
        return
    directory.mkdir(parents=True, exist_ok=True)
    for name in FlowMediaClient._input_names(directory):
        path = directory / name
        if not path.exists():
            path.write_text('{}' if path.suffix == '.json' else 'original')
    (directory / 'image_prompt.json').write_text(json.dumps(image or {}))
    (directory / 'video_prompt.txt').write_text(video)
    FlowMediaClient.freeze_inputs(directory)


def video_receipt(directory, request_hash, media_id, *, attempt=1):
    """Existing child contract, independent of the new parent record version."""
    import hashlib
    from flow_readback import identity_hash
    from google_video_client import GoogleVideoClient
    directory = Path(directory)
    frame, _ = GoogleVideoClient._to_letterboxed_png_bytes(directory/'input_art.png')
    (directory/'flow_start_frame.png').write_bytes(frame)
    data = dict(version=1, request_hash=request_hash, profile='shadow', project_id='',
        baseline=[], seed=None, prompt_hash='b'*64, model='veo_3_1_i2v_s_fast_portrait',
        aspect='portrait', count=1, attempt=attempt,
        frozen_inputs_sha256=hashlib.sha256((directory/'flow_inputs.json').read_bytes()).hexdigest(),
        kind='video', source_hash=hashlib.sha256(frame).hexdigest(), frame_id='33333333-3333-4333-8333-333333333333',
        duration=8, wire_verified=True)
    (directory/f'flow_video_attempt_{attempt}.context.json').write_text(json.dumps(data))
    (directory/f'flow_video_attempt_{attempt}.submit').write_text(json.dumps(dict(state='forwarded',
        actual_model=data['model'], readback_hash=identity_hash(data))))
    (directory/f'flow_video_attempt_{attempt}.result.json').write_text(json.dumps(dict(
        request_hash=request_hash, profile='shadow', project_id='', media_id=media_id,
        workflow_id='44444444-4444-4444-8444-444444444444', succeeded=True,
        request=dict(count=1,model='veo_3_1_fast',mode='i2v',aspect='portrait'))))


class FrozenFlowCalls:
    def setUp(self):
        from datetime import datetime
        from unittest.mock import patch
        from flow_media_client import FlowMediaClient
        for clock in (patch.object(FlowMediaClient, '_generation_seconds', return_value=7200),
                      patch('utils.get_pipeline_now', return_value=datetime(2026, 10, 25, 10)),
                      patch('notifier.Notifier.notify_status', return_value=False)):
            clock.start()
            self.addCleanup(clock.stop)

    def frozen_image(self, client, prompt, output):
        freeze(Path(output).parent, image=prompt)
        return client.generate_image(prompt, output)

    def frozen_video(self, client, prompt, image, output):
        freeze(Path(output).parent, video=prompt)
        return client.generate_video(prompt, image, output)

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

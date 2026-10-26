"""Credit-free boundaries that replace the deleted alternate Flow paths."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/scripts'))
from flow_media_client import FlowCommandError, FlowMediaClient


class ReleaseThreeSimplificationTests(unittest.TestCase):
    def test_unfrozen_flow_entry_points_fail_before_any_provider_work(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            root = Path(directory)
            art = root / 'art.png'
            art.write_bytes(b'unchanged input')
            with patch.object(FlowMediaClient, '_generate_image') as image, \
                    patch.object(FlowMediaClient, '_generate_video') as video, \
                    patch('flow_media_client.GoogleImageClient') as api:
                for call in (lambda: FlowMediaClient().generate_image({}, root / 'output.png'),
                             lambda: FlowMediaClient().generate_video('original', art, root / 'output.mp4')):
                    with self.assertRaises(FlowCommandError) as caught:
                        call()
                    self.assertEqual(caught.exception.category, 'request_identity_unverified')
                image.assert_not_called()
                video.assert_not_called()
                api.assert_not_called()
            self.assertEqual(art.read_bytes(), b'unchanged input')

    def test_cli_error_never_reads_catalog_for_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'flow_image_attempt_1.submit'
            marker.write_text('{"state":"forwarded"}')
            result = subprocess.CompletedProcess([], 1, json.dumps({
                'status': 'error', 'error': {'class': 'NetworkError'}}), '')
            with patch('flow_media_client.run_bounded', return_value=result), \
                    patch('gflow_cli.config.get_settings', side_effect=AssertionError('catalog must not be read')):
                with self.assertRaises(FlowCommandError) as caught:
                    FlowMediaClient._run_json(['gflow', 'image'], 10, marker)
            self.assertEqual(caught.exception.category, 'post_submit_error')
            self.assertEqual(caught.exception.operation_id, '')

    def test_contextless_remote_recovery_stops_without_browser_or_new_allowance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            diagnostic = root / 'flow_image_diagnostics.json'
            original = {'record_version':2, 'request_hash': 'a'*64, 'result': {
                'count': 1, 'model': 'NARWHAL', 'images': [{'media_name': 'exact'}]}}
            diagnostic.write_text(json.dumps(original))
            attempt = {'number': 1, 'recovery_deadline': 1100}
            with patch.object(FlowMediaClient, '_generation_seconds', return_value=7200), \
                    patch('flow_media_client.time.time', return_value=1000), \
                    patch('flow_media_client.time.sleep', side_effect=AssertionError('legacy polling loop')), \
                    patch.object(FlowMediaClient, '_notify_transition'), \
                    patch.object(FlowMediaClient, '_recovery_command') as browser:
                self.assertIsNone(FlowMediaClient()._recover_pending(
                    'image', diagnostic, attempt, root / 'generated_art.png'))
                browser.assert_not_called()
            self.assertEqual(json.loads(diagnostic.read_text()), original)
            self.assertEqual(attempt, {'number': 1, 'recovery_deadline': 1100})

    def test_dockerfiles_disable_the_apt_delayed_retry_loop(self):
        for name in ('Dockerfile', 'Dockerfile.flow-shadow'):
            text = (ROOT / name).read_text()
            self.assertGreater(text.count('apt-get '), 0)
            self.assertEqual(text.count('apt-get '), text.count('apt-get -o Acquire::Retries=0 '), name)

    def test_lifecycle_records_routing_state_at_the_event(self):
        from flow_readback import note_video_lifecycle
        with patch('flow_readback.note_upload_response'):
            data = {}
            note_video_lifecycle(data, 'request_failed', request=Mock(failure='net::ERR_ABORTED'), upload=True)
            self.assertFalse(data['observer']['events'][-1]['routing_active'])
            note_video_lifecycle(data, 'routing_active', upload=True)
            note_video_lifecycle(data, 'request_failed', request=Mock(failure='net::ERR_ABORTED'), upload=True)
            self.assertTrue(data['observer']['events'][-1]['routing_active'])

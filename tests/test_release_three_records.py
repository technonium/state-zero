"""Completed-date record cutover and spend-boundary replays; no live provider calls."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
from flow_media_client import FlowCommandError, FlowMediaClient
from flow_fixture import freeze


class ReleaseThreeRecordTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.output = self.root / 'generated_art.png'
        self.record = self.root / 'flow_image_diagnostics.json'
        for guard in (patch.dict(os.environ, {'FLOW_API_FALLBACK_ENABLED': 'true'}, clear=True),
                      patch.object(FlowMediaClient, '_generation_seconds', return_value=7200),
                      patch('utils.get_pipeline_now', return_value=datetime(2026, 10, 27, 10)),
                      patch.object(FlowMediaClient, '_notify_transition')):
            guard.start(); self.addCleanup(guard.stop)
        freeze(self.root)

    def image(self, command, timeout, marker):
        raw = Path(command[command.index('--output') + 1])
        Image.new('RGB', (768, 1024), 'white').save(raw)
        marker.write_text(json.dumps({'state': 'forwarded', 'expected_model': 'NARWHAL',
            'actual_model': 'BELUGA', 'selected_model': 'Nano Banana 2.1'}))
        return {'count': 1, 'model': 'NARWHAL', 'images': [{'media_name': 'exact',
            'local_path': str(raw), 'model_name_type': 'BELUGA'}]}

    def assert_new_record(self, saved):
        self.assertEqual(saved['record_version'], 2)
        self.assertFalse({'generation_started', 'output_sha256', 'retry_count',
            'submissions_estimated', 'api_fallback_state'} & saved.keys())
        for attempt in saved.get('attempts', []) + ([saved['pending_attempt']] if saved.get('pending_attempt') else []):
            self.assertFalse({'status', 'submit_attempted', 'recovery_exhausted', 'submission'} & attempt.keys())
            self.assertNotIn('submission_state', attempt.get('failure') or {})
        self.assertNotIn('submission_state', saved.get('failure') or {})
        self.assertNotIn('submission_state', saved.get('processing_failure') or {})
        self.assertNotIn('succeeded', saved.get('result') or {})

    def test_new_date_has_one_processed_checksum_and_no_old_phase_copies(self):
        with patch.object(FlowMediaClient, '_run_json', side_effect=self.image) as cli:
            FlowMediaClient().generate_image({}, self.output)
        self.assert_new_record(json.loads(self.record.read_text()))
        marker = self.root / 'flow_image_attempt_1.submit'; original = marker.read_bytes()
        with patch.object(FlowMediaClient, '_run_json') as cli, patch('flow_media_client.GoogleImageClient') as api:
            FlowMediaClient().generate_image({}, self.output)
        cli.assert_not_called(); api.assert_not_called()
        self.assertEqual(marker.read_bytes(), original)

    def test_legacy_active_record_requires_stop_reconcile_without_rewrite_or_spend(self):
        Image.new('RGB', (768, 1024)).save(self.output)
        self.record.write_text(json.dumps({'request_hash': 'old', 'daily_policy': True,
            'generation_started': True, 'selected_sha256': hashlib.sha256(self.output.read_bytes()).hexdigest()}))
        before = self.record.read_bytes()
        with patch.object(FlowMediaClient, '_run_json') as cli, patch('flow_media_client.GoogleImageClient') as api:
            with self.assertRaises(FlowCommandError) as error:
                FlowMediaClient().generate_image({}, self.output)
        self.assertEqual(error.exception.category, 'request_identity_unverified')
        self.assertIn('cutover', error.exception.detail)
        self.assertEqual(self.record.read_bytes(), before)
        cli.assert_not_called(); api.assert_not_called()

    def test_writer_does_not_upgrade_an_existing_legacy_record(self):
        self.record.write_text('{"generation_started":true}')
        before = self.record.read_bytes()
        with self.assertRaises(FlowCommandError):
            FlowMediaClient._write_diagnostics(self.record, {'request_hash': 'new'})
        self.assertEqual(self.record.read_bytes(), before)

    def test_missing_child_marker_never_proves_no_send_or_authorizes_retry(self):
        with patch.object(FlowMediaClient, '_run_json', side_effect=FlowCommandError(
                'image', 'pre_submit_timeout', submission_state='not_attempted')) as cli, \
                patch('flow_media_client.GoogleImageClient') as api:
            with self.assertRaises(FlowCommandError) as error:
                FlowMediaClient().generate_image({}, self.output)
        self.assertEqual(error.exception.category, 'request_identity_unverified')
        cli.assert_called_once(); api.assert_not_called()
        saved = json.loads(self.record.read_text()); self.assert_new_record(saved)
        self.assertEqual(len(saved['attempts']), 1)

    def test_child_forwarding_wins_over_a_pre_submit_exception_hint(self):
        def lost(command, timeout, marker):
            self.image(command, timeout, marker)
            raise FlowCommandError('image', 'pre_submit_timeout', submission_state='not_attempted')
        with patch.object(FlowMediaClient, '_run_json', side_effect=lost) as cli, \
                patch.object(FlowMediaClient, '_recover_pending', return_value=None) as recovery, \
                patch('flow_media_client.GoogleImageClient') as api:
            with self.assertRaises(FlowCommandError):
                FlowMediaClient().generate_image({}, self.output)
        cli.assert_called_once(); recovery.assert_called_once(); api.assert_not_called()
        self.assert_new_record(json.loads(self.record.read_text()))

    def test_api_promotion_crash_keeps_reservation_and_selected_bytes(self):
        def auth(command, timeout, marker):
            self.image(command, timeout, marker)
            raise FlowCommandError('image', 'generation_failed', retryable=True, submission_state='forwarded')
        def api_image(request, path):
            Image.new('RGB', (768, 1024), 'blue').save(path)
        replace = Path.replace
        def crash(path, destination):
            if path.name == '.generated_art.api.png': raise OSError('promotion crash')
            return replace(path, destination)
        with patch.object(FlowMediaClient, '_run_json', side_effect=auth), \
                patch('flow_media_client.GoogleImageClient') as api, patch.object(Path, 'replace', crash):
            api.return_value.generate_from_json.side_effect = api_image
            with self.assertRaises(OSError): FlowMediaClient().generate_image({}, self.output)
            self.assertEqual(api.return_value.generate_from_json.call_count, 1)
        saved = json.loads(self.record.read_text()); self.assert_new_record(saved)
        self.assertTrue(saved['api_reserved']); self.assertEqual(saved['provider'], 'google_api')
        with patch.object(FlowMediaClient, '_run_json') as cli, patch('flow_media_client.GoogleImageClient') as api:
            FlowMediaClient().generate_image({}, self.output)
        cli.assert_not_called(); api.assert_not_called()
        self.assertEqual(hashlib.sha256(self.output.read_bytes()).hexdigest(), saved['selected_sha256'])

    def test_video_restart_uses_child_receipt_without_parent_success_copy(self):
        from flow_fixture import video_receipt
        from shutil import copyfile
        art = self.root/'input_art.png'; Image.new('RGB', (768, 1024)).save(art)
        output = self.root/'generated_video.mp4'; diagnostic = self.root/'flow_video_diagnostics.json'
        media = '22222222-2222-4222-8222-222222222222'
        def generated(command, timeout, marker):
            request_hash = json.loads(diagnostic.read_text())['request_hash']
            video_receipt(self.root, request_hash, media)
            raw = Path(command[command.index('--output')+1]); raw.write_bytes(b'validated-video')
            return dict(succeeded=True, media_id=media, local_path=str(raw),
                request=dict(count=1,model='veo_3_1_fast',mode='i2v',aspect='portrait'))
        def crash(source, destination):
            if Path(destination).name == 'flow_raw_video.mp4': raise OSError('processing crash')
            return copyfile(source, destination)
        client = FlowMediaClient()
        with patch.object(client, '_probe_video', return_value=(1080,1920,8,True)), \
                patch.object(client, '_run_json', side_effect=generated) as cli:
            with patch('flow_media_client.shutil.copyfile', side_effect=crash), self.assertRaises(FlowCommandError):
                client.generate_video('move', art, output)
            self.assert_new_record(json.loads(diagnostic.read_text()))
            child = self.root/'flow_video_attempt_1.result.json'; before=child.read_bytes()
            child.unlink()
            saved=json.loads(diagnostic.read_text()); saved['result']['succeeded']=True
            diagnostic.write_text(json.dumps(saved))
            with patch('flow_media_client.GoogleVideoClient') as api, self.assertRaises(FlowCommandError):
                client.generate_video('move', art, output)
            api.assert_not_called(); cli.assert_called_once()
            child.write_bytes(before)
            client.generate_video('move', art, output)
        cli.assert_called_once()
        self.assertEqual(child.read_bytes(), before)
        self.assert_new_record(json.loads(diagnostic.read_text()))

    def test_legacy_and_unknown_versions_fail_closed_without_mutating_history(self):
        for raw in ('{}', '{"record_version":1}', '{"record_version":3}', '{"record_version":2.0}', 'broken'):
            with self.subTest(raw=raw):
                self.record.write_text(raw)
                with patch.object(FlowMediaClient, '_run_json') as cli, patch('flow_media_client.GoogleImageClient') as api:
                    with self.assertRaises(FlowCommandError): FlowMediaClient().generate_image({}, self.output)
                self.assertEqual(self.record.read_text(), raw)
                cli.assert_not_called(); api.assert_not_called()

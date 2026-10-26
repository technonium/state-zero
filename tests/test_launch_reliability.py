"""Regression checks for recovery ownership and private operational messages."""
import tests  # Explicit notification isolation before production imports.
import os
import hashlib
import json
import io
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
from flow_media_client import FlowCommandError, FlowMediaClient


class RecoveryOwnershipTests(unittest.TestCase):
    def setUp(self):
        clock=patch.object(FlowMediaClient,'_generation_seconds',return_value=7200)
        clock.start();self.addCleanup(clock.stop)

    def test_generation_timeout_reaps_its_child_processes(self):
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = Path(tmp) / 'child.pid'
            script = ("import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c',"
                      "'import time; time.sleep(30)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
                      "open(sys.argv[1],'w').write(str(p.pid)); time.sleep(30)")
            try:
                with self.assertRaises(FlowCommandError):
                    FlowMediaClient._run_json([sys.executable, '-c', script, str(pidfile)], .5)
                pid = int(pidfile.read_text())
                # A reparented zombie is dead; Linux init may not reap it immediately.
                for _ in range(50):
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    status = Path(f'/proc/{pid}/stat')
                    if status.exists() and status.read_text().split()[2] == 'Z':
                        break
                    time.sleep(.02)
                else:
                    self.fail('Generation timeout left an owned child running')
            finally:
                if pidfile.exists():
                    try:
                        os.kill(int(pidfile.read_text()), signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_transient_worker_failure_uses_original_remaining_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); context = root / 'flow_video_attempt_1.context.json'
            from PIL import Image
            from flow_fixture import freeze, video_receipt
            freeze(root)
            Image.new('RGB', (768,1024)).save(root/'input_art.png')
            video_receipt(root, 'fingerprint', '22222222-2222-4222-8222-222222222222')
            (root/'flow_video_diagnostics.json').write_text(json.dumps({'record_version':2,
                'request_hash':'fingerprint', 'attempts':[{'number':1}]}))
            now = [100.0]
            expected = json.loads((root/'flow_video_attempt_1.result.json').read_text())
            candidate={'kind':'video','context_sha256':hashlib.sha256(context.read_bytes()).hexdigest(),'result':expected}
            original=root/'original.mp4';original.write_bytes(b'synthetic original')
            retrieved=expected|{'local_path':str(original),'sha256':hashlib.sha256(original.read_bytes()).hexdigest()}
            client = FlowMediaClient()
            with patch('flow_media_client.time.time', side_effect=lambda: now[0]), \
                    patch('flow_media_client.time.sleep', side_effect=lambda s: now.__setitem__(0, now[0] + s)), \
                    patch.object(client,'_probe_video',return_value=(720,1280,8,True)), \
                    patch.object(client, '_recovery_command', side_effect=[
                        (3, {'error_class': 'TargetClosedError'}), (0, {'candidate':candidate}), (0, {'result': retrieved})]) as worker:
                result = client._readback_media(context, 'fingerprint', root / 'video.mp4', 400, stage='video')
            self.assertEqual(result, retrieved)
            self.assertEqual(worker.call_count, 3)
            self.assertTrue(all(float(call.args[0][6]) == 400 for call in worker.call_args_list[:2]))
            self.assertLess(now[0], 400)

    def test_unsupported_and_identity_failures_do_not_restart_browser(self):
        for status in ('recovery_unsupported', 'identity_unverified'):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); client = FlowMediaClient()
                with patch.object(client, '_recovery_command', return_value=(3, {'status': status})) as worker:
                    with self.assertRaises(FlowCommandError):
                        client._readback_media(root / 'context', 'hash', root / 'video.mp4', time.time()+300)
                worker.assert_called_once()

    def test_recovery_restrictions_cannot_enable_another_provider(self):
        for error in ('ContentPolicyError', 'RecaptchaError', 'AccountRestrictedError'):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); client = FlowMediaClient()
                with patch.object(client, '_recovery_command', return_value=(3, {'error_class': error})) as worker:
                    with self.assertRaises(FlowCommandError) as caught:
                        client._readback_media(root / 'context', 'hash', root / 'video.mp4', time.time()+300)
                    self.assertTrue(client._stops_replacement(caught.exception))
                worker.assert_called_once()

    def test_repeated_browser_failures_cannot_extend_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); client = FlowMediaClient(); now = [100.0]
            with patch('flow_media_client.time.time', side_effect=lambda: now[0]), \
                    patch('flow_media_client.time.sleep', side_effect=lambda s: now.__setitem__(0, now[0]+s)), \
                    patch.object(client, '_recovery_command', return_value=(3, {'error_class':'TargetClosedError'})) as worker:
                self.assertIsNone(client._readback_media(root/'context', 'hash', root/'video.mp4', 106))
            self.assertLessEqual(now[0], 106)
            self.assertEqual(worker.call_count, 2)


class OperationalPrivacyTests(unittest.TestCase):
    def test_manual_polling_and_pipeline_error_logs_are_sanitized(self):
        from contextlib import redirect_stdout
        from pipeline import WHOOPPipeline, PipelineFatalError
        import requests
        private = 'synthetic-pipeline-token'
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN':private}):
            pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
            pipeline.bot_token = private
            captured = io.StringIO()
            with patch('pipeline.requests.get', side_effect=requests.ConnectionError(
                    f'https://api.telegram.org/bot{private}/getUpdates')), redirect_stdout(captured):
                self.assertEqual(pipeline._get_latest_update_id(), 0)
            self.assertNotIn(private, captured.getvalue())
            pipeline.post_to_instagram=False;pipeline.output_dir=Path(tmp);pipeline.run_date='2030-01-01'
            with patch('pipeline.get_notifier',return_value=Mock()), redirect_stdout(captured):
                with self.assertRaises(PipelineFatalError) as caught:
                    pipeline.log_error('fixture', f'Failure https://example.test/?access_token={private}')
                pipeline._report_fatal_error(caught.exception)
            self.assertNotIn(private, captured.getvalue())
            self.assertNotIn(private,(Path(tmp)/'pipeline_error.log').read_text())

    def test_telegram_errors_and_fallback_logs_do_not_expose_credentials(self):
        import notifier
        secret = 'synthetic-sensitive-value'
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': secret, 'TELEGRAM_CHAT_ID': 'fixture'}, clear=True):
            old = notifier.Notifier._instance
            notifier.Notifier._instance = None
            self.addCleanup(setattr, notifier.Notifier, '_instance', old)
            instance = notifier.Notifier()
            with patch.object(instance, '_send_message', return_value=True) as send:
                instance.notify_error('2030-01-01', 'poll', 'ConnectionError',
                    f'Failed https://api.example.test/status?access_token={secret}',
                    details_tail='Authorization: Bearer unknown-secret Cookie: session=private')
            message = send.call_args.args[0]
            for value in (secret, 'unknown-secret', 'session=private'):
                self.assertNotIn(value, message)

    def test_sanitizer_preserves_public_permalink_and_error_identity(self):
        from utils import sanitize_error
        public = 'https://www.instagram.com/reel/example/'
        self.assertEqual(sanitize_error('TargetClosedError ' + public), 'TargetClosedError ' + public)
        self.assertNotIn('signed-value', sanitize_error('https://media.example.test/file?token=signed-value'))
        self.assertNotIn('private-cookie', sanitize_error("{'Cookie': 'SID=private-cookie'}"))
        self.assertNotIn('private-auth', sanitize_error('Authorization: SAPISIDHASH private-auth'))

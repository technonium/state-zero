"""Test runners must not load notification credentials or reach Telegram."""
import tests  # Explicit notification isolation before production imports.
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
import notifier


class NotificationIsolationTests(unittest.TestCase):
    def test_explicit_flag_and_production_stack_imports(self):
        code = """
import os, sys, unittest
import requests
import utils
loads = []
sends = []
utils.load_project_dotenv = lambda: loads.append(True)
class Reply:
    def json(self): return {'ok': True}
def fake_post(*args, **kwargs):
    sends.append(True)
    return Reply()
requests.post = fake_post
mode = sys.argv[1]
if mode == 'test': os.environ['STATE_ZERO_TEST_MODE'] = '1'
if mode == 'production-stack':
    import pipeline, gflow_cli
    import playwright.sync_api
sys.modules['pytest'] = object()
import notifier
instance = notifier.Notifier()
assert instance._is_enabled()
if mode == 'late-test': os.environ['STATE_ZERO_TEST_MODE'] = '1'
if mode == 'test': os.environ.clear()
try:
    instance._telegram_request('sendMessage')
except notifier.NotificationError:
    assert mode in ('test', 'late-test')
else:
    assert mode in ('normal', 'production-stack')
assert len(sends) == (1 if mode in ('normal', 'production-stack') else 0)
assert not loads if mode == 'test' else loads
"""
        env = {'PATH': os.environ.get('PATH', ''),
            'PYTHONPATH': str(Path(notifier.__file__).parent),
            'TELEGRAM_BOT_TOKEN': 'synthetic-bot', 'TELEGRAM_CHAT_ID': 'synthetic-chat'}
        for mode in ('normal', 'production-stack', 'test', 'late-test'):
            with self.subTest(mode=mode):
                result = subprocess.run([sys.executable, '-B', '-c', code, mode], env=env,
                    capture_output=True, text=True, timeout=45)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_import_does_not_load_project_dotenv_under_test_runner(self):
        handlers = list(notifier.LOCAL_LOG.handlers)
        self.addCleanup(setattr, notifier.LOCAL_LOG, 'handlers', handlers)
        with patch('utils.load_project_dotenv', side_effect=AssertionError('real dotenv load')):
            runpy.run_path(notifier.__file__)

    def test_real_transport_is_blocked_even_with_credentials(self):
        with patch.object(notifier.Notifier, '_instance', None), patch.dict(os.environ, {
                'TELEGRAM_BOT_TOKEN': 'synthetic-bot', 'TELEGRAM_CHAT_ID': 'synthetic-chat'}):
            instance = notifier.Notifier()
            with patch('notifier.requests.post', side_effect=AssertionError('real send attempted')) as post, \
                    patch('notifier.requests.get', side_effect=AssertionError('real send attempted')) as get:
                for use_get in (False, True):
                    with self.subTest(use_get=use_get), self.assertRaisesRegex(
                            notifier.NotificationError, 'test process'):
                        instance._telegram_request('sendMessage', use_get=use_get)
                post.assert_not_called(); get.assert_not_called()

    def test_status_and_document_cannot_escape_to_http(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(notifier.Notifier, '_instance', None), \
                patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'synthetic-bot',
                    'TELEGRAM_CHAT_ID': 'synthetic-chat'}):
            document = Path(tmp)/'fixture.mp4'; document.write_bytes(b'fixture')
            instance = notifier.Notifier()
            with patch('notifier.requests.post', side_effect=AssertionError('real send attempted')) as post, \
                    patch('notifier.time.sleep'):
                self.assertFalse(instance.notify_status('2026-10-23', 'FLOW_IMAGE_API_COMPLETE', 'fixture'))
                self.assertFalse(instance._send_document(document))
                post.assert_not_called()

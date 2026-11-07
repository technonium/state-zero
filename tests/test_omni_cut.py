"""Real FFmpeg cut detection and an offline manual replacement through card render."""
import tests
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from PIL import Image
from flow_fixture import freeze
from google_video_client import GoogleVideoClient, OmniCutError, first_video_cut
from pipeline import WHOOPPipeline
from utils import probe_video


class OmniCutTests(unittest.TestCase):
    def fixture(self, path, *, cut=False):
        inputs = ['-f', 'lavfi', '-i', 'color=black:s=720x1280:r=4:d=4']
        if cut:
            inputs += ['-f', 'lavfi', '-i', 'color=white:s=720x1280:r=4:d=4']
        inputs += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
        mapping = (['-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]', '-map', '2:a']
                   if cut else [])
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', *inputs, *mapping, '-shortest',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', str(path)], check=True)

    def test_cut_original_is_rejected_before_scale_and_kept_for_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / '.generated_video.api.original.mp4'
            output = root / '.generated_video.api.mp4'
            self.fixture(original, cut=True)
            self.assertAlmostEqual(first_video_cut(original), 4, places=2)
            client = object.__new__(GoogleVideoClient)
            with patch('google_video_client.video_1080_command') as scale, \
                    patch('google_video_client.requests.post') as create:
                with self.assertRaisesRegex(OmniCutError, r'Omni clip had a cut at 4\.00 s; send a video in manual mode\.'):
                    client._process_original(original, output)
            scale.assert_not_called()
            create.assert_not_called()
            self.assertTrue(original.is_file())
            self.assertFalse(output.exists())
            self.assertEqual(client._read_diagnostics(output)['cut_at_seconds'], 4)

    def test_manual_cut_replacement_finishes_card_in_dry_run(self):
        now = datetime(2026, 11, 8, 13, 10, tzinfo=ZoneInfo('Asia/Kolkata'))
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'STATE_ZERO_PRIVATE_ROOT': directory, 'PIPELINE_DATE': '2026-11-08',
                'PIPELINE_POST_TO_INSTAGRAM': 'false', 'PIPELINE_MODE': 'automatic',
                'PIPELINE_MEDIA_MODE': 'local_test', 'MEDIA_GENERATION_PROVIDER': 'flow',
                'TELEGRAM_BOT_TOKEN': 'fixture-token', 'TELEGRAM_CHAT_ID': '100',
                'PIPELINE_MANUAL_MATCH_STRICT': 'true',
                'PIPELINE_EFFECTIVE_DEADLINE_ISO': now.replace(hour=14, minute=0).isoformat()}, clear=True), \
                patch('pipeline.WHOOPPipeline._now', return_value=now), \
                patch('utils.get_pipeline_deadline', return_value=(now.replace(hour=14, minute=0), 'test')), \
                patch('time.time', return_value=now.timestamp()):
            p = WHOOPPipeline()
            root = p.output_dir
            freeze(root, video='original')
            art = root / 'generated_art.png'
            Image.new('RGB', (1080, 1440), 'navy').save(art)
            art_bytes = art.read_bytes()
            video = root / 'manual-fixture.mp4'
            self.fixture(video)
            self.assertIsNone(first_video_cut(video))
            p.daily_run = Mock()
            p.daily_run.notify_once.side_effect = lambda transition, send, **kw: send()
            for method in ('_claim_daily_run_or_exit', '_ensure_owner_runtime_dirs', '_set_heartbeat_context',
                           '_stop_heartbeat_thread', '_cleanup_non_authoritative_daily_state', 'step_1_validate',
                           '_revalidate_whoop_checkpoint', '_run_portfolio_media_secondary'):
                setattr(p, method, Mock())
            p.step_7_generate_image = Mock(return_value=art)
            p._build_caption_or_raise = Mock(return_value='fixture')
            p.finalize_posted_run = Mock(return_value=True)
            p._load_required_text_outputs = Mock(return_value=('blend', 'creature', 'environment'))
            p.step_12_upload_vps = Mock()
            p.step_14_post_instagram = Mock()
            p._get_latest_update_id = Mock(return_value=1)
            p._send_telegram_message = Mock(return_value=1001)
            p._fetch_telegram_updates = Mock(return_value=[
                {'message': {'chat': {'id': 100}, 'message_id': 10, 'video': {'file_id': 'unrelated'}}},
                {'message': {'chat': {'id': 100}, 'message_id': 11, 'reply_to_message': {'message_id': 1001},
                             'video': {'file_id': 'replacement'}}}])
            p._download_telegram_file = Mock(side_effect=lambda _, target: shutil.copyfile(video, target))
            with patch('pipeline._setup_global_exception_handler'), \
                    patch('flow_media_client.FlowMediaClient.generate_video', side_effect=OmniCutError(1.45833)), \
                    patch('pipeline.get_notifier') as notifier:
                p._run_owned()
            p._send_telegram_message.assert_called_once_with(
                'Omni clip had a cut at 1.46 s; send a video in manual mode.')
            self.assertEqual(p._download_telegram_file.call_args.args[0], 'replacement')
            self.assertEqual(p.asset_source, 'manual_telegram')
            self.assertEqual(art.read_bytes(), art_bytes)
            self.assertEqual((root / 'generated_video.mp4').read_bytes(), video.read_bytes())
            self.assertEqual(json.loads(p.session_file.read_text())['status'], 'COMPLETED')
            with Image.open(root / 'card_final.png') as card:
                self.assertEqual(card.size, (1080, 1920))
            self.assertEqual(probe_video(root / 'card_final.mp4')[:2], (1080, 1920))
            p.step_12_upload_vps.assert_not_called()
            p.step_14_post_instagram.assert_not_called()
            notifier.return_value.notify_dry_run_complete.assert_called_once()

"""Fallback delivery uses the real mounted publisher with a simulated public HTTP boundary."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from PIL import Image
from requests import Response

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
from daily_run_state import DailyRunStateManager
from emergency_fallback_manager import EmergencyFallbackManager, PORTFOLIO_MEDIA_FILENAMES
from pipeline import WHOOPPipeline
from portfolio_metadata import build_metadata, media_revision, publish_metadata


class FallbackPortfolioDeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.private = self.root / 'private'
        self.public = self.root / 'public'
        self.addCleanup(os.umask, os.umask(0o077))
        self.env = patch.dict(os.environ, {
            'STATE_ZERO_PRIVATE_ROOT': str(self.private), 'EMERGENCY_FALLBACK_ENABLED': 'true',
            'VPS_PUBLIC_BASE_URL': 'https://media.example.test', 'PIPELINE_MEDIA_MODE': 'live_vps',
            'VPS_SSH_HOST': 'media.example.test', 'VPS_SSH_USER': 'fixture', 'VPS_SSH_PATH': str(self.public),
        }, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.manager = EmergencyFallbackManager()
        self.source = self.manager.fallback_root / 'portfolio'
        self.source.mkdir(parents=True)
        self.public.mkdir()
        for theme, color in (('light', 'white'), ('dark', 'black')):
            Image.new('RGB', (48, 64), color).save(self.source / f'{theme}.webp')
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                f'color=c={color}:s=48x64:d=0.2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                str(self.source / f'{theme}.mp4')], check=True, timeout=15)
        self.source_hashes = self.hashes(self.source)
        self.result = {'post_id': 'fixture-post', 'permalink': 'https://www.instagram.com/reel/fallback_fixture/'}
        self.pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        p = self.pipeline
        p.run_date = '2030-01-02'; p.generation_started_at = '2030-01-02T08:00:00Z'
        p.output_dir = self.private / 'runtime' / 'output' / p.run_date
        p.post_to_instagram = True; p.portfolio_media_enabled = True; p.media_mode = 'live_vps'
        p.daily_run = DailyRunStateManager(run_date=p.run_date, timezone_name='Asia/Kolkata', run_token='fixture')
        p.daily_run.state_path.write_text(json.dumps({'date': p.run_date, 'status': 'POSTED',
            'asset_source': 'emergency_fallback', 'instagram_post_id': self.result['post_id']}))
        self.posted_bytes = p.daily_run.state_path.read_bytes()
        self.previous = build_metadata(date='2030-01-01', title='PREVIOUS ARTWORK',
            post_result={'post_id': 'previous-post', 'permalink': 'https://www.instagram.com/reel/previous_fixture/'},
            base_url=os.environ['VPS_PUBLIC_BASE_URL'], revision='a' * 64)
        publish_metadata(self.previous, self.public, generation_started_at='2030-01-01T08:00:00Z')
        self.latest = self.public / 'portfolio/latest/metadata.json'

    @staticmethod
    def hashes(directory):
        return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                for name in PORTFOLIO_MEDIA_FILENAMES}

    def deliver(self, unavailable=None):
        def public_response(url, **kwargs):
            target = self.public / urlsplit(url).path.lstrip('/')
            directories = (self.public / parent for parent in target.relative_to(self.public).parents)
            traversable = all(directory.stat().st_mode & 0o001 for directory in directories)
            accessible = traversable and target.is_file() and target.stat().st_mode & 0o004
            response = Response(); response.url = url
            response.status_code = 200 if accessible and target.name != unavailable else 404
            response.headers['Content-Type'] = 'video/mp4' if target.suffix == '.mp4' else 'image/webp'
            response.raw = io.BytesIO(target.read_bytes() if response.status_code == 200 else b'')
            return response

        # The mounted publisher is real; its server-path guard rejects isolated scratch storage.
        with patch('pipeline.get_live_vps_config_error', return_value=None), \
                patch('pipeline.requests.get', side_effect=public_response), patch('pipeline.time.sleep'), \
                patch('pipeline.get_notifier') as notifier, \
                patch.object(self.pipeline, 'step_16_render_portfolio_media') as render:
            self.pipeline._run_portfolio_media_secondary(fallback_manager=self.manager,
                post_result=self.result, title='ERROR 404')
            render.assert_not_called()
        self.assertEqual(self.pipeline.daily_run.state_path.read_bytes(), self.posted_bytes)
        return notifier

    def test_fallback_mounted_delivery_publishes_matching_pair_under_private_umask(self):
        self.deliver()
        revision = media_revision(self.source)
        archive = self.public / 'portfolio' / self.pipeline.run_date / revision
        payload = json.loads(self.latest.read_text())
        self.assertEqual(payload, build_metadata(date=self.pipeline.run_date, title='ERROR 404',
            post_result=self.result, base_url=os.environ['VPS_PUBLIC_BASE_URL'], revision=revision))
        self.assertEqual(payload['schemaVersion'], 1)
        self.assertEqual(payload['instagramUrl'], self.result['permalink'])
        self.assertEqual(self.hashes(archive), self.source_hashes)
        for directory in (self.public, self.public / 'portfolio', archive.parent, archive, self.latest.parent):
            self.assertEqual(directory.stat().st_mode & 0o777, 0o755)
        for name in PORTFOLIO_MEDIA_FILENAMES:
            self.assertEqual((archive / name).stat().st_mode & 0o777, 0o644)
            self.assertEqual((self.latest.parent / name).stat().st_mode & 0o777, 0o644)
            self.assertEqual((self.source / name).stat().st_mode & 0o777, 0o600)
        for metadata in (self.latest, archive.parent / 'metadata.json'):
            self.assertEqual(metadata.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.hashes(self.source), self.source_hashes)
        self.assertEqual(self.source.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.pipeline.output_dir / 'portfolio').stat().st_mode & 0o777, 0o700)
        control = self.public / '.portfolio-publication'
        self.assertEqual(control.stat().st_mode & 0o777, 0o700)
        for path in control.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_existing_restrictive_revision_is_repaired_without_changing_bytes(self):
        self.deliver()
        archive = self.public / 'portfolio' / self.pipeline.run_date / media_revision(self.source)
        original = self.hashes(archive)
        archive.parent.chmod(0o700); archive.chmod(0o700)
        for name in PORTFOLIO_MEDIA_FILENAMES:
            (archive / name).chmod(0o600)
        self.deliver()
        self.assertEqual(self.hashes(archive), original)
        self.assertEqual(archive.parent.stat().st_mode & 0o777, 0o755)
        self.assertEqual(archive.stat().st_mode & 0o777, 0o755)
        self.assertTrue(all((archive / name).stat().st_mode & 0o777 == 0o644 for name in PORTFOLIO_MEDIA_FILENAMES))
        self.assertEqual(json.loads(self.latest.read_text())['title'], 'ERROR 404')

    def test_unreachable_fallback_preserves_feed_ordering_and_confirmed_post(self):
        previous = self.latest.read_bytes()
        order = self.public / '.portfolio-publication/latest.json'
        previous_order = order.read_bytes()
        notifier = self.deliver(unavailable='dark.mp4')
        self.assertEqual(self.latest.read_bytes(), previous)
        self.assertEqual(order.read_bytes(), previous_order)
        self.assertFalse((self.public / 'portfolio' / self.pipeline.run_date / 'metadata.json').exists())
        notifier.return_value.notify_warning.assert_called_once()

    def test_late_fallback_cannot_replace_a_newer_feed(self):
        newer = build_metadata(date='2030-01-03', title='NEWER ARTWORK',
            post_result={'post_id': 'newer-post', 'permalink': 'https://www.instagram.com/reel/newer_fixture/'},
            base_url=os.environ['VPS_PUBLIC_BASE_URL'], revision='b' * 64)
        publish_metadata(newer, self.public, generation_started_at='2030-01-03T08:00:00Z')
        latest = self.latest.read_bytes()
        self.deliver()
        self.assertEqual(self.latest.read_bytes(), latest)
        dated = self.public / 'portfolio' / self.pipeline.run_date / 'metadata.json'
        self.assertEqual(json.loads(dated.read_text())['title'], 'ERROR 404')


if __name__ == '__main__':
    unittest.main()

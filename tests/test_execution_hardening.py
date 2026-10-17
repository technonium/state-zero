"""Real boundary behavior with isolated storage and mocked external calls."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
from flow_media_client import FlowMediaClient, FlowCommandError
from google_image_client import GoogleImageClient
from google_video_client import GoogleVideoClient
from instagram_poster import InstagramPoster, InstagramPublicationUncertain, read_publication_record


class ExecutionHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'STATE_ZERO_PRIVATE_ROOT': str(self.root),
            'GOOGLE_API_KEY_PRIMARY': 'test-key', 'PIPELINE_POST_TO_INSTAGRAM': 'true'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def freeze(self):
        for name in FlowMediaClient._input_names(self.root):
            (self.root / name).write_text('{}' if name.endswith('.json') else 'original')
        FlowMediaClient.freeze_inputs(self.root)

    def test_api_stage_reuses_completed_image_across_invocations(self):
        self.freeze()
        target = self.root / 'generated_art.png'
        def generate(request, candidate):
            Image.new('RGB', (768, 1024)).save(candidate)
        with patch.object(GoogleImageClient, 'generate_from_json', side_effect=generate) as call, \
                patch.object(FlowMediaClient, '_generation_seconds', return_value=7200):
            FlowMediaClient().generate_api_stage('image', {}, target)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            FlowMediaClient().generate_api_stage('image', {}, target)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), digest)

    def test_api_uncertain_attempt_cannot_spend_again_after_restart(self):
        self.freeze()
        target = self.root / 'generated_art.png'
        with patch.object(GoogleImageClient, 'generate_from_json', side_effect=TimeoutError('lost response')) as call, \
                patch.object(FlowMediaClient, '_generation_seconds', return_value=7200):
            with self.assertRaises(TimeoutError):
                FlowMediaClient().generate_api_stage('image', {}, target)
            with self.assertRaises(FlowCommandError):
                FlowMediaClient().generate_api_stage('image', {}, target)
        self.assertEqual(call.call_count, 1)

    def test_upload_error_name_does_not_invent_policy_rejection(self):
        payload = {'status': 'error', 'error': {'class': 'MediaUploadRejectedError',
            'detail': 'migrated host: maseQ answered 200 without a media id'}}
        with patch('flow_media_client.subprocess.run', return_value=subprocess.CompletedProcess([], 27, json.dumps(payload), '')):
            with self.assertRaises(FlowCommandError) as error:
                FlowMediaClient._run_json(['gflow', 'video'], 10)
        self.assertEqual(error.exception.category, 'pre_submit_transient')
        self.assertNotIn('provider reason', error.exception.detail)
        self.assertFalse(FlowMediaClient._stops_replacement(error.exception))

    def test_provider_switch_cannot_reset_api_allowance(self):
        self.freeze()
        output = self.root / 'generated_art.png'
        receipt = self.root / 'flow_image_diagnostics.json'
        receipt.write_text(json.dumps({'api_fallback_state': 'started'}))
        with patch.object(GoogleImageClient, 'generate_from_json') as api:
            with self.assertRaises(FlowCommandError):
                FlowMediaClient().generate_api_stage('image', {}, output)
        api.assert_not_called()
        receipt.unlink()
        marker = self.root / 'flow_image_attempt_1.submit'
        marker.write_text('{"state":"forwarded"}')
        with patch.object(GoogleImageClient, 'generate_from_json') as api:
            with self.assertRaises(FlowCommandError):
                FlowMediaClient().generate_api_stage('image', {}, output)
        api.assert_not_called()
        marker.unlink()
        (self.root / 'google_image_diagnostics.json').write_text(json.dumps({'logical_attempt': 'reserved'}))
        with patch.object(FlowMediaClient, '_generate_image') as flow:
            with self.assertRaises(FlowCommandError):
                FlowMediaClient().generate_image({}, output)
        flow.assert_not_called()

    def test_api_request_must_match_frozen_prompt(self):
        self.freeze()
        with patch.object(GoogleImageClient, 'generate_from_json') as api:
            with self.assertRaises(FlowCommandError):
                FlowMediaClient().generate_api_stage('image', {'changed': True}, self.root / 'generated_art.png')
        api.assert_not_called()

    def test_legacy_api_media_without_manifest_blocks_before_input_rewrite(self):
        from pipeline import WHOOPPipeline, PipelineStageError
        p = WHOOPPipeline.__new__(WHOOPPipeline); p.output_dir = self.root
        original = self.root / 'generated_art.png'; original.write_bytes(b'legacy media')
        with patch.dict(os.environ, {'MEDIA_GENERATION_PROVIDER': 'google_api'}):
            with self.assertRaises(PipelineStageError):
                p._check_flow_submission_state()
        self.assertEqual(original.read_bytes(), b'legacy media')
        original.unlink()
        (self.root / 'flow_video_attempt_1.submit').write_text('{"state":"forwarded"}')
        with patch.dict(os.environ, {'MEDIA_GENERATION_PROVIDER': 'google_api'}):
            with self.assertRaises(PipelineStageError):
                p._check_flow_submission_state()

    def test_unreadable_daily_state_is_not_an_empty_slate(self):
        from daily_run_state import DailyRunStateManager, DailyStateUnreadableError
        manager = DailyRunStateManager(run_date='2026-10-18', timezone_name='Asia/Kolkata', run_token='test')
        manager.state_path.write_text('{broken')
        with self.assertRaises(DailyStateUnreadableError):
            manager.acquire()
        self.assertFalse(manager.claim_path.exists())

    def poster(self):
        poster = object.__new__(InstagramPoster)
        poster.mock_mode = False
        poster.access_token = 'test'
        poster.user_id = 'account'
        poster.base_url = 'https://example.test'
        poster.diagnostics_output_dir = self.root
        poster.run_date = '2026-10-18'
        poster.publish_context = None
        return poster

    def test_publish_timeout_checks_same_container_and_never_posts_twice(self):
        import requests
        poster = self.poster()
        with patch('instagram_poster.requests.post', side_effect=requests.Timeout('lost reply')) as send, \
                patch('instagram_poster.requests.get', return_value=Mock(status_code=200, json=lambda: {'status_code': 'PUBLISHED'})):
            with self.assertRaises(InstagramPublicationUncertain):
                poster.publish_media('exact-container')
        record = read_publication_record(self.root)
        self.assertEqual(record['creation_id'], 'exact-container')
        self.assertEqual(record['publication_state'], 'published_unidentified')
        self.assertEqual(send.call_count, 1)

    def test_post_id_is_durable_before_permalink_lookup(self):
        poster = self.poster()
        with patch('instagram_poster.requests.post', return_value=Mock(status_code=200, headers={}, json=lambda: {'id': 'post-1'})):
            self.assertEqual(poster.publish_media('container-1'), 'post-1')
        self.assertEqual(read_publication_record(self.root)['post_id'], 'post-1')

    def test_next_retry_matches_real_schedule(self):
        from pipeline import WHOOPPipeline
        from datetime import datetime
        from zoneinfo import ZoneInfo
        p = WHOOPPipeline.__new__(WHOOPPipeline)
        self.assertEqual(p._next_cron_slot_ist(datetime(2026, 10, 18, 13, 24, tzinfo=ZoneInfo('Asia/Kolkata'))), '14:00 IST')

    def test_status_delivery_has_one_bounded_attempt(self):
        from notifier import Notifier
        with patch.object(Notifier, '_instance', None), patch.dict(os.environ, {
                'TELEGRAM_BOT_TOKEN': 'test', 'TELEGRAM_CHAT_ID': 'test'}):
            notifier = Notifier()
            with patch.object(notifier, '_telegram_request', side_effect=TimeoutError('offline')) as call:
                self.assertFalse(notifier.notify_status('2026-10-18', 'TEST', 'testing'))
            self.assertEqual(call.call_count, 1)
            self.assertLessEqual(sum(call.call_args.kwargs['timeout']), 10)

    def test_public_portfolio_ignores_restrictive_private_umask(self):
        from pipeline import WHOOPPipeline
        p = WHOOPPipeline.__new__(WHOOPPipeline)
        p.run_date = '2026-10-18'; p.post_to_instagram = True
        p.media_mode = 'local_test'; p.local_vps_dir = self.root / 'public'
        source = self.root / 'fixtures'; source.mkdir()
        for name in ('light.webp', 'dark.webp', 'light.mp4', 'dark.mp4'):
            (source / name).write_bytes(name.encode()); (source / name).chmod(0o600)
        old = os.umask(0o077)
        try:
            with patch.dict(os.environ, {'VPS_PUBLIC_BASE_URL': 'https://media.example.test'}), \
                    patch.object(p, '_ensure_public_urls_reachable'):
                p.step_17_upload_portfolio_vps(source)
        finally:
            os.umask(old)
        target = p.local_vps_dir / 'portfolio' / p.run_date / p.portfolio_revision
        self.assertEqual(target.stat().st_mode & 0o777, 0o755)
        self.assertEqual(p.local_vps_dir.stat().st_mode & 0o777, 0o755)
        self.assertEqual((target / 'light.webp').stat().st_mode & 0o777, 0o644)
        self.assertEqual((source / 'light.webp').stat().st_mode & 0o777, 0o600)
        target.chmod(0o700)
        for path in target.iterdir():
            path.chmod(0o600)
        with patch.dict(os.environ, {'VPS_PUBLIC_BASE_URL': 'https://media.example.test'}), \
                patch.object(p, '_ensure_public_urls_reachable'):
            p.step_17_upload_portfolio_vps(source)
        self.assertEqual(target.stat().st_mode & 0o777, 0o755)
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o644 for path in target.iterdir()))

    def test_timed_out_prepublication_group_is_reaped(self):
        from utils import run_bounded
        with self.assertRaises(subprocess.TimeoutExpired):
            run_bounded([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=.05)

    def test_public_card_upload_corrects_private_source_modes(self):
        from pipeline import WHOOPPipeline
        p = WHOOPPipeline.__new__(WHOOPPipeline)
        p.run_date = '2026-10-18'; p.post_to_instagram = True
        p.media_mode = 'local_test'; p.local_vps_dir = self.root / 'public'
        video = self.root / 'card.mp4'; video.write_bytes(b'video'); video.chmod(0o600)
        image = self.root / 'card.png'; image.write_bytes(b'image'); image.chmod(0o600)
        old = os.umask(0o077)
        try:
            with patch.dict(os.environ, {'VPS_PUBLIC_BASE_URL': 'https://media.example.test'}), \
                    patch.object(p, '_ensure_public_urls_reachable'), patch.object(p, '_set_heartbeat_context'):
                p.step_12_upload_vps(video, image)
        finally:
            os.umask(old)
        self.assertEqual(p.local_vps_dir.stat().st_mode & 0o777, 0o755)
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o644 for path in p.local_vps_dir.iterdir()))
        self.assertEqual(video.stat().st_mode & 0o777, 0o600)

    def test_ssh_portfolio_upload_opens_public_ancestor(self):
        from pipeline import WHOOPPipeline
        p = WHOOPPipeline.__new__(WHOOPPipeline)
        p.run_date = '2026-10-18'; p.post_to_instagram = True; p.media_mode = 'live_vps'
        source = self.root / 'fixtures'; source.mkdir()
        for name in ('light.webp', 'dark.webp', 'light.mp4', 'dark.mp4'):
            (source / name).write_bytes(name.encode())
        remote = self.root / 'absent'
        with patch.dict(os.environ, {'VPS_PUBLIC_BASE_URL': 'https://media.example.test',
                'VPS_SSH_HOST': 'example.test', 'VPS_SSH_USER': 'test', 'VPS_SSH_PATH': str(remote)}), \
                patch('pipeline.get_live_vps_config_error', return_value=None), \
                patch('pipeline.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')) as ssh, \
                patch.object(p, '_ensure_public_urls_reachable'):
            p.step_17_upload_portfolio_vps(source)
        activation = next(call.args[0][-1] for call in ssh.call_args_list if 'chmod 755' in call.args[0][-1])
        self.assertIn(f'chmod 755 {remote} {remote}/portfolio ', activation)

    def test_rescue_lock_wait_is_bounded(self):
        import fcntl
        from ops.run_rescue import acquire_execution_lock
        path = self.root / 'execution.lock'
        with path.open('a') as owner, path.open('a') as contender:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch('ops.run_rescue.time.monotonic', side_effect=[0, 0, 601]), \
                    patch('ops.run_rescue.time.sleep'):
                self.assertFalse(acquire_execution_lock(contender, 600))

    def test_notification_exception_cannot_stop_emergency_publish_or_archive(self):
        from pipeline import WHOOPPipeline
        from types import SimpleNamespace
        p = WHOOPPipeline.__new__(WHOOPPipeline)
        p.output_dir = self.root; p.run_date = '2026-10-18'
        p.daily_run = Mock(); p.daily_run.is_owner.return_value = False
        p._set_heartbeat_context = Mock(); p._ensure_owner_runtime_dirs = Mock()
        p._ensure_public_urls_reachable = Mock(); p._run_portfolio_media_secondary = Mock()
        p.step_14_post_instagram = Mock(return_value={'post_id': 'post', 'permalink': 'https://example.test/post'})
        manager = Mock()
        manager.load_and_validate_manifest.return_value = {'version': 'error_404_v1', 'title': 'ERROR 404', 'scene_description': 'fixture'}
        manager.copy_to_run_output.return_value = {'mp4_path': self.root / 'card.mp4', 'png_path': self.root / 'card.png'}
        manager.get_publish_strategy.return_value = {'mode': 'prehosted', 'video_url': 'https://example.test/video', 'thumb_url': 'https://example.test/image'}
        manager.write_emergency_log.side_effect = OSError('fixture archive failure')
        with patch('emergency_fallback_manager.EmergencyFallbackManager', return_value=manager), \
                patch('database_manager.CardDatabase') as db, \
                patch('pipeline.get_notifier', side_effect=RuntimeError('fixture notification failure')):
            self.assertTrue(p._run_emergency_fallback('Image Generation', 'fixture technical failure'))
        p.step_14_post_instagram.assert_called_once()
        db.return_value.insert_fallback_post.assert_called_once()

    def test_success_notification_exception_preserves_confirmed_result(self):
        from pipeline import WHOOPPipeline
        from types import SimpleNamespace
        p = WHOOPPipeline.__new__(WHOOPPipeline)
        p.output_dir = self.root; p.run_date = '2026-10-18'; p.run_token = 'fixture'
        p.post_to_instagram = True; p.in_emergency_fallback = False
        p.daily_run = Mock(); p.daily_run.load_state.return_value = {}
        p._set_heartbeat_context = Mock(); p._build_instagram_publish_context = Mock(return_value={})
        p._mark_posted_terminal_success = Mock()
        token = Mock(); token.get_valid_token.return_value = 'test'; token.get_user_id.return_value = 'account'
        poster = Mock(); poster.publish_with_strategy.return_value = SimpleNamespace(post_id='post', permalink='https://example.test/post')
        with patch('instagram_token_manager.get_instagram_token_manager', return_value=token), \
                patch('instagram_poster.InstagramPoster', return_value=poster), \
                patch('pipeline.get_notifier', side_effect=RuntimeError('fixture notification failure')):
            result = p.step_14_post_instagram('https://example.test/video', 'https://example.test/image', 'fixture')
        self.assertEqual(result['post_id'], 'post')
        p._mark_posted_terminal_success.assert_called_once()

    def test_auto_strategy_cannot_switch_after_uncertain_binary_publication(self):
        from instagram_poster import InstagramPublishDiagnosticsError
        poster = self.poster()
        error = InstagramPublicationUncertain('publish_media', 'lost response', {'creation_id': 'exact'})
        with patch.object(poster, 'publish_resumable_binary_and_get_result', side_effect=error), \
                patch.object(poster, 'publish_and_get_result') as alternate:
            with self.assertRaises(InstagramPublicationUncertain):
                poster.publish_with_strategy(video_url='https://example.test/video', cover_url='https://example.test/image',
                    caption='test', local_video_path=self.root / 'card.mp4', strategy='auto')
        alternate.assert_not_called()

    def test_restart_cannot_reset_publication_recovery_allowance(self):
        poster = self.poster()
        poster._persist_diagnostics({'phase': 'publish_media', 'publication_state': 'uncertain',
            'creation_id': 'exact', 'reconciliation_deadline': 1})
        with patch('instagram_poster.requests.post') as send, patch('instagram_poster.requests.get') as query:
            with self.assertRaises(InstagramPublicationUncertain):
                poster.publish_media('exact')
        send.assert_not_called(); query.assert_not_called()

    def test_emergency_archive_repair_never_creates_normal_card(self):
        from pipeline import WHOOPPipeline
        from database_manager import CardDatabase
        p = WHOOPPipeline.__new__(WHOOPPipeline)
        p.output_dir = self.root; p.post_to_instagram = True; p.run_date = '2026-10-18'
        evidence = {'run_date': p.run_date, 'asset_source': 'emergency_fallback', 'instagram_post_id': 'post',
            'fallback_version': 'error_404_v1', 'title': 'ERROR 404'}
        (self.root / 'emergency_fallback_used.json').write_text(json.dumps(evidence))
        with patch.object(p, 'finalize_posted_run') as normal:
            p._recover_posted_archive_if_needed({'asset_source': 'emergency_fallback', 'instagram_post_id': 'post'})
        normal.assert_not_called()
        records = CardDatabase().published_records_for_date(p.run_date)
        self.assertEqual(records[0]['asset_source'], 'emergency_fallback')
        self.assertFalse(CardDatabase().has_card_for_date(p.run_date))

    def test_conflicting_database_and_state_post_ids_block_new_claim(self):
        from pipeline import WHOOPPipeline
        from database_manager import CardDatabase
        from daily_run_state import DailyRunStateManager
        p = WHOOPPipeline.__new__(WHOOPPipeline)
        p.output_dir = self.root; p.post_to_instagram = True; p.run_date = '2026-10-18'
        p.daily_run = DailyRunStateManager(run_date=p.run_date, timezone_name='Asia/Kolkata', run_token='test')
        p.daily_run.state_path.write_text(json.dumps({'date': p.run_date, 'status': 'POSTED', 'instagram_post_id': 'different'}))
        CardDatabase().insert_fallback_post({'run_date': p.run_date, 'instagram_post_id': 'post'})
        with self.assertRaises(InstagramPublicationUncertain):
            p._check_publication_before_claim()
        self.assertFalse(p.daily_run.claim_path.exists())

    def test_api_video_resumes_accepted_operation_without_creation(self):
        self.freeze()
        image = self.root / 'generated_art.png'; Image.new('RGB', (768, 1024)).save(image)
        output = self.root / 'generated_video.mp4'
        def fail(*args):
            diagnostic = self.root / 'google_video_diagnostics.json'
            FlowMediaClient._write_diagnostics(diagnostic, {'operation_name': 'operations/exact'})
            raise TimeoutError('poll interrupted')
        def resume(candidate):
            candidate.write_bytes(b'validated-video')
        with patch.object(FlowMediaClient, '_generation_seconds', return_value=7200), \
                patch.object(GoogleVideoClient, 'generate_from_image', side_effect=fail) as start, \
                patch.object(GoogleVideoClient, 'resume_operation', side_effect=resume) as poll, \
                patch.object(FlowMediaClient, '_probe_video', return_value=(1080, 1920, 8, True)):
            with self.assertRaises(TimeoutError):
                FlowMediaClient().generate_api_stage('video', 'original', output, image)
            FlowMediaClient().generate_api_stage('video', 'original', output, image)
        self.assertEqual(start.call_count, 1); self.assertEqual(poll.call_count, 1)
        self.assertEqual(output.read_bytes(), b'validated-video')

    def test_two_technical_upload_failures_reach_api_video_without_flow_submission(self):
        self.freeze()
        art = self.root / 'generated_art.png'; Image.new('RGB', (768, 1024)).save(art)
        output = self.root / 'generated_video.mp4'
        payload = {'status': 'error', 'error': {'class': 'MediaUploadRejectedError',
            'detail': 'upload_response_unrecognized: HTTP 200 rpc=maseQ bytes=0 frames=0'}}
        def video(prompt, image, target):
            target.write_bytes(b'valid-video')
        with patch.dict(os.environ, {'FLOW_API_FALLBACK_ENABLED': 'true'}), \
                patch.object(FlowMediaClient, '_generation_seconds', return_value=7200), \
                patch('flow_media_client.subprocess.run', return_value=subprocess.CompletedProcess([], 27, json.dumps(payload), '')) as flow, \
                patch.object(GoogleVideoClient, 'generate_from_image', side_effect=video) as api, \
                patch.object(FlowMediaClient, '_probe_video', return_value=(1080, 1920, 8, True)), \
                patch.object(FlowMediaClient, '_notify_transition'):
            FlowMediaClient().generate_video('original', art, output)
        self.assertEqual(flow.call_count, 2); self.assertEqual(api.call_count, 1)
        self.assertEqual(list(self.root.glob('flow_video_attempt_*.submit')), [])
        saved = json.loads((self.root / 'flow_video_diagnostics.json').read_text())
        self.assertEqual(saved['provider'], 'google_api')
        self.assertTrue(all(not a['submit_attempted'] for a in saved['attempts']))

import json
import os
import subprocess
import hashlib
import fcntl
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/scripts'))
sys.path.insert(0, str(ROOT / 'ops'))
from flow_media_client import FlowCommandError, FlowMediaClient


from flow_fixture import FrozenFlowCalls, freeze


class FlowRetrySafetyTests(FrozenFlowCalls, unittest.TestCase):
    def test_pending_submission_requires_reconciliation_for_both_stages(self):
        for stage in ('image', 'video'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                marker = root / f'flow_{stage}_attempt_1.submit'
                marker.write_text('{"state":"forwarded"}')
                failure = FlowMediaClient._pending_submission(root)
                self.assertEqual(failure.stage, stage)
                self.assertEqual(failure.category, 'submission_uncertain')
                self.assertEqual(marker.read_text(), '{"state":"forwarded"}')

    def test_corrupt_evidence_fails_closed_but_proven_pre_submit_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diagnostic = root / 'flow_image_diagnostics.json'
            diagnostic.write_text('broken')
            self.assertIsNotNone(FlowMediaClient._pending_submission(root))
            diagnostic.write_text(json.dumps({'record_version':2, 'failure': {'category': 'pre_submit_timeout',
                                                          'submission_state': 'not_attempted'}}))
            self.assertIsNone(FlowMediaClient._pending_submission(root))
            (root / 'flow_image_attempt_1.submit').write_text('unreadable')
            self.assertIsNotNone(FlowMediaClient._pending_submission(root))

    def test_pipeline_stops_before_lookups_and_prompt_overwrite(self):
        from pipeline import WHOOPPipeline, PipelineStageError
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'MEDIA_GENERATION_PROVIDER': 'flow'}):
            root = Path(tmp)
            files = {'image_prompt.json': '{"scene":"original"}', 'daily_data.json': '{}',
                     'flow_image_attempt_1.submit': '{"state":"forwarded"}'}
            for name, value in files.items():
                (root / name).write_text(value)
            pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
            pipeline.output_dir = root
            with self.assertRaises(PipelineStageError) as failure:
                pipeline._check_flow_submission_state()
            self.assertEqual(failure.exception.failure_classification, 'flow_reconciliation_required')
            self.assertEqual({n: (root / n).read_text() for n in files}, files)
            with patch.object(pipeline, '_claim_daily_run_or_exit'), \
                    patch.object(pipeline, '_ensure_owner_runtime_dirs'), \
                    patch.object(pipeline, '_stop_heartbeat_thread'), \
                    patch.object(pipeline, '_cleanup_non_authoritative_daily_state'), \
                    patch('pipeline._setup_global_exception_handler'), \
                    patch.object(pipeline, '_handle_runtime_stage_error', return_value=True) as handler:
                pipeline.mode, pipeline.post_to_instagram = 'automatic', False
                pipeline.media_mode, pipeline.run_token = 'local_test', 'test'
                pipeline.step_1_validate = Mock()
                pipeline.step_2_3_lookups = Mock()
                pipeline.step_4_6_prompts = Mock()
                pipeline.run()
                pipeline.step_1_validate.assert_not_called()
                pipeline.step_2_3_lookups.assert_not_called()
                pipeline.step_4_6_prompts.assert_not_called()
                handler.assert_called_once()

    def test_reconciliation_does_not_claim_fresh_retry_and_notification_failure_is_nonfatal(self):
        from pipeline import WHOOPPipeline, PipelineStageError
        from unittest.mock import Mock
        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.daily_run, pipeline._stop_heartbeat_thread = Mock(), Mock()
        pipeline.run_date = '2026-10-17'
        error = PipelineStageError('Flow image reconciliation', 'Inspect the original Flow project',
                                   failure_classification='flow_reconciliation_required')
        with patch.object(pipeline, '_is_terminal_rescue_run', return_value=False), \
                patch('pipeline.safe_notify_status', return_value=False):
            self.assertTrue(pipeline._handle_runtime_stage_error(error))
        pipeline.daily_run.release_claim.assert_called_once()
        message = pipeline.daily_run.mark_retryable_failure.call_args.kwargs['message']
        self.assertNotIn('Next retry', message)
        pipeline.daily_run.mark_fatal_failure.assert_not_called()

    def test_reconciliation_preserves_terminal_emergency_rescue(self):
        from pipeline import WHOOPPipeline, PipelineStageError
        from unittest.mock import Mock
        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.post_to_instagram, pipeline.in_emergency_fallback = True, False
        pipeline.run_date = '2026-10-17'
        error = PipelineStageError('Flow image reconciliation', 'Inspect original Flow project',
            fallback_eligible=True, failure_classification='flow_reconciliation_required')
        with patch.object(pipeline, '_is_terminal_rescue_run', return_value=True), \
                patch.object(pipeline, '_run_emergency_fallback', return_value=True) as rescue, \
                patch('pipeline.get_notifier', return_value=Mock()), \
                patch.dict(os.environ, {'EMERGENCY_FALLBACK_ENABLED': 'true'}):
            self.assertTrue(pipeline._handle_runtime_stage_error(error))
        rescue.assert_called_once()

    def image_response(self, command, timeout, marker):
        raw = Path(command[command.index('--output') + 1])
        Image.new('RGB', (768, 1024), 'white').save(raw)
        marker.write_text(json.dumps({'state': 'forwarded', 'expected_model': 'NARWHAL',
                                     'actual_model': 'BELUGA', 'selected_model': 'Nano Banana 2.1'}))
        return {'status': 'ok', 'count': 1, 'model': 'NARWHAL', 'project_id': 'project',
                'images': [{'local_path': str(raw), 'media_name': 'image-id', 'model_name_type': None}]}

    def test_completed_image_is_reused_and_changed_input_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'FLOW_IMAGE_UPSCALE_2K': 'false'}):
            target = Path(tmp) / 'generated_art.png'
            client = FlowMediaClient()
            with patch.object(client, '_run_json', side_effect=self.image_response) as generate:
                self.frozen_image(client, {'scene': 'cloud'}, target)
                self.frozen_image(client, {'scene': 'cloud'}, target)
                with self.assertRaises(FlowCommandError):
                    self.frozen_image(client, {'scene': 'different'}, target)
            self.assertEqual(generate.call_count, 1)

    def test_legacy_forwarded_marker_blocks_generation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'FLOW_SHADOW_RECOVERY_LOCKED': 'true'}):
            root = Path(tmp)
            (root / 'flow_image_attempt_1.submit').write_text('{"state":"forwarded"}')
            with patch.object(FlowMediaClient, '_run_json') as generate:
                with self.assertRaises(FlowCommandError):
                    self.frozen_image(FlowMediaClient(), {'scene': 'cloud'}, root / 'art.png')
            generate.assert_not_called()

    def test_legacy_recovery_flag_cannot_restart_auth_failed_image(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            client = FlowMediaClient()
            with patch.object(client, '_run_json', side_effect=FlowCommandError('image', 'auth_required', submission_state='not_attempted')) as generate:
                for flag in ('false', 'true', 'true'):
                    with patch.dict(os.environ, {'FLOW_SHADOW_RECOVERY_LOCKED': flag}), self.assertRaises(FlowCommandError):
                        self.frozen_image(client, {}, Path(tmp) / 'art.png')
            generate.assert_called_once()

    def test_legacy_recovery_flag_cannot_restart_auth_failed_video(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp); art = root / 'art.png'
            Image.new('RGB', (768, 1024), 'white').save(art)
            client = FlowMediaClient()
            with patch.object(client, '_run_json', side_effect=FlowCommandError('video', 'auth_required', submission_state='not_attempted')) as generate:
                for flag in ('false', 'true'):
                    with patch.dict(os.environ, {'FLOW_SHADOW_RECOVERY_LOCKED': flag}), self.assertRaises(FlowCommandError):
                        self.frozen_video(client, 'move', art, root / 'video.mp4')
            generate.assert_called_once()

    def test_missing_child_evidence_keeps_one_conservative_reservation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                'FLOW_SHADOW_RECOVERY_LOCKED': 'true'}, clear=True):
            error = FlowCommandError('image', 'pre_submit_timeout', submission_state='not_attempted')
            with patch.object(FlowMediaClient, '_run_json', side_effect=error) as generate:
                for _ in range(3):
                    with self.assertRaises(FlowCommandError):
                        self.frozen_image(FlowMediaClient(), {}, Path(tmp) / 'art.png')
                self.assertEqual(generate.call_count, 1)

    def test_existing_canonical_without_identity_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            art, video = root / 'art.png', root / 'video.mp4'
            Image.new('RGB', (768, 1024), 'white').save(art)
            video.write_bytes(b'existing video')
            with patch.object(FlowMediaClient, '_run_json') as generate:
                with self.assertRaises(FlowCommandError):
                    self.frozen_image(FlowMediaClient(), {}, art)
                with self.assertRaises(FlowCommandError):
                    self.frozen_video(FlowMediaClient(), 'move', art, video)
                generate.assert_not_called()
            self.assertEqual(video.read_bytes(), b'existing video')

    def test_missing_canonical_image_recovers_saved_result_without_generation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'FLOW_IMAGE_UPSCALE_2K': 'false'}):
            target = Path(tmp) / 'art.png'
            client = FlowMediaClient()
            with patch.object(client, '_run_json', side_effect=self.image_response) as generate:
                self.frozen_image(client, {'scene': 'cloud'}, target)
                target.unlink()
                self.frozen_image(client, {'scene': 'cloud'}, target)
            self.assertEqual(generate.call_count, 1)
            self.assertTrue(target.exists())

    def test_concurrent_invocation_cannot_submit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (root / '.flow-generation.lock').open('a') as lock, \
                    patch.object(FlowMediaClient, '_run_json') as generate:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(FlowCommandError) as failure:
                    self.frozen_image(FlowMediaClient(), {}, root / 'art.png')
                self.assertEqual(failure.exception.category, 'execution_busy')
                generate.assert_not_called()

    def test_video_recovers_exact_id_and_rejects_a_changed_start_frame(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                'FLOW_VIDEO_UPSCALE_1080P': 'false'}, clear=True):
            root = Path(tmp)
            art, video = root / 'art.png', root / 'video.mp4'
            Image.new('RGB', (768, 1024), 'white').save(art)
            raw = root / 'flow_raw_video.mp4'
            def generated(command, timeout, marker):
                raw.write_bytes(b'original')
                marker.with_suffix('.context.json').write_text('{}')
                marker.write_text('submit_attempted\n')
                return {'succeeded': True, 'media_id': 'exact-id', 'local_path': str(raw),
                        'request': {'count': 1, 'model': 'veo_3_1_fast', 'mode': 'i2v', 'aspect': 'portrait'}}
            recovered = root / 'recovered.mp4'
            recovered.write_bytes(b'original')
            client = FlowMediaClient()
            with patch.object(client, '_run_json', side_effect=generated) as generate, \
                    patch.object(client, '_probe_video', return_value=(1080, 1920, 8, True)), \
                    patch.object(client, '_readback_media', return_value={'succeeded': True, 'media_id': 'exact-id', 'local_path': str(recovered), 'request': {'count': 1, 'model': 'veo_3_1_fast', 'mode': 'i2v', 'aspect': 'portrait'}}) as recover:
                self.frozen_video(client, 'move', art, video)
                video.unlink()
                raw.unlink()
                self.frozen_video(client, 'move', art, video)
                recover.assert_called_once()
                self.assertEqual(recover.call_args.kwargs['known']['media_id'], 'exact-id')
                Image.new('RGB', (768, 1024), 'black').save(art)
                with self.assertRaises(FlowCommandError):
                    self.frozen_video(client, 'move', art, video)
                self.assertEqual(generate.call_count, 1)

    def test_changed_original_cannot_be_reused(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            client = FlowMediaClient()
            target = root / 'art.png'
            with patch.object(client, '_run_json', side_effect=self.image_response) as generate:
                self.frozen_image(client, {}, target)
                target.unlink()
                Image.new('RGB', (768, 1024), 'black').save(root / 'flow_art_attempt_1.png')
                with self.assertRaises(FlowCommandError) as failure:
                    self.frozen_image(client, {}, target)
                self.assertEqual(failure.exception.category, 'original_asset_changed')
                self.assertEqual(generate.call_count, 1)

    def test_unknown_submission_and_policy_do_not_use_api(self):
        from image_gen import ImageGenerator
        cases = [FlowCommandError('image', 'post_submit_error', submission_state='forwarded'),
                 FlowCommandError('image', 'pre_submit_terminal', error_class='SafetyRejectedError'),
                 FlowCommandError('image', 'pre_submit_terminal', error_class='InsufficientCreditsError'),
                 RuntimeError('unknown')]
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            'MEDIA_GENERATION_PROVIDER': 'flow', 'FLOW_API_FALLBACK_ENABLED': 'true'}, clear=True):
            for i, error in enumerate(cases):
                freeze(Path(tmp)/str(i), image={'scene': 'cloud'})
                with self.subTest(error=str(error)), patch.object(FlowMediaClient, '_run_json', side_effect=error) as flow, \
                        patch('google_image_client.GoogleImageClient.generate_from_json') as api:
                    with self.assertRaises(Exception):
                        ImageGenerator().generate({'scene': 'cloud'}, str(Path(tmp) / str(i) / 'art.png'))
                    flow.assert_called_once()
                    api.assert_not_called()

    def test_api_fallback_notification_failure_does_not_regenerate(self):
        def api_image(_self, _prompt, target):
            Image.new('RGB', (768, 1024), 'white').save(target)
            return target
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            'FLOW_API_FALLBACK_ENABLED': 'true', 'GOOGLE_API_KEY_PRIMARY': 'test'}, clear=True):
            target = Path(tmp) / 'art.png'
            client = FlowMediaClient()
            with patch('utils.get_pipeline_now', return_value=datetime(2026,10,27,12)), \
                    patch.object(client, '_run_json', side_effect=FlowCommandError('image', 'auth_required',
                    submission_state='not_attempted')) as flow, \
                    patch('google_image_client.GoogleImageClient.generate_from_json', autospec=True,
                          side_effect=api_image) as api, patch('notifier.Notifier.notify_status', side_effect=RuntimeError('offline')) as notify:
                self.frozen_image(client, {'scene': 'cloud'}, target)
                self.frozen_image(client, {'scene': 'cloud'}, target)
            self.assertEqual(flow.call_count, 1)
            self.assertEqual(api.call_count, 1)
            notify.assert_called()
            self.assertEqual(json.loads((Path(tmp) / 'flow_image_diagnostics.json').read_text())['provider'], 'google_api')

    def test_video_failure_limit_survives_second_invocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            art = root / 'art.png'
            Image.new('RGB', (768, 1024), 'white').save(art)
            def failed(command, timeout, marker):
                marker.write_text('submit_attempted\n')
                raise FlowCommandError('video', 'generation_failed', media_id='failed-id', retryable=True)
            with patch.object(FlowMediaClient, '_run_json', side_effect=failed) as generate, \
                    patch('flow_media_client.time.sleep'):
                for _ in range(2):
                    with self.assertRaises(FlowCommandError):
                        self.frozen_video(FlowMediaClient(), 'move', art, root / 'video.mp4')
            self.assertEqual(generate.call_count, 2)

    def test_corrupt_upscale_uses_valid_original(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'FLOW_VIDEO_UPSCALE_1080P': 'true',
                'FLOW_PREFLIGHT_PROJECT_ID': 'project'}, clear=True):
            root = Path(tmp)
            art = root / 'art.png'
            Image.new('RGB', (768, 1024), 'white').save(art)
            def generated(command, timeout, marker):
                (root / 'flow_raw_video.mp4').write_bytes(b'original')
                marker.write_text('submit_attempted\n')
                return {'succeeded': True, 'media_id': 'id', 'local_path': str(root / 'flow_raw_video.mp4'),
                        'request': {'count': 1, 'model': 'veo_3_1_fast', 'mode': 'i2v', 'aspect': 'portrait'}}
            def command(cmd, **kwargs):
                if 'flow_video_upscale.py' in str(cmd):
                    Path(cmd[4]).write_bytes(b'corrupt')
                    return subprocess.CompletedProcess(cmd, 0, stdout='{"status":"downloaded"}')
                Path(cmd[-1]).write_bytes(b'canonical')
                return subprocess.CompletedProcess(cmd, 0)
            def probe(path):
                if path.name == 'flow_1080p_download.mp4':
                    raise subprocess.CalledProcessError(1, ['ffprobe'])
                return (720, 1280, 8, True) if path.name == 'flow_raw_video.mp4' else (1080, 1920, 8, True)
            with patch.object(FlowMediaClient, '_run_json', side_effect=generated), \
                    patch.object(FlowMediaClient, '_probe_video', side_effect=probe), \
                    patch('flow_media_client.run_bounded', side_effect=command):
                self.frozen_video(FlowMediaClient(), 'move', art, root / 'video.mp4')
            report = json.loads((root / 'flow_video_diagnostics.json').read_text())
            self.assertTrue(report['locally_scaled_to_1080p'])
            self.assertEqual(report['upscale_status'], 'invalid_media')

    def test_image_upscale_is_off_by_default_and_request_model_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True), \
                patch.object(FlowMediaClient, '_run_json', side_effect=self.image_response), \
                patch('flow_media_client.run_bounded') as upscale:
            self.frozen_image(FlowMediaClient(), {'scene': 'cloud'}, Path(tmp) / 'art.png')
            upscale.assert_not_called()
            d = json.loads((Path(tmp) / 'flow_image_diagnostics.json').read_text())
            self.assertEqual(d['wire_model'], 'BELUGA')
            self.assertIsNone(d['response_model'])

    def test_installed_provenance_ignores_persistent_deployment_manifest(self):
        import flow_shadow_run
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stale = root / 'runtime/state/flow_shadow/deployment_version.json'
            stale.parent.mkdir(parents=True)
            stale.write_text('{"state_zero_commit":"stale"}')
            (root / '.build-provenance.json').write_text(json.dumps({
                'state_zero_commit': 'a' * 40, 'state_zero_source_sha256': 'b' * 64}))
            with patch.object(flow_shadow_run, 'ROOT', root), patch('flow_shadow_run.subprocess.run'):
                versions = flow_shadow_run._runtime_versions(root)
            self.assertEqual(versions['state_zero_commit'], 'a' * 40)
            self.assertEqual(versions['state_zero_source_sha256'], 'b' * 64)

    def test_catalog_local_file_cannot_bypass_missing_exact_context(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp); raw = root / 'recovered.jpg'
            Image.new('RGB', (768, 1024), 'white').save(raw)
            def failed(command, timeout, marker):
                marker.write_text(json.dumps({'state': 'forwarded', 'expected_model': 'NARWHAL',
                    'actual_model': 'BELUGA', 'selected_model': 'Nano Banana 2.1'}))
                raise FlowCommandError('image', 'post_submit_error', media_id='known', submission_state='forwarded')
            db = root / 'catalog.db'
            with sqlite3.connect(db) as c:
                c.executescript('CREATE TABLE assets (id TEXT, flow_media_id TEXT, profile_name TEXT, kind TEXT);'
                                'CREATE TABLE local_files (asset_id TEXT, path TEXT, sha256 TEXT);')
                c.execute('INSERT INTO assets VALUES (?,?,?,?)', ('asset', 'known', 'shadow', 'image'))
                c.execute('INSERT INTO local_files VALUES (?,?,?)', ('asset', str(raw), hashlib.sha256(raw.read_bytes()).hexdigest()))
            from types import SimpleNamespace
            config = SimpleNamespace(get_settings=lambda: SimpleNamespace(resolved_db_path=lambda: db))
            with patch.object(FlowMediaClient, '_run_json', side_effect=failed) as generate, \
                    patch.dict(sys.modules, {'gflow_cli.config': config}), \
                    patch.object(FlowMediaClient, '_readback_media') as download, \
                    patch('flow_media_client.GoogleImageClient') as api:
                with self.assertRaises(FlowCommandError):
                    self.frozen_image(FlowMediaClient(), {'scene': 'cloud'}, root / 'art.png')
            generate.assert_called_once(); download.assert_not_called(); api.assert_not_called()

    def test_api_submission_uncertainty_blocks_another_provider_call(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                'FLOW_API_FALLBACK_ENABLED': 'true', 'GOOGLE_API_KEY_PRIMARY': 'test'}, clear=True):
            client = FlowMediaClient()
            with patch('utils.get_pipeline_now', return_value=datetime(2026,10,27,12)), \
                    patch.object(client, '_run_json', side_effect=FlowCommandError('image', 'auth_required', submission_state='not_attempted')) as flow, \
                    patch('google_image_client.GoogleImageClient.generate_from_json', side_effect=RuntimeError('timeout')) as api:
                for _ in range(2):
                    with self.assertRaises(Exception):
                        self.frozen_image(client, {'scene': 'cloud'}, Path(tmp) / 'art.png')
            self.assertEqual(flow.call_count, 1)
            self.assertEqual(api.call_count, 1)

    def test_policy_detail_survives_a_later_invocation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                'FLOW_API_FALLBACK_ENABLED': 'true'}, clear=True):
            error = FlowCommandError('image', 'provider_rejected', error_class='ContentPolicyError', media_id='id',
                                     detail='Safety policy rejected this generation')
            with patch.object(FlowMediaClient, '_run_json', side_effect=error) as flow, \
                    patch('google_image_client.GoogleImageClient.generate_from_json') as api:
                for _ in range(2):
                    with self.assertRaises(FlowCommandError):
                        self.frozen_image(FlowMediaClient(), {}, Path(tmp) / 'art.png')
                api.assert_not_called()
                self.assertEqual(flow.call_count, 1)

    def test_policy_failure_reasons_override_retryable_error(self):
        for reason in ('SAFETY_REJECTION', 'PUBLIC_ERROR_UNSAFE_CONTENT', 'ACCOUNT_RESTRICTED'):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as tmp, \
                    patch.dict(os.environ, {'FLOW_API_FALLBACK_ENABLED': 'true', 'GOOGLE_API_KEY_PRIMARY': 'test'}, clear=True):
                result = subprocess.CompletedProcess([], 1, stdout=json.dumps({
                    'status': 'fail', 'failure_reasons': [reason],
                    'error': {'class': 'ServiceError', 'retryable': True, 'detail': 'Unavailable'}}))
                with patch('flow_media_client.run_bounded', return_value=result) as flow, \
                        patch('google_image_client.GoogleImageClient.generate_from_json') as api:
                    with self.assertRaises(FlowCommandError):
                        self.frozen_image(FlowMediaClient(), {}, Path(tmp) / 'art.png')
                    api.assert_not_called()
                    self.assertEqual(flow.call_count, 1)

    def test_saved_recovery_checks_existing_media_through_identity_guard(self):
        import flow_shadow_recover
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, value in {'daily_data.json': {}, 'card_metadata.json': {}, 'image_prompt.json': {}}.items():
                (root / name).write_text(json.dumps(value))
            (root / 'video_prompt.txt').write_text('move')
            (root / 'generated_art.png').write_bytes(b'existing')
            (root / 'generated_video.mp4').write_bytes(b'existing')
            pipeline = MagicMock(output_dir=root, post_to_instagram=False)
            pipeline.step_7_generate_image.side_effect = FlowCommandError('image', 'request_identity_unverified')
            with self.assertRaises(FlowCommandError):
                flow_shadow_recover.recover_media(pipeline)
            pipeline.step_7_generate_image.assert_called_once()
            pipeline.step_9_generate_video.assert_not_called()
            pipeline.step_15_archive.assert_not_called()

    def test_api_timeout_cannot_repeat_generation_inside_fallback(self):
        import requests
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                'FLOW_API_FALLBACK_ENABLED': 'true', 'GOOGLE_API_KEY_PRIMARY': 'test',
                'GOOGLE_API_KEY_FALLBACK': 'test-fallback', 'GOOGLE_API_FALLBACK_ENABLED': 'true'}, clear=True):
            with patch('utils.get_pipeline_now', return_value=datetime(2026,10,27,12)), \
                    patch.object(FlowMediaClient, '_run_json', side_effect=FlowCommandError('image', 'auth_required', submission_state='not_attempted')), \
                    patch('google_image_client.requests.post', side_effect=requests.Timeout('uncertain')) as submit:
                with self.assertRaises(requests.Timeout):
                    self.frozen_image(FlowMediaClient(), {}, Path(tmp) / 'art.png')
                self.assertEqual(submit.call_count, 1)

    def test_accepted_api_video_cannot_restart_on_poll_auth_failure(self):
        from google_video_client import GoogleVideoClient
        from google_key_router import GoogleAPIError
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                'GOOGLE_API_KEY_PRIMARY': 'test', 'GOOGLE_API_KEY_FALLBACK': 'test-fallback',
                'GOOGLE_API_FALLBACK_ENABLED': 'true'}, clear=True):
            root = Path(tmp)
            art = root / 'art.png'
            Image.new('RGB', (768, 1024), 'white').save(art)
            client = GoogleVideoClient()
            client.router.retry_ambiguous_calls = False
            accepted = Mock(status_code=200)
            accepted.json.return_value = {'name': 'operations/accepted'}
            denied = Mock(status_code=403, text='Poll authorization failed')
            with patch('google_video_client.requests.post', return_value=accepted) as submit, \
                    patch('google_video_client.requests.get', return_value=denied):
                with self.assertRaises(GoogleAPIError):
                    client.generate_from_image('move', art, root / 'video.mp4')
                self.assertEqual(submit.call_count, 1)

    def test_api_key_fallback_remains_available_before_video_acceptance(self):
        from google_video_client import GoogleVideoClient
        from google_key_router import GoogleAPIError
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                'GOOGLE_API_KEY_PRIMARY': 'test', 'GOOGLE_API_KEY_FALLBACK': 'test-fallback',
                'GOOGLE_API_FALLBACK_ENABLED': 'true'}, clear=True):
            root = Path(tmp)
            art = root / 'art.png'
            Image.new('RGB', (768, 1024), 'white').save(art)
            client = GoogleVideoClient()
            client.router.retry_ambiguous_calls = False
            denied = Mock(status_code=401, text='Invalid key')
            accepted = Mock(status_code=200)
            accepted.json.return_value = {'name': 'operations/accepted'}
            with patch('google_video_client.requests.post', side_effect=[denied, accepted]) as submit, \
                    patch('google_video_client.requests.get', return_value=denied):
                with self.assertRaises(GoogleAPIError):
                    client.generate_from_image('move', art, root / 'video.mp4')
                self.assertEqual(submit.call_count, 2)

    def test_verified_reuse_refreshes_timestamp_for_existing_pipeline_checks(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'FLOW_IMAGE_UPSCALE_2K': 'false'}):
            target = Path(tmp) / 'art.png'
            client = FlowMediaClient()
            with patch.object(client, '_run_json', side_effect=self.image_response) as generate:
                self.frozen_image(client, {}, target)
                os.utime(target, (1, 1))
                self.frozen_image(client, {}, target)
            self.assertGreater(target.stat().st_mtime, 1)
            self.assertEqual(generate.call_count, 1)

    def test_known_id_without_context_cannot_launch_recovery(self):
        import asyncio
        from ops.flow_media_recover import download_existing
        from flow_readback import ReadbackIdentityError
        with tempfile.TemporaryDirectory() as tmp, patch('gflow_cli.api.client.FlowApiClient') as browser:
            with self.assertRaises(ReadbackIdentityError):
                asyncio.run(download_existing('11111111-1111-4111-8111-111111111111',
                    '22222222-2222-4222-8222-222222222222', Path(tmp) / 'original.png'))
        browser.assert_not_called()

    def test_recovery_runner_accepts_known_result_but_not_uncertain_marker(self):
        from flow_shadow_recover import validate_recovery_inputs
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'flow_video_attempt_1.submit').write_text('submit_attempted\n')
            with self.assertRaises(ValueError):
                validate_recovery_inputs(root)
            (root / 'flow_video_diagnostics.json').write_text(json.dumps({
                'request_hash': 'verified-at-adapter', 'result': {'media_id': 'known-id'}}))
            validate_recovery_inputs(root)

"""Daily retry policy uses temporary state and mocked provider boundaries."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
from flow_media_client import FlowCommandError, FlowMediaClient


class DailyFlowPolicyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.env = patch.dict(os.environ, {'FLOW_API_FALLBACK_ENABLED': 'true'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        clock = patch.object(FlowMediaClient, '_generation_seconds', return_value=7200)
        clock.start()
        self.addCleanup(clock.stop)

    def freeze(self):
        for name in ('daily_data.json', 'whoop_snapshot.json', 'card_metadata.json',
                     'image_prompt.json', 'video_prompt.txt', 'blend_option.txt',
                     'creature_selected.txt', 'environment_selected.txt'):
            (self.root / name).write_text('{}' if name.endswith('.json') else 'original')
        FlowMediaClient.freeze_inputs(self.root)

    def save_image(self, request, output):
        Image.new('RGB', (768, 1024), 'white').save(output)
        return output

    def test_frozen_inputs_detect_changes_without_rewriting_manifest(self):
        self.freeze()
        manifest = self.root / 'flow_inputs.json'
        before = manifest.read_bytes()
        self.assertTrue(FlowMediaClient.validate_inputs(self.root))
        (self.root / 'video_prompt.txt').write_text('changed')
        with self.assertRaises(FlowCommandError):
            FlowMediaClient.validate_inputs(self.root)
        self.assertEqual(manifest.read_bytes(), before)

    def test_uncertain_flow_recovers_then_replaces_then_uses_one_api(self):
        self.freeze()
        target = self.root / 'generated_art.png'
        client = FlowMediaClient()
        calls = []
        def uncertain(request, output, recovered=None):
            calls.append('flow')
            raise FlowCommandError('image', 'submission_uncertain', submission_state='uncertain')
        api = Mock()
        api.generate_from_json.side_effect = self.save_image
        def recover(*args):
            calls.append('recover')
            return None
        with patch.object(client, '_generate_image', side_effect=uncertain), \
                patch.object(client, '_recover_pending', side_effect=recover, create=True), \
                patch('flow_media_client.GoogleImageClient', return_value=api):
            self.assertEqual(client.generate_image({}, target), target)
            self.assertEqual(calls, ['flow', 'recover', 'flow', 'recover'])
            client.generate_image({}, target)
        api.generate_from_json.assert_called_once()
        saved = json.loads((self.root / 'flow_image_diagnostics.json').read_text())
        self.assertEqual(len(saved['attempts']), 2)
        self.assertEqual(saved['provider'], 'google_api')

    def test_recovery_reuses_result_without_replacement(self):
        self.freeze()
        client = FlowMediaClient()
        target = self.root / 'generated_art.png'
        def generate(request, output, recovered=None):
            if recovered:
                self.save_image(request, output)
                return output
            raise FlowCommandError('image', 'submission_uncertain')
        with patch.object(client, '_generate_image', side_effect=generate) as generate, \
                patch.object(client, '_recover_pending', return_value={'media': 'known'}, create=True), \
                patch('flow_media_client.GoogleImageClient') as api:
            client.generate_image({}, target)
        self.assertEqual(generate.call_count, 2)
        api.assert_not_called()

    def test_pipeline_resumes_without_lookups_or_prompt_generation(self):
        from pipeline import WHOOPPipeline
        self.freeze()
        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.output_dir = self.root
        with patch.dict(os.environ, {'MEDIA_GENERATION_PROVIDER': 'flow'}):
            self.assertTrue(pipeline._check_flow_submission_state())

    def test_corrupt_marker_keeps_original_policy_verdict(self):
        diagnostic = self.root / 'flow_image_diagnostics.json'
        diagnostic.write_text(json.dumps({'failure': {'category': 'provider_rejected',
            'error_class': 'ContentPolicyError', 'detail': 'SAFETY rejected', 'operation_id': 'original'}}))
        (self.root / 'flow_image_attempt_1.submit').write_text('broken')
        error = FlowMediaClient._pending_submission(self.root)
        self.assertEqual(error.operation_id, 'original')
        self.assertIn('SAFETY', error.detail)

    def test_accepted_api_operation_resumes_without_starting_a_video(self):
        from google_video_client import GoogleVideoClient
        target = self.root / 'video.mp4'
        (self.root / 'google_video_diagnostics.json').write_text(json.dumps({
            'operation_name': 'models/veo-3.1-fast-generate-preview/operations/existing', 'key_label': 'primary'}))
        with patch.dict(os.environ, {'GOOGLE_API_KEY_PRIMARY': 'mock-key'}):
            client = GoogleVideoClient()
        done = Mock(status_code=200)
        done.json.return_value = {'done': True, 'response': {'generateVideoResponse': {
            'generatedSamples': [{'video': {'uri': 'https://example.invalid/video'}}]}}}
        with patch('google_video_client.requests.get', return_value=done), \
                patch('google_video_client.requests.post') as create, \
                patch.object(client, '_download_video', side_effect=lambda uri, key, path: path.write_bytes(b'video')):
            self.assertEqual(client.resume_operation(target), target)
        create.assert_not_called()

    def test_real_image_adapter_uses_distinct_markers_and_preserves_first_failure(self):
        self.freeze()
        target = self.root / 'generated_art.png'
        client = FlowMediaClient()
        markers = []
        def submit(command, timeout, marker):
            markers.append(marker.name)
            marker.write_text(json.dumps({'state': 'forwarded', 'selected_model': 'Nano Banana 2.1',
                                         'expected_model': 'NARWHAL', 'actual_model': 'BELUGA'}))
            if len(markers) == 1:
                raise FlowCommandError('image', 'submission_uncertain', detail='Original transport error',
                                       submission_state='forwarded', operation_id='original-operation')
            raw = Path(command[command.index('--output') + 1])
            self.save_image({}, raw)
            return {'count': 1, 'model': 'NARWHAL', 'images': [{'local_path': str(raw), 'media_name': 'second'}]}
        with patch.object(client, '_run_json', side_effect=submit), \
                patch.object(client, '_recover_pending', return_value=None):
            client.generate_image({}, target)
        self.assertEqual(markers, ['flow_image_attempt_1.submit', 'flow_image_attempt_2.submit'])
        saved = json.loads((self.root / 'flow_image_diagnostics.json').read_text())
        self.assertIn('Original transport error', saved['attempts'][0]['failure']['detail'])
        self.assertEqual(saved['provider'], 'flow')

    def test_attempt_limits_survive_api_timeout_and_a_new_client(self):
        import requests
        self.freeze()
        target = self.root / 'generated_art.png'
        api = Mock()
        api.generate_from_json.side_effect = requests.Timeout('uncertain API response')
        with patch.object(FlowMediaClient, '_generate_image', side_effect=FlowCommandError('image', 'submission_uncertain')) as flow, \
                patch.object(FlowMediaClient, '_recover_pending', return_value=None), \
                patch('flow_media_client.GoogleImageClient', return_value=api):
            with self.assertRaises(requests.Timeout):
                FlowMediaClient().generate_image({}, target)
            with self.assertRaises(FlowCommandError):
                FlowMediaClient().generate_image({}, target)
        self.assertEqual(flow.call_count, 2)
        api.generate_from_json.assert_called_once()

    def test_recorded_pending_reservation_consumes_a_submission_after_crash(self):
        self.freeze()
        target = self.root / 'generated_art.png'
        client = FlowMediaClient()
        with patch.object(client, '_generate_image', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                client.generate_image({}, target)
        api = Mock()
        api.generate_from_json.side_effect = self.save_image
        with patch.object(client, '_generate_image', side_effect=FlowCommandError('image', 'submission_uncertain')) as flow, \
                patch.object(client, '_recover_pending', return_value=None), \
                patch('flow_media_client.GoogleImageClient', return_value=api):
            client.generate_image({}, target)
        flow.assert_called_once()

    def test_recovery_deadline_is_five_minutes_and_does_not_restart(self):
        self.freeze()
        diagnostic = self.root / 'flow_image_diagnostics.json'
        diagnostic.write_text(json.dumps({'attempts': [{'number': 1}]}))
        client = FlowMediaClient()
        attempt = {'number': 1, 'catalog_position': 0}
        now = [100.0]
        with patch('flow_media_client.time.time', side_effect=lambda: now[0]), \
                patch('flow_media_client.time.sleep', side_effect=lambda seconds: now.__setitem__(0, now[0] + seconds)), \
                patch.object(client, '_notify_transition'), \
                patch.object(client, '_catalog_result', return_value=None) as lookup:
            self.assertIsNone(client._recover_pending('image', diagnostic, attempt, self.root / 'art.png'))
            self.assertEqual(now[0], 400)
            calls = lookup.call_count
            self.assertIsNone(client._recover_pending('image', diagnostic, attempt, self.root / 'art.png'))
            self.assertEqual(lookup.call_count, calls)
        saved = json.loads(diagnostic.read_text())
        self.assertEqual(saved['attempts'][0]['recovery_deadline'], 400)

    def test_short_window_skips_flow_for_api_then_stops_all_generation(self):
        self.freeze()
        api = Mock()
        api.generate_from_json.side_effect = self.save_image
        with patch.object(FlowMediaClient, '_generation_seconds', return_value=200), \
                patch.object(FlowMediaClient, '_generate_image') as flow, \
                patch('flow_media_client.GoogleImageClient', return_value=api):
            FlowMediaClient().generate_image({}, self.root / 'generated_art.png')
        flow.assert_not_called()
        api.generate_from_json.assert_called_once()
        (self.root / 'generated_art.png').unlink()
        with patch.object(FlowMediaClient, '_generation_seconds', return_value=0), \
                patch('flow_media_client.GoogleImageClient') as create:
            with self.assertRaises(FlowCommandError):
                FlowMediaClient().generate_image({}, self.root / 'generated_art.png')
        create.assert_not_called()

    def test_policy_rejection_does_not_replace_or_call_api(self):
        self.freeze()
        with patch.object(FlowMediaClient, '_generate_image', side_effect=FlowCommandError(
                'image', 'provider_rejected', detail='SAFETY policy rejection')) as flow, \
                patch.object(FlowMediaClient, '_recover_pending') as recover, \
                patch('flow_media_client.GoogleImageClient') as api:
            with self.assertRaises(FlowCommandError):
                FlowMediaClient().generate_image({}, self.root / 'generated_art.png')
        flow.assert_called_once()
        recover.assert_not_called()
        api.assert_not_called()

    def test_confirmed_flow_auth_failure_goes_to_api_without_second_flow(self):
        self.freeze()
        api = Mock()
        api.generate_from_json.side_effect = self.save_image
        with patch.object(FlowMediaClient, '_generate_image', side_effect=FlowCommandError(
                'image', 'auth_required', submission_state='not_attempted')) as flow, \
                patch('flow_media_client.GoogleImageClient', return_value=api):
            FlowMediaClient().generate_image({}, self.root / 'generated_art.png')
        flow.assert_called_once()
        api.generate_from_json.assert_called_once()

    def test_wrong_request_cannot_use_an_unrelated_frozen_manifest(self):
        self.freeze()
        with patch.object(FlowMediaClient, '_generate_image') as flow:
            with self.assertRaises(FlowCommandError):
                FlowMediaClient().generate_image({'different': 'prompt'}, self.root / 'generated_art.png')
        flow.assert_not_called()

    def test_resume_path_skips_lookups_and_prompts_before_media(self):
        from pipeline import WHOOPPipeline, PipelineStageError
        self.freeze()
        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.output_dir = self.root
        pipeline.mode, pipeline.post_to_instagram = 'automatic', False
        pipeline.media_mode, pipeline.run_token = 'local_test', 'test'
        with patch.dict(os.environ, {'MEDIA_GENERATION_PROVIDER': 'flow'}), \
                patch.object(pipeline, '_claim_daily_run_or_exit'), \
                patch.object(pipeline, '_ensure_owner_runtime_dirs'), \
                patch.object(pipeline, '_stop_heartbeat_thread'), \
                patch.object(pipeline, '_cleanup_non_authoritative_daily_state'), \
                patch('pipeline._setup_global_exception_handler'), \
                patch.object(pipeline, '_handle_runtime_stage_error', return_value=True), \
                patch.object(pipeline, 'step_1_validate'), \
                patch.object(pipeline, '_revalidate_whoop_checkpoint'), \
                patch.object(pipeline, 'step_2_3_lookups') as lookups, \
                patch.object(pipeline, 'step_4_6_prompts') as prompts, \
                patch.object(pipeline, 'step_7_generate_image', side_effect=PipelineStageError('stop', 'test stop')) as image:
            pipeline.run()
        lookups.assert_not_called()
        prompts.assert_not_called()
        image.assert_called_once()

    def test_attempt_failure_verdict_survives_crash_before_summary_write(self):
        self.freeze()
        target = self.root / 'generated_art.png'
        client = FlowMediaClient()
        def policy(*args):
            raise FlowCommandError('image', 'provider_rejected', detail='SAFETY rejection')
        original = client._write_diagnostics
        def crash(path, data):
            if set(data) == {'failure'}:
                raise KeyboardInterrupt
            return original(path, data)
        with patch.object(client, '_generate_image', side_effect=policy), \
                patch.object(client, '_write_diagnostics', side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):
                client.generate_image({}, target)
        with patch.object(client, '_generate_image') as flow, \
                patch.object(client, '_recover_pending') as recover, \
                patch('flow_media_client.GoogleImageClient') as api:
            with self.assertRaises(FlowCommandError):
                client.generate_image({}, target)
        flow.assert_not_called()
        recover.assert_not_called()
        api.assert_not_called()

    def test_provider_failure_reasons_block_even_when_generic_error_is_retryable(self):
        import subprocess
        for reasons in (['SAFETY'], ['RECAPTCHA_FAILED'], ['QUOTA', 'REFUSAL']):
            with self.subTest(reasons=reasons):
                payload = {'status': 'error', 'failure_reasons': reasons,
                           'error': {'class': 'ServiceError', 'detail': 'Unavailable', 'retryable': True}}
                with patch('flow_media_client.subprocess.run', return_value=subprocess.CompletedProcess(
                        ['gflow'], 1, json.dumps(payload), '')):
                    with self.assertRaises(FlowCommandError) as caught:
                        FlowMediaClient._run_json(['gflow', 'image', 'test'], 10)
                self.assertTrue(FlowMediaClient._stops_replacement(caught.exception))
                self.assertIn('provider reason', caught.exception.detail)

    def test_one_pre_submit_retry_then_api_without_billable_flow_submissions(self):
        self.freeze()
        api = Mock()
        api.generate_from_json.side_effect = self.save_image
        with patch.object(FlowMediaClient, '_generate_image', side_effect=FlowCommandError(
                'image', 'pre_submit_timeout', submission_state='not_attempted')) as flow, \
                patch.object(FlowMediaClient, '_recover_pending') as recover, \
                patch('flow_media_client.GoogleImageClient', return_value=api):
            FlowMediaClient().generate_image({}, self.root / 'generated_art.png')
        self.assertEqual(flow.call_count, 2)
        recover.assert_not_called()
        saved = json.loads((self.root / 'flow_image_diagnostics.json').read_text())
        self.assertTrue(all(not a['submit_attempted'] for a in saved['attempts']))

    def test_marker_validation_refusal_survives_lost_cli_error_response(self):
        self.freeze()
        def blocked(*args):
            (self.root / 'flow_image_attempt_1.submit').write_text(json.dumps({
                'state': 'blocked_before_submission', 'reason': 'Requested model differs'}))
            raise FlowCommandError('image', 'pre_submit_timeout', submission_state='not_attempted')
        with patch.object(FlowMediaClient, '_generate_image', side_effect=blocked) as flow, \
                patch('flow_media_client.GoogleImageClient') as api:
            with self.assertRaises(FlowCommandError):
                FlowMediaClient().generate_image({}, self.root / 'generated_art.png')
        flow.assert_called_once()
        api.assert_not_called()

    def test_video_budget_and_start_frame_identity_survive_later_invocation(self):
        self.freeze()
        art = self.save_image({}, self.root / 'generated_art.png')
        target = self.root / 'generated_video.mp4'
        before = art.read_bytes()
        api = Mock()
        api.generate_from_image.side_effect = lambda prompt, image, path: path.write_bytes(b'validated video')
        with patch.object(FlowMediaClient, '_generate_video', side_effect=FlowCommandError('video', 'submission_uncertain')) as flow, \
                patch.object(FlowMediaClient, '_recover_pending', return_value=None), \
                patch.object(FlowMediaClient, '_probe_video', return_value=(1080, 1920, 8, True)), \
                patch('flow_media_client.GoogleVideoClient', return_value=api):
            FlowMediaClient().generate_video('original', art, target)
            FlowMediaClient().generate_video('original', art, target)
            Image.new('RGB', (768, 1024), 'red').save(art)
            with self.assertRaises(FlowCommandError):
                FlowMediaClient().generate_video('original', art, target)
        self.assertEqual(flow.call_count, 2)
        api.generate_from_image.assert_called_once()
        self.assertNotEqual(art.read_bytes(), before)
        self.assertEqual((self.root / 'video_prompt.txt').read_text(), 'original')

    def test_unknown_catalog_operation_cannot_recover_some_other_project_media(self):
        self.assertIsNone(FlowMediaClient()._catalog_result('image', {'number': 1, 'catalog_position': 0}))

    def test_selected_api_bytes_survive_crash_during_atomic_promotion(self):
        self.freeze()
        target = self.root / 'generated_art.png'
        candidate = self.save_image({}, self.root / '.generated_art.api.png')
        client = FlowMediaClient()
        with patch.object(client, '_generate_image', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                client.generate_image({}, target)
        diagnostic = self.root / 'flow_image_diagnostics.json'
        replace = Path.replace
        def fail_promotion(path, destination):
            if path == candidate:
                raise KeyboardInterrupt
            return replace(path, destination)
        with patch.object(Path, 'replace', autospec=True, side_effect=fail_promotion):
            with self.assertRaises(KeyboardInterrupt):
                client._finish_daily('image', target, diagnostic, 'google_api', candidate)
        saved = json.loads(diagnostic.read_text())
        self.assertEqual(saved['selected_sha256'], hashlib.sha256(candidate.read_bytes()).hexdigest())
        with patch.object(client, '_generate_image') as flow, patch('flow_media_client.GoogleImageClient') as api:
            client.generate_image({}, target)
        flow.assert_not_called()
        api.assert_not_called()
        self.assertTrue(target.is_file())

    def test_emergency_rescue_survives_notification_exception(self):
        from pipeline import WHOOPPipeline, PipelineStageError
        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.run_date = '2026-10-17'
        pipeline.post_to_instagram, pipeline.in_emergency_fallback = True, False
        notifier = Mock()
        notifier.notify_warning.side_effect = RuntimeError('Telegram unavailable')
        with patch.dict(os.environ, {'EMERGENCY_FALLBACK_ENABLED': 'true'}), \
                patch.object(pipeline, '_is_terminal_rescue_run', return_value=True), \
                patch.object(pipeline, '_run_emergency_fallback', return_value=True) as rescue, \
                patch('pipeline.get_notifier', return_value=notifier):
            self.assertTrue(pipeline._handle_runtime_stage_error(PipelineStageError('Flow', 'failed', fallback_eligible=True)))
        rescue.assert_called_once()

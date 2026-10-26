"""Daily retry policy uses temporary state and mocked provider boundaries."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
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
        morning=patch('utils.get_pipeline_now',return_value=datetime(2026,10,23,10))
        morning.start();self.addCleanup(morning.stop)

    def freeze(self):
        for name in ('daily_data.json', 'whoop_snapshot.json', 'card_metadata.json',
                     'image_prompt.json', 'video_prompt.txt', 'blend_option.txt',
                     'creature_selected.txt', 'environment_selected.txt'):
            (self.root / name).write_text('{}' if name.endswith('.json') else 'original')
        FlowMediaClient.freeze_inputs(self.root)

    def save_image(self, request, output):
        Image.new('RGB', (768, 1024), 'white').save(output)
        return output

    def image_marker(self, number=1):
        marker = self.root / f'flow_image_attempt_{number}.submit'
        marker.with_suffix('.context.json').write_text('{}')
        marker.write_text(json.dumps({'state': 'forwarded', 'selected_model': 'Nano Banana 2.1',
                                     'expected_model': 'NARWHAL', 'actual_model': 'BELUGA'}))
        return marker

    def test_corrupt_image_recovers_exact_original_without_another_submission(self):
        self.freeze()
        client = FlowMediaClient()
        recovered = self.save_image({}, self.root / 'recovered.png')
        def submit(command, timeout, marker):
            self.image_marker()
            raw = Path(command[command.index('--output') + 1])
            raw.write_bytes(b'corrupt download')
            return {'count': 1, 'model': 'NARWHAL', 'images': [{'media_name': 'exact', 'local_path': str(raw)}]}
        with patch.object(client, '_run_json', side_effect=submit) as submit_mock, \
                patch.object(client, '_readback_media', return_value={'count': 1, 'model': 'NARWHAL', 'images': [{'media_name': 'exact', 'local_path': str(recovered)}]}) as recover, \
                patch('flow_media_client.GoogleImageClient') as api:
            client.generate_image({}, self.root / 'generated_art.png')
        submit_mock.assert_called_once()
        recover.assert_called_once()
        self.assertEqual(recover.call_args.kwargs['known']['images'][0]['media_name'], 'exact')
        api.assert_not_called()
        saved = json.loads((self.root / 'flow_image_diagnostics.json').read_text())
        self.assertEqual(saved['attempts'][0]['failure']['category'], 'asset_download_failed')
        self.assertIsNone(saved['failure'])

    def test_invalid_retrieval_never_proves_generation_failure(self):
        self.freeze()
        client = FlowMediaClient()
        def submit(command, timeout, marker):
            self.image_marker()
            raw = Path(command[command.index('--output') + 1]); raw.write_bytes(b'corrupt')
            return {'count': 1, 'model': 'NARWHAL', 'images': [{'media_name': 'exact', 'local_path': str(raw)}]}
        with patch.object(client, '_run_json', side_effect=submit) as calls, \
                patch.object(client, '_readback_media', side_effect=FlowCommandError('image', 'asset_download_failed')), \
                patch('flow_media_client.GoogleImageClient') as api:
            with self.assertRaises(FlowCommandError) as error:
                client.generate_image({}, self.root / 'generated_art.png')
        self.assertEqual(error.exception.category, 'asset_download_failed')
        calls.assert_called_once(); api.assert_not_called()
        self.assertEqual(len(json.loads((self.root / 'flow_image_diagnostics.json').read_text())['attempts']), 1)

    def test_corrupt_video_recovers_without_resubmission_or_upscale(self):
        import subprocess
        self.freeze()
        client = FlowMediaClient()
        art = self.save_image({}, self.root / 'generated_art.png')
        recovered = self.root / 'recovered.mp4'
        recovered.write_bytes(b'valid recovered video')
        def submit(command, timeout, marker):
            marker.with_suffix('.context.json').write_text('{}')
            marker.write_text('submit_attempted\n')
            raw = Path(command[command.index('--output') + 1])
            raw.write_bytes(b'corrupt')
            return {'succeeded': True, 'media_id': 'exact-video', 'local_path': str(raw),
                    'request': {'count': 1, 'model': 'veo_3_1_fast', 'mode': 'i2v', 'aspect': 'portrait'}}
        def probe(path):
            if path.read_bytes() == b'corrupt':
                raise subprocess.CalledProcessError(1, 'ffprobe')
            return 1080, 1920, 8, True
        with patch.object(client, '_run_json', side_effect=submit) as submit_mock, \
                patch.object(client, '_probe_video', side_effect=probe), \
                patch.object(client, '_readback_media', return_value={'succeeded': True, 'media_id': 'exact-video', 'local_path': str(recovered), 'request': {'count': 1, 'model': 'veo_3_1_fast', 'mode': 'i2v', 'aspect': 'portrait'}}) as recover, \
                patch('flow_media_client.run_bounded') as upscale, \
                patch('flow_media_client.GoogleVideoClient.generate_from_image') as api:
            client.generate_video('original', art, self.root / 'generated_video.mp4')
        submit_mock.assert_called_once()
        recover.assert_called_once()
        self.assertEqual(recover.call_args.kwargs['known']['media_id'], 'exact-video')
        upscale.assert_not_called()
        api.assert_not_called()

    def test_video_reply_without_download_recovers_same_result(self):
        self.freeze()
        client = FlowMediaClient()
        art = self.save_image({}, self.root / 'generated_art.png')
        recovered = self.root / 'recovered.mp4'; recovered.write_bytes(b'original video')
        result = {'succeeded': True, 'media_id': 'exact-video',
                  'request': {'count': 1, 'model': 'veo_3_1_fast', 'mode': 'i2v', 'aspect': 'portrait'}}
        def submit(command, timeout, marker):
            marker.write_text('submit_attempted\n')
            marker.with_suffix('.context.json').write_text('{}')
            return result
        with patch.object(client, '_run_json', side_effect=submit) as generate, \
                patch.object(client, '_readback_media', return_value=result | {'local_path': str(recovered)}) as read, \
                patch.object(client, '_probe_video', return_value=(1080, 1920, 8, True)), \
                patch('flow_media_client.run_bounded') as upscale, \
                patch('flow_media_client.GoogleVideoClient.generate_from_image') as api:
            output = client.generate_video('original', art, self.root / 'generated_video.mp4')
        self.assertEqual(output.read_bytes(), b'original video')
        generate.assert_called_once(); read.assert_called_once()
        upscale.assert_not_called(); api.assert_not_called()
        self.assertEqual(read.call_args.kwargs['known']['media_id'], 'exact-video')

    def test_bad_markers_cannot_enable_replacement_and_keep_original_context(self):
        for raw in ('broken', '[]', '{"state":"unknown"}', '{"state":"attempted"}',
                    '{"state":"forwarded"}', '{"state":"blocked_before_submission"}'):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as directory:
                original = self.root
                self.root = Path(directory)
                self.freeze()
                def submit(*args):
                    (self.root / 'flow_image_attempt_1.submit').write_text(raw)
                    raise FlowCommandError('image', 'post_submit_error', error_class='NetworkError',
                        detail='Original transport error', operation_id='original', submission_state='forwarded')
                with patch.object(FlowMediaClient, '_generate_image', side_effect=submit) as flow, \
                        patch.object(FlowMediaClient, '_recover_pending') as recover, \
                        patch('flow_media_client.GoogleImageClient') as api:
                    with self.assertRaises(FlowCommandError) as error:
                        FlowMediaClient().generate_image({}, self.root / 'generated_art.png')
                self.assertEqual(error.exception.category, 'request_identity_unverified')
                self.assertEqual(error.exception.operation_id, 'original')
                self.assertIn('Original transport error', error.exception.detail)
                flow.assert_called_once()
                recover.assert_not_called()
                api.assert_not_called()
                self.assertEqual((self.root / 'flow_image_attempt_1.submit').read_text(), raw)
                self.root = original

    def test_original_checksum_violation_blocks_recovery_replacement_and_api(self):
        self.freeze()
        with patch.object(FlowMediaClient, '_generate_image', side_effect=FlowCommandError(
                'image', 'original_asset_changed')) as flow, \
                patch.object(FlowMediaClient, '_recover_pending') as recover, \
                patch('flow_media_client.GoogleImageClient') as api:
            with self.assertRaises(FlowCommandError):
                FlowMediaClient().generate_image({}, self.root / 'generated_art.png')
        flow.assert_called_once()
        recover.assert_not_called()
        api.assert_not_called()

    def test_empty_reservation_and_gaps_in_attempt_history_fail_closed(self):
        diagnostic = self.root / 'flow_image_diagnostics.json'
        for record in ({'pending_attempt': {}}, {'attempts': [{'number': 2, 'submit_attempted': True}]}):
            diagnostic.write_text(json.dumps(record))
            with self.subTest(record=record), self.assertRaises(FlowCommandError) as error:
                FlowMediaClient._daily_attempts('image', diagnostic)
            self.assertEqual(error.exception.category, 'request_identity_unverified')

    def test_receipt_requires_exact_request_profile_project_and_settings(self):
        diagnostic = self.root / 'flow_image_diagnostics.json'
        diagnostic.write_text(json.dumps({'record_version':2, 'request_hash': 'original'}))
        self.image_marker()
        receipt = self.root / 'flow_image_attempt_1.result.json'
        result = {'request_hash': 'original', 'profile': 'shadow', 'project_id': '', 'count': 1,
                  'model': 'NARWHAL', 'images': [{'media_name': 'exact', 'model_name_type': None,
                  'aspect_ratio': 'IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR'}]}
        receipt.write_text(json.dumps(result))
        self.assertEqual(FlowMediaClient()._image_receipt(diagnostic, {'number': 1})['images'][0]['media_name'], 'exact')
        for key, wrong in (('request_hash', 'changed'), ('profile', 'other'), ('project_id', 'other'), ('count', 2)):
            receipt.write_text(json.dumps(result | {key: wrong}))
            with self.subTest(key=key), self.assertRaises(FlowCommandError):
                FlowMediaClient()._image_receipt(diagnostic, {'number': 1})

    def test_image_receipt_survives_crash_and_recovers_without_a_new_submission(self):
        self.freeze()
        client = FlowMediaClient()
        target = self.root / 'generated_art.png'
        recovered = self.save_image({}, self.root / 'recovered.png')
        def crash(command, timeout, marker):
            self.image_marker()
            request_hash = json.loads((self.root / 'flow_image_diagnostics.json').read_text())['request_hash']
            marker.with_suffix('.result.json').write_text(json.dumps({
                'request_hash': request_hash, 'profile': 'shadow', 'project_id': '',
                'count': 1, 'model': 'NARWHAL', 'images': [{'media_name': 'exact', 'model_name_type': None,
                'aspect_ratio': 'IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR'}]}))
            raise KeyboardInterrupt
        with patch.object(client, '_run_json', side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):
                client.generate_image({}, target)
        with patch.object(FlowMediaClient, '_run_json') as generate, \
                patch.object(FlowMediaClient, '_readback_media', return_value={'count': 1, 'model': 'NARWHAL', 'images': [{'media_name': 'exact', 'local_path': str(recovered)}]}) as recover, \
                patch('flow_media_client.GoogleImageClient') as api:
            FlowMediaClient().generate_image({}, target)
        generate.assert_not_called()
        recover.assert_called_once()
        self.assertEqual(recover.call_args.kwargs['known']['images'][0]['media_name'], 'exact')
        api.assert_not_called()

    def test_corrupt_image_upscale_keeps_the_valid_original(self):
        import subprocess
        client = FlowMediaClient()
        def submit(command, timeout, marker):
            self.image_marker()
            raw = self.save_image({}, Path(command[command.index('--output') + 1]))
            return {'count': 1, 'model': 'NARWHAL', 'project_id': 'project',
                    'images': [{'media_name': 'exact', 'local_path': str(raw)}]}
        def upscale(command, **kwargs):
            folder = Path(command[command.index('--out') + 1])
            (folder / 'exact_2k.png').write_bytes(b'corrupt upscale')
            return subprocess.CompletedProcess(command, 0)
        with patch.dict(os.environ, {'FLOW_IMAGE_UPSCALE_2K': 'true'}):
            self.freeze()
            with patch.object(client, '_run_json', side_effect=submit), \
                    patch('flow_media_client.run_bounded', side_effect=upscale):
                client.generate_image({}, self.root / 'generated_art.png')
        saved = json.loads((self.root / 'flow_image_diagnostics.json').read_text())
        self.assertFalse(saved['flow_2k_upscaled'])

    def test_telegram_flow_handoff_preserves_earlier_deadline_and_api_default(self):
        from datetime import datetime
        from pipeline import WHOOPPipeline
        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.deadline_dt = datetime(2026, 10, 17, 14)
        with patch.dict(os.environ, {'MEDIA_GENERATION_PROVIDER': 'flow'}):
            self.assertEqual(pipeline._get_session_deadline({}).strftime('%H:%M'), '12:55')
            self.assertEqual(pipeline._get_session_deadline({'deadline_local': '2026-10-17T12:00:00'}).hour, 12)
        self.assertEqual(pipeline._get_session_deadline({}).hour, 14)

    def test_telegram_image_only_is_reused_for_flow_video(self):
        from pipeline import WHOOPPipeline
        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.output_dir = self.root
        session = {'telegram': {'image_file_id': 'image'}}
        video = self.root / 'generated_video.mp4'
        with patch.object(pipeline, '_download_telegram_file', side_effect=lambda file_id, path: self.save_image({}, path.with_suffix('.png')).replace(path)), \
                patch.object(pipeline, '_validate_image_file'), \
                patch.object(pipeline, 'step_7_generate_image') as image, \
                patch.object(pipeline, 'step_9_generate_video', return_value=video) as generate_video:
            art, result = pipeline._manual_fallback_media({}, session)
        image.assert_not_called()
        generate_video.assert_called_once_with(art, self.root / 'video_prompt.txt')
        self.assertTrue(art.is_file())
        self.assertEqual(result, video)

    def test_frozen_inputs_detect_changes_without_rewriting_manifest(self):
        self.freeze()
        manifest = self.root / 'flow_inputs.json'
        before = manifest.read_bytes()
        self.assertTrue(FlowMediaClient.validate_inputs(self.root))
        (self.root / 'video_prompt.txt').write_text('changed')
        with self.assertRaises(FlowCommandError):
            FlowMediaClient.validate_inputs(self.root)
        self.assertEqual(manifest.read_bytes(), before)

    def test_confirmed_failed_flow_uses_two_submits_then_one_api(self):
        self.freeze()
        target = self.root / 'generated_art.png'
        client = FlowMediaClient()
        calls = []
        def uncertain(request, output, recovered=None):
            calls.append('flow')
            raise FlowCommandError('image', 'generation_failed', retryable=True, submission_state='uncertain')
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
        diagnostic.write_text(json.dumps({'record_version':2, 'failure': {'category': 'provider_rejected',
            'error_class': 'ContentPolicyError', 'detail': 'SAFETY rejected', 'operation_id': 'original'}}))
        (self.root / 'flow_image_attempt_1.submit').write_text('broken')
        error = FlowMediaClient._pending_submission(self.root)
        self.assertEqual(error.operation_id, 'original')
        self.assertIn('SAFETY', error.detail)

    def test_accepted_api_operation_resumes_without_starting_a_video(self):
        from google_video_client import GoogleVideoClient
        target = self.root / 'video.mp4'
        (self.root / 'google_video_diagnostics.json').write_text(json.dumps({'record_version':2,
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
            marker.with_suffix('.context.json').write_text('{}')
            marker.write_text(json.dumps({'state': 'forwarded', 'selected_model': 'Nano Banana 2.1',
                                         'expected_model': 'NARWHAL', 'actual_model': 'BELUGA'}))
            if len(markers) == 1:
                raise FlowCommandError('image', 'generation_failed', retryable=True, detail='Original transport error',
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
        with patch.object(FlowMediaClient, '_generate_image', side_effect=FlowCommandError('image', 'generation_failed', retryable=True)) as flow, \
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
            with self.assertRaises(FlowCommandError) as error:
                client.generate_image({}, target)
        self.assertEqual(error.exception.category,'request_identity_unverified')
        flow.assert_not_called()
        api.generate_from_json.assert_not_called()

    def test_recovery_deadline_is_five_minutes_and_does_not_restart(self):
        self.freeze()
        diagnostic = self.root / 'flow_image_diagnostics.json'
        diagnostic.write_text(json.dumps({'record_version':2, 'request_hash': 'original', 'attempts': [{'number': 1}]}))
        (self.root / 'flow_image_attempt_1.context.json').write_text('{}')
        client = FlowMediaClient(); attempt = {'number': 1}; now = [100.0]
        def read(*args, **kwargs):
            self.assertEqual(args[3], 400)
            now[0] = 400
        with patch('flow_media_client.time.time', side_effect=lambda: now[0]), \
                patch.object(client, '_readback_media', side_effect=read) as lookup, \
                patch.object(client, '_notify_transition'):
            for _ in range(2):
                self.assertIsNone(client._recover_pending('image', diagnostic, attempt, self.root / 'art.png'))
            self.assertEqual(lookup.call_count, 2)
        self.assertEqual(json.loads(diagnostic.read_text())['attempts'][0]['recovery_deadline'], 400)

    def test_existing_exact_result_precedes_project_readback(self):
        self.freeze()
        target = self.root / 'art.png'
        self.save_image({}, target)
        result = {'images': [{'media_name': 'exact', 'local_path': str(target)}]}
        diagnostic = self.root / 'flow_image_diagnostics.json'
        diagnostic.write_text(json.dumps({'record_version':2, 'attempts': [{'number': 1}], 'result': result}))
        (self.root / 'flow_image_attempt_1.context.json').write_text('{}')
        client = FlowMediaClient()
        with patch.object(client, '_notify_transition'), patch.object(client, '_readback_media') as read:
            self.assertEqual(client._recover_pending('image', diagnostic, {'number': 1}, target), result)
            read.assert_not_called()

    def saved_local_work(self, stage, *, exhausted=False, receipt_only=False):
        root = Path(tempfile.mkdtemp(dir=self.root))
        for name in FlowMediaClient._input_names(root):
            (root / name).write_text('{}' if name.endswith('.json') else 'original')
        FlowMediaClient.freeze_inputs(root)
        image = self.save_image({}, root / 'input_art.png')
        asset = root / ('recovered.png' if stage == 'image' else 'recovered.mp4')
        if stage == 'image':
            self.save_image({}, asset)
        else:
            asset.write_bytes(b'validated-video')
        request = {} if stage == 'image' else {
            'prompt': 'original', 'start_frame_sha256': hashlib.sha256(image.read_bytes()).hexdigest()}
        identity = {'request': request, 'profile': 'shadow', 'project': '',
                    'settings': 'Nano Banana 2.1/3:4/x1' if stage == 'image' else 'veo-fast/i2v/9:16/x1/8s'}
        request_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        result = ({'count': 1, 'model': 'NARWHAL', 'images': [{'media_name': 'exact',
                   'local_path': str(asset), 'model_name_type': 'BELUGA',
                   'aspect_ratio': 'IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR'}]}
                  if stage == 'image' else {'succeeded': True, 'media_id': '22222222-2222-4222-8222-222222222222', 'local_path': str(asset),
                   'request': {'count': 1, 'model': 'veo_3_1_fast', 'mode': 'i2v', 'aspect': 'portrait'}})
        attempt = {'number': 1, 'failure': {'category': 'post_submit_error'}, 'recovery_deadline': 1}
        diagnostic = root / f'flow_{stage}_diagnostics.json'
        diagnostic.write_text(json.dumps({'record_version':2, 'request_hash': request_hash, 'daily_policy': True,
            'attempts': [attempt], 'result': None if receipt_only else {k:v for k,v in result.items() if k!='succeeded'},
            'original_sha256': hashlib.sha256(asset.read_bytes()).hexdigest()}))
        (root / f'flow_{stage}_attempt_1.submit').write_text(
            json.dumps({'state': 'forwarded', 'selected_model': 'Nano Banana 2.1',
                        'expected_model': 'NARWHAL', 'actual_model': 'BELUGA'})
            if stage == 'image' else 'submit_attempted\n')
        if stage == 'video':
            from flow_fixture import video_receipt
            video_receipt(root, request_hash, result['media_id'])
        if exhausted:
            context = root / f'flow_{stage}_attempt_1.context.json'
            if not context.exists(): context.write_text('{}')
            saved = json.loads(diagnostic.read_text())
            saved['retrieval'] = {'version':1, 'context_file':context.name, 'attempts':3, 'deadline':1,
                'candidate': {'kind':stage, 'context_sha256':hashlib.sha256(context.read_bytes()).hexdigest(), 'result':{}}}
            diagnostic.write_text(json.dumps(saved))
        if receipt_only:
            (root / 'flow_image_attempt_1.result.json').write_text(json.dumps(result | {
                'request_hash': request_hash, 'profile': 'shadow', 'project_id': ''}))
        return root, diagnostic, attempt, result, asset

    def test_expired_and_exhausted_recovery_reuses_saved_image_and_video(self):
        from contextlib import ExitStack
        import shutil
        for stage, receipt_only in (('image', False), ('image', True), ('video', False)):
            for exhausted in (False, True):
                with self.subTest(stage=stage, receipt=receipt_only, exhausted=exhausted), ExitStack() as stack:
                    root, diagnostic, attempt, result, asset = self.saved_local_work(
                        stage, exhausted=exhausted, receipt_only=receipt_only)
                    target = root / ('generated_art.png' if stage == 'image' else 'generated_video.mp4')
                    frozen = (root / 'flow_inputs.json').read_bytes()
                    client = FlowMediaClient()
                    for name in ('_run_json', '_readback_media', '_recovery_command'):
                        stack.enter_context(patch.object(client, name, side_effect=AssertionError('Remote work forbidden')))
                    stack.enter_context(patch.object(client, '_notify_transition'))
                    stack.enter_context(patch.object(client, '_probe_video', return_value=(1080, 1920, 8, True)))
                    for name in ('GoogleImageClient', 'GoogleVideoClient'):
                        stack.enter_context(patch('flow_media_client.' + name, side_effect=AssertionError('API forbidden')))
                    def finish(*args):
                        self.assertIsNotNone(args[-1], 'A new submission was selected instead of saved work')
                        for key, value in result.items():
                            self.assertEqual(args[-1][key], value)
                        shutil.copyfile(asset, target)
                    generate = stack.enter_context(patch.object(client, '_generate_' + stage, side_effect=finish))
                    if stage == 'image':
                        client.generate_image({}, target)
                    else:
                        client.generate_video('original', root / 'input_art.png', target)
                    generate.assert_called_once()
                    self.assertEqual(target.read_bytes(), asset.read_bytes())
                    self.assertEqual((root / 'flow_inputs.json').read_bytes(), frozen)
                    self.assertEqual(json.loads(diagnostic.read_text())['attempts'], [attempt])
                    self.assertEqual(len(list(root.glob('*.submit'))), 1)

    def test_expired_recovery_does_not_read_remote_for_missing_or_damaged_files(self):
        for state in ('missing', 'damaged'):
            with self.subTest(state=state):
                root, diagnostic, attempt, result, asset = self.saved_local_work('image')
                saved = json.loads(diagnostic.read_text()); saved.pop('original_sha256')
                diagnostic.write_text(json.dumps(saved))
                asset.unlink() if state == 'missing' else asset.write_bytes(b'damaged')
                client = FlowMediaClient()
                with patch.object(client, '_recovery_command') as download, \
                        patch.object(client, '_readback_media') as read:
                    self.assertIsNone(client._recover_pending('image', diagnostic, attempt, root / 'art.png'))
                download.assert_not_called(); read.assert_not_called()
                self.assertEqual(json.loads(diagnostic.read_text())['attempts'], [attempt])

    def test_expired_recovery_still_blocks_changed_checksum_or_receipt(self):
        for state in ('checksum', 'receipt'):
            with self.subTest(state=state):
                root, diagnostic, attempt, result, asset = self.saved_local_work('image', receipt_only=state == 'receipt')
                if state == 'checksum':
                    asset.write_bytes(b'changed-original')
                else:
                    path = root / 'flow_image_attempt_1.result.json'
                    saved = json.loads(path.read_text()); saved['profile'] = 'other'; path.write_text(json.dumps(saved))
                with self.assertRaises(FlowCommandError) as error:
                    FlowMediaClient()._recover_pending('image', diagnostic, attempt, root / 'art.png')
                self.assertEqual(error.exception.category,
                                 'original_asset_changed' if state == 'checksum' else 'request_identity_unverified')

    def test_earlier_attempt_receipt_cannot_displace_latest_attempt(self):
        root, diagnostic, first, result, asset = self.saved_local_work('image', receipt_only=True)
        latest = first | {'number': 2}
        saved = json.loads(diagnostic.read_text()); saved.update(attempts=[first, latest], original_sha256=None)
        diagnostic.write_text(json.dumps(saved))
        (root / 'flow_image_attempt_2.submit').write_bytes((root / 'flow_image_attempt_1.submit').read_bytes())
        client = FlowMediaClient()
        with patch.dict(os.environ, {'FLOW_API_FALLBACK_ENABLED': 'false'}), \
                patch.object(client, '_generate_image') as generate, self.assertRaises(FlowCommandError) as error:
            client.generate_image({}, root / 'generated_art.png')
        self.assertEqual(error.exception.category, 'request_identity_unverified')
        generate.assert_not_called()
        self.assertTrue(asset.is_file())

    def test_api_reservation_precedes_expired_local_flow_result(self):
        root, diagnostic, attempt, result, asset = self.saved_local_work('image')
        saved = json.loads(diagnostic.read_text()); saved['api_reserved'] = True
        diagnostic.write_text(json.dumps(saved))
        client = FlowMediaClient()
        with patch.object(client, '_recover_pending') as recover, \
                patch.object(client, '_generate_image') as generate, self.assertRaises(FlowCommandError) as error:
            client.generate_image({}, root / 'generated_art.png')
        self.assertEqual(error.exception.submission_state, 'api_attempted')
        recover.assert_not_called(); generate.assert_not_called()

    def test_saved_receipt_is_promoted_after_crash_without_remote_work(self):
        for stage in ('image', 'video'):
            with self.subTest(stage=stage):
                root, diagnostic, attempt, result, asset = self.saved_local_work(stage, receipt_only=stage == 'image')
                target = root / ('generated_art.png' if stage == 'image' else 'generated_video.mp4')
                client = FlowMediaClient()
                with patch.object(client, '_generation_seconds', return_value=0), \
                        patch.object(client, '_run_json', side_effect=AssertionError('Submission forbidden')), \
                        patch.object(client, '_readback_media', side_effect=AssertionError('Readback forbidden')), \
                        patch.object(client, '_probe_video', return_value=(1080, 1920, 8, True)), \
                        patch('flow_media_client.run_bounded', side_effect=AssertionError('Download/upscale forbidden')):
                    if stage == 'image':
                        client.generate_image({}, target)
                    else:
                        client.generate_video('original', root / 'input_art.png', target)
                self.assertTrue(target.is_file())
                self.assertEqual(json.loads(diagnostic.read_text())['attempts'], [attempt])

    def test_file_disappearing_during_handoff_cannot_reopen_remote_recovery(self):
        for stage in ('image', 'video'):
            for exhausted in (False, True):
                with self.subTest(stage=stage, exhausted=exhausted):
                    root, diagnostic, attempt, result, asset = self.saved_local_work(stage, exhausted=exhausted)
                    if exhausted:
                        attempt['recovery_deadline'] = 10**12
                    client = FlowMediaClient()
                    with patch.object(client, '_probe_video', return_value=(1080, 1920, 8, True)):
                        recovered = client._recover_pending(stage, diagnostic, attempt, root / 'generated.png')
                    self.assertIsNotNone(recovered)
                    asset.unlink()
                    with patch('flow_media_client.subprocess.run') as run, \
                            patch('flow_media_client.subprocess.Popen') as spawn, self.assertRaises(FlowCommandError):
                        if stage == 'image':
                            client._generate_image({}, root / 'generated_art.png', recovered)
                        else:
                            client._generate_video('original', root / 'input_art.png', root / 'generated_video.mp4', recovered)
                    run.assert_not_called(); spawn.assert_not_called()

    def test_conflicting_receipt_and_result_block_local_reuse(self):
        root, diagnostic, attempt, result, asset = self.saved_local_work('image', receipt_only=True)
        saved = json.loads(diagnostic.read_text())
        saved['result'] = result | {'images': [result['images'][0] | {'media_name': 'different'}]}
        diagnostic.write_text(json.dumps(saved))
        with self.assertRaises(FlowCommandError) as error:
            FlowMediaClient()._recover_pending('image', diagnostic, attempt, root / 'art.png')
        self.assertEqual(error.exception.category, 'request_identity_unverified')

    def test_local_reuse_rejects_unowned_or_malformed_paths(self):
        for state in ('outside', 'symlink', 'malformed'):
            with self.subTest(state=state):
                root, diagnostic, attempt, result, asset = self.saved_local_work('image')
                outside = self.save_image({}, self.root / 'outside.png')
                saved = json.loads(diagnostic.read_text())
                if state == 'symlink':
                    asset.unlink(); asset.symlink_to(outside)
                else:
                    saved['result']['images'][0]['local_path'] = str(outside) if state == 'outside' else 7
                    diagnostic.write_text(json.dumps(saved))
                with self.assertRaises(FlowCommandError) as error:
                    FlowMediaClient()._recover_pending('image', diagnostic, attempt, root / 'art.png')
                self.assertEqual(error.exception.category, 'request_identity_unverified')

    def test_missing_or_damaged_expired_asset_cannot_enable_replacement(self):
        for state in ('missing', 'damaged'):
            with self.subTest(state=state):
                root, diagnostic, attempt, result, asset = self.saved_local_work('image')
                saved = json.loads(diagnostic.read_text()); saved.pop('original_sha256'); diagnostic.write_text(json.dumps(saved))
                asset.unlink() if state == 'missing' else asset.write_bytes(b'damaged')
                client = FlowMediaClient()
                target = root / 'generated_art.png'
                def replace(request, output, recovered):
                    self.assertIsNone(recovered)
                    (root / 'flow_image_attempt_2.submit').write_bytes((root / 'flow_image_attempt_1.submit').read_bytes())
                    self.save_image({}, output)
                with patch.object(client, '_generate_image', side_effect=replace) as generate, \
                        patch.object(client, '_recovery_command', side_effect=AssertionError('Expired download forbidden')), \
                        patch.object(client, '_readback_media', side_effect=AssertionError('Expired readback forbidden')), \
                        patch.object(client, '_notify_transition'):
                    with self.assertRaises(FlowCommandError):
                        client.generate_image({}, target)
                generate.assert_not_called()
                attempts = json.loads(diagnostic.read_text())['attempts']
                self.assertEqual([a['number'] for a in attempts], [1])
                self.assertEqual(attempts[0]['recovery_deadline'], attempt['recovery_deadline'])

    def test_short_window_skips_flow_for_api_then_stops_all_generation(self):
        self.freeze()
        api = Mock()
        api.generate_from_json.side_effect = self.save_image
        with patch.object(FlowMediaClient, '_generation_seconds', return_value=1600), \
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

    def test_auth_error_without_child_evidence_cannot_authorize_early_api(self):
        self.freeze()
        with patch.object(FlowMediaClient, '_generate_image', side_effect=FlowCommandError(
                'image', 'auth_required', submission_state='not_attempted')) as flow, \
                patch('flow_media_client.GoogleImageClient') as api:
            with self.assertRaises(FlowCommandError) as error:
                FlowMediaClient().generate_image({}, self.root / 'generated_art.png')
        self.assertEqual(error.exception.category, 'request_identity_unverified')
        flow.assert_called_once(); api.assert_not_called()

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
        with patch.dict(os.environ, {'MEDIA_GENERATION_PROVIDER': 'flow', 'STATE_ZERO_PRIVATE_ROOT':str(self.root)}), \
                patch.object(pipeline, '_claim_daily_run_or_exit'), \
                patch.object(pipeline, '_ensure_owner_runtime_dirs'), \
                patch.object(pipeline, '_stop_heartbeat_thread'), \
                patch.object(pipeline, '_cleanup_non_authoritative_daily_state'), \
                patch('pipeline._setup_global_exception_handler'), \
                patch.object(pipeline, '_handle_runtime_stage_error', return_value=True), \
                patch.object(pipeline, 'step_1_validate'), patch.object(pipeline,'_is_terminal_rescue_run',return_value=False), \
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
        for reasons in (['SAFETY'], ['RECAPTCHA_FAILED'], ['QUOTA', 'PUBLIC_ERROR_UNSAFE_CONTENT']):
            with self.subTest(reasons=reasons):
                payload = {'status': 'error', 'failure_reasons': reasons,
                           'error': {'class': 'ServiceError', 'detail': 'Unavailable', 'retryable': True}}
                with patch('flow_media_client.run_bounded', return_value=subprocess.CompletedProcess(
                        ['gflow'], 1, json.dumps(payload), '')):
                    with self.assertRaises(FlowCommandError) as caught:
                        FlowMediaClient._run_json(['gflow', 'image', 'test'], 10)
                self.assertTrue(FlowMediaClient._stops_replacement(caught.exception))
                self.assertIn('provider reason', caught.exception.detail)

    def test_pre_submit_hint_without_child_evidence_keeps_one_uncertain_reservation(self):
        self.freeze()
        with patch.object(FlowMediaClient, '_generate_image', side_effect=FlowCommandError(
                'image', 'pre_submit_timeout', submission_state='not_attempted')) as flow, \
                patch('flow_media_client.GoogleImageClient') as api:
            with self.assertRaises(FlowCommandError):
                FlowMediaClient().generate_image({}, self.root / 'generated_art.png')
        flow.assert_called_once(); api.assert_not_called()
        saved = json.loads((self.root / 'flow_image_diagnostics.json').read_text())
        self.assertEqual(len(saved['attempts']), 1)
        self.assertNotIn('submit_attempted', saved['attempts'][0])

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
        with patch.object(FlowMediaClient, '_generate_video', side_effect=FlowCommandError('video', 'generation_failed', retryable=True)) as flow, \
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

    def test_catalog_operation_without_exact_context_cannot_recover_other_media(self):
        diagnostic = self.root / 'flow_image_diagnostics.json'
        attempt = {'number': 1, 'catalog_position': 0, 'catalog_operation_id': 'unrelated'}
        diagnostic.write_text(json.dumps({'record_version':2, 'attempts': [attempt]}))
        client = FlowMediaClient()
        with patch.object(client, '_readback_media') as browser:
            self.assertIsNone(client._recover_pending('image', diagnostic, attempt, self.root / 'art.png'))
        browser.assert_not_called()

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

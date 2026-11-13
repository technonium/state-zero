"""Simulated schedule invocations with real durable recovery records and mocked transport."""
import tests
import hashlib
import json
import os
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from PIL import Image
from flow_fixture import freeze, video_receipt
from flow_media_client import FlowCommandError, FlowMediaClient
from pipeline import WHOOPPipeline


class FinalSlotTests(unittest.TestCase):
    def run_day(self, recover_at_1030=False, api_fails=False, *, scenario='normal'):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            stack.enter_context(patch.dict(os.environ, {'MEDIA_GENERATION_PROVIDER': 'flow',
                'FLOW_API_FALLBACK_ENABLED': 'true', 'EMERGENCY_FALLBACK_ENABLED': 'true',
                'GOOGLE_VIDEO_TIMEOUT_SECONDS': '900', 'PIPELINE_DATE': '2026-11-08',
                'STATE_ZERO_PRIVATE_ROOT': str(root)}, clear=True))
            freeze(root, video='original')
            art = root / 'generated_art.png'
            Image.new('RGB', (768, 1024), 'navy').save(art)
            shutil.copyfile(art, root / 'input_art.png')
            output = root / 'generated_video.mp4'
            diagnostic = root / 'flow_video_diagnostics.json'
            context = root / 'flow_video_attempt_1.context.json'
            identity = {'request': {'prompt': 'original', 'start_frame_sha256': hashlib.sha256(art.read_bytes()).hexdigest()},
                        'profile': 'shadow', 'project': '', 'settings': 'omni-1.2-flash/i2v/9:16/x1/8s/720p'}
            request_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            video_receipt(root, request_hash, '22222222-2222-4222-8222-222222222222')
            receipt_path = context.with_name('flow_video_attempt_1.result.json')
            receipt = json.loads(receipt_path.read_text())
            receipt_path.unlink()
            marker = context.with_name('flow_video_attempt_1.submit')
            if scenario == 'overlap':
                marker.unlink()
                context.unlink()
            else:
                failure = ({'category': 'generation_failed', 'retryable': True} if scenario == 'replacement'
                           else {'category': 'post_submit_error'})
                FlowMediaClient._write_diagnostics(diagnostic, {'request_hash': request_hash,
                    'attempts': [{'number': 1, 'failure': failure}]})
            now = datetime(2026, 11, 8, 10, tzinfo=ZoneInfo('Asia/Kolkata'))
            stack.enter_context(patch('utils.get_pipeline_now', side_effect=lambda: now))
            stack.enter_context(patch('flow_media_client.time.time', side_effect=lambda: now.timestamp()))
            stack.enter_context(patch.object(FlowMediaClient, '_probe_video', return_value=(1080, 1920, 8, True)))
            retrieval_times = []
            creation_times = []
            competing_slot = WHOOPPipeline.__new__(WHOOPPipeline)
            competing_slot._run_owned = Mock()
            skipped = False

            def worker(command, timeout):
                nonlocal now, skipped
                if 'search' in command:
                    if scenario == 'overlap':
                        now = now.replace(hour=13, minute=0, second=0)
                        with patch('builtins.print') as printed:
                            competing_slot.run()
                        skipped = any('skipping this slot' in str(call) for call in printed.call_args_list)
                    return 0, {'candidate': {'kind': 'video', 'context_sha256': hashlib.sha256(context.read_bytes()).hexdigest(),
                                              'result': receipt, 'observed_at': now.timestamp()}}
                retrieval_times.append(now.strftime('%H:%M:%S'))
                self.assertLessEqual(timeout, 90)
                self.assertEqual(json.loads(diagnostic.read_text())['retrieval']['candidate']['result'], receipt)
                if recover_at_1030 and (now.hour, now.minute) == (10, 30):
                    raw = root / 'flow_recovered' / 'original.mp4'
                    raw.write_bytes(b'recovered exact original')
                    return 0, {'result': receipt | {'local_path': str(raw), 'sha256': hashlib.sha256(raw.read_bytes()).hexdigest()}}
                if scenario == 'overlap':
                    now += timedelta(seconds=timeout)
                return 3, {'error_class': 'TargetClosedError'}

            stack.enter_context(patch.object(FlowMediaClient, '_recovery_command', side_effect=worker))

            def flow(prompt, image, target, result):
                nonlocal now
                if result is None and scenario in ('overlap', 'replacement'):
                    number = 1 if scenario == 'overlap' else 2
                    video_receipt(root, request_hash, receipt['media_id'], attempt=number)
                    context.with_name(f'flow_video_attempt_{number}.result.json').unlink()
                    if scenario == 'overlap':
                        now = now.replace(hour=12, minute=55, second=2)
                        raise FlowCommandError('video', 'post_submit_error', submission_state='forwarded')
                    now = now.replace(minute=25)
                    raise FlowCommandError('video', 'generation_failed', retryable=True, submission_state='forwarded')
                self.assertIsNotNone(result, 'No replacement generation may be started')
                self.assertEqual(json.loads(receipt_path.read_text()), receipt)
                shutil.copyfile(result['local_path'], target)

            generation = stack.enter_context(patch.object(FlowMediaClient, '_generate_video', side_effect=flow))
            api = stack.enter_context(patch('flow_media_client.GoogleVideoClient')).return_value
            def create(prompt, image, target):
                creation_times.append(now.strftime('%H:%M:%S'))
                if api_fails:
                    raise TimeoutError('Fixture accepted request then lost reply')
                target.write_bytes(b'validated Omni')
            api.generate_from_image.side_effect = create
            def resume(target):
                self.assertTrue(json.loads(diagnostic.read_text())['api_reserved'])
                raise TimeoutError('No new creation allowed')
            api.resume_operation.side_effect = resume
            p = WHOOPPipeline.__new__(WHOOPPipeline)
            p.output_dir, p.run_date, p.run_token = root, '2026-11-08', 'test'
            p.mode, p.post_to_instagram, p.media_mode = 'automatic', True, 'local_test'
            p.in_emergency_fallback = p.in_auto_fallback = False
            p.daily_run = Mock()
            p.daily_run.load_state.return_value = {}
            p.daily_run.current_status.return_value = 'STARTING'
            p._now = lambda: now
            p._run_emergency_fallback = Mock(return_value=True)
            for method in ('_claim_daily_run_or_exit', '_ensure_owner_runtime_dirs', '_set_heartbeat_context',
                           '_stop_heartbeat_thread', '_cleanup_non_authoritative_daily_state', 'step_1_validate',
                           'step_1b_validate_instagram_token', '_revalidate_whoop_checkpoint', 'step_10a_render_image',
                           'step_10b_render_video', '_build_caption_or_raise', '_run_portfolio_media_secondary'):
                setattr(p, method, Mock())
            p.step_7_generate_image = Mock(return_value=art)
            p._load_required_text_outputs = Mock(return_value=('blend', 'creature', 'environment'))
            p._prepare_instagram_media_urls = Mock(return_value=(None, None))
            p.step_14_post_instagram = Mock(return_value={'post_id': 'fixture'})
            p.finalize_posted_run = Mock(return_value=True)
            stack.enter_context(patch('pipeline._setup_global_exception_handler'))
            early_slots = ['10:00:00', '10:30:00']
            slots = {
                'overlap': ['12:30:00'],
                'replacement': ['13:00:00'],
                'boundary': early_slots + ['12:59:59', '13:00:00'],
                'late': early_slots + ['13:07:01', '14:00:00'],
                'normal': early_slots if recover_at_1030 else early_slots + [
                    '11:00:00', '11:30:00', '12:00:00', '12:30:00', '13:00:00'
                ] + (['13:01:00', '14:00:00'] if api_fails else []),
            }[scenario]
            for slot in slots:
                now = datetime.fromisoformat(f'2026-11-08T{slot}+05:30')
                should_defer = (scenario != 'overlap' and now.hour < 13 and
                                not (recover_at_1030 and now.minute == 30))
                if should_defer or (api_fails and now.hour == 13) or slot == '13:07:01':
                    with self.assertRaises(SystemExit) as exited:
                        p.run()
                    self.assertEqual(exited.exception.code, 0)
                    p.step_14_post_instagram.assert_not_called()
                    p.daily_run.mark_retryable_failure.assert_called()
                    p.daily_run.release_claim.assert_called()
                else:
                    try:
                        p.run()
                    except SystemExit as exc:
                        self.fail(f'{slot} deferred unexpectedly (exit {exc.code})')
                if should_defer:
                    api.generate_from_image.assert_not_called()
            self.assertEqual(len(json.loads(diagnostic.read_text())['attempts']), 1)
            if recover_at_1030:
                generation.assert_called_once()
                p.step_14_post_instagram.assert_called_once()
                self.assertEqual(output.read_bytes(), b'recovered exact original')
                self.assertEqual(retrieval_times, early_slots)
                api.generate_from_image.assert_not_called()
            elif scenario == 'late':
                self.assertEqual(retrieval_times, early_slots + ['13:07:01'])
                api.generate_from_image.assert_not_called()
                p._run_emergency_fallback.assert_called_once()
                p.step_14_post_instagram.assert_not_called()
            else:
                if scenario == 'overlap':
                    generation.assert_called_once()
                    self.assertTrue(skipped)
                    competing_slot._run_owned.assert_not_called()
                    self.assertEqual(retrieval_times, ['13:00:00', '13:01:30', '13:03:00'])
                elif scenario == 'replacement':
                    generation.assert_not_called()
                    self.assertEqual(retrieval_times, [])
                else:
                    generation.assert_not_called()
                    self.assertEqual(retrieval_times, early_slots + ['13:00:00'])
                api.generate_from_image.assert_called_once()
                self.assertEqual(creation_times, ['13:04:30' if scenario == 'overlap' else '13:00:00'])
                if api_fails:
                    api.resume_operation.assert_called_once()
                    p._run_emergency_fallback.assert_called_once()
                    p.step_14_post_instagram.assert_not_called()
                else:
                    p.step_14_post_instagram.assert_called_once()
                    self.assertEqual(output.read_bytes(), b'validated Omni')

    def test_10am_failure_then_1030_exact_recovery_posts(self):
        self.run_day(recover_at_1030=True)

    def test_1230_defers_and_final_slot_posts_one_omni(self):
        self.run_day()

    def test_lost_api_reply_cannot_recreate_and_14_rescue_still_works(self):
        self.run_day(api_fails=True)

    def test_1230_lock_owner_can_create_when_13_slot_skips(self):
        self.run_day(scenario='overlap')

    def test_13_skips_replacement_to_preserve_omni_budget(self):
        self.run_day(scenario='replacement')

    def test_one_second_before_13_defers_and_13_can_create(self):
        self.run_day(scenario='boundary')

    def test_after_omni_cutoff_cannot_create_and_rescue_still_runs(self):
        self.run_day(scenario='late')

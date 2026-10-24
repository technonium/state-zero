"""Credit-free checks at the verdict, post-publication and state-write boundaries."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
import pipeline
from daily_run_state import DailyRunStateManager
from flow_media_client import FlowCommandError, FlowMediaClient


class ReleaseTwoTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {'STATE_ZERO_PRIVATE_ROOT': str(self.root)}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def classify(self, reasons, *, state=None, error_class='ServiceError', failed=False):
        marker = self.root / 'flow_video_attempt_1.submit'
        if state is not None:
            marker.write_text(json.dumps({'state': state}))
        else:
            marker.unlink(missing_ok=True)
        payload = {'status': 'error', 'failure_reasons': reasons,
                   'error': {'class': error_class, 'detail': 'MODEL CREDIT REJECT', 'retryable': False}}
        if failed:
            payload.update(media_id='exact', generation_status='MEDIA_GENERATION_STATUS_FAILED')
        with patch('flow_media_client.run_bounded', return_value=subprocess.CompletedProcess(
                [], 1, json.dumps(payload), '')), patch.object(FlowMediaClient, '_catalog_position', return_value=0):
            with self.assertRaises(FlowCommandError) as caught:
                FlowMediaClient._run_json(['gflow', 'video'], 10, marker=marker)
        return caught.exception

    def test_unknown_verdict_before_submission_is_transient(self):
        for reasons in (['GENERATION_TIMEOUT'], ['NEW_REASON'], ['QUOTA', 'REFUSAL'], {'bad': True}):
            with self.subTest(reasons=reasons):
                error = self.classify(reasons)
                self.assertEqual(error.category, 'pre_submit_transient')
                self.assertFalse(FlowMediaClient._stops_replacement(error))

    def test_unknown_verdict_after_forwarding_requires_same_result_recovery(self):
        for reasons in (['GENERATION_TIMEOUT'], ['NEW_REASON'], ['QUOTA', 'NEW_REASON']):
            with self.subTest(reasons=reasons):
                error = self.classify(reasons, state='forwarded')
                self.assertEqual(error.category, 'post_submit_error')
                self.assertEqual(error.submission_state, 'forwarded')
                self.assertFalse(error.retryable)

    def test_typed_unknown_refusal_uses_submission_state(self):
        for state, category in ((None, 'pre_submit_transient'), ('forwarded', 'post_submit_error')):
            with self.subTest(state=state):
                self.assertEqual(self.classify([], state=state,
                    error_class='UnknownFlowRefusalError').category, category)

    def test_known_restrictions_override_quota_and_unknown_verdicts(self):
        for reason in ('PUBLIC_ERROR_UNSAFE_CONTENT', 'PUBLIC_ERROR_UNSAFE_GENERATION',
                       'PUBLIC_ERROR_UNSAFE_IDENTITY', 'PUBLIC_ERROR_UNSAFE_FACE',
                       'PUBLIC_ERROR_UNUSUAL_ACTIVITY', 'SAFETY_REJECTION', 'ACCOUNT_RESTRICTED',
                       'RECAPTCHA_FAILED'):
            with self.subTest(reason=reason):
                error = self.classify(['QUOTA', reason, 'NEW_REASON'], state='forwarded')
                self.assertEqual(error.category, 'provider_rejected')
                self.assertTrue(FlowMediaClient._stops_replacement(error))

    def test_quota_and_confirmed_failure_keep_existing_routes(self):
        self.assertEqual(self.classify(['QUOTA_EXCEEDED']).category, 'credits_exhausted')
        error = self.classify([], state='forwarded', failed=True)
        self.assertEqual(error.category, 'generation_failed')
        self.assertTrue(error.retryable)

    def test_blocked_marker_is_not_an_uncertain_forwarded_request(self):
        self.assertEqual(self.classify(['NEW_REASON'], state='blocked_before_submission').category,
                         'blocked_before_submission')

    def manager(self):
        manager = DailyRunStateManager(run_date='2030-01-01', timezone_name='Asia/Kolkata', run_token='owner')
        manager.acquire()
        return manager

    def test_alert_write_cannot_erase_concurrent_publication(self):
        manager = self.manager()
        entered, release, attempted = threading.Event(), threading.Event(), threading.Event()
        errors = []
        def notify():
            entered.set()
            if not release.wait(3):
                raise TimeoutError('fixture notification')
            return True
        def post():
            attempted.set()
            try:
                manager.mark_posted(post_id='exact', permalink='https://instagram.com/p/exact/')
            except Exception as error:
                errors.append(error)
        alert = threading.Thread(target=manager.notify_once, args=('DEGRADED', notify))
        publish = threading.Thread(target=post)
        alert.start()
        try:
            self.assertTrue(entered.wait(2))
            publish.start()
            self.assertTrue(attempted.wait(2))
            publish.join(.1)
        finally:
            release.set()
            alert.join(3)
            if publish.ident is not None:
                publish.join(3)
        self.assertFalse(errors)
        state = manager.load_state()
        self.assertEqual(state['status'], 'POSTED')
        self.assertEqual(state['instagram_post_id'], 'exact')
        self.assertEqual(state['last_actionable_alert'], 'DEGRADED')
        manager.heartbeat(status='STARTING')
        self.assertEqual(manager.current_status(), 'POSTED')

    def test_alert_transitions_and_failed_delivery_are_preserved(self):
        manager = self.manager()
        notify = Mock(return_value=True)
        for transition in ('A', 'A', 'B', 'A'):
            manager.notify_once(transition, notify)
        self.assertEqual(notify.call_count, 3)
        manager.notify_once('C', lambda: False)
        self.assertEqual(manager.load_state()['last_actionable_alert'], 'A')

    def test_real_portfolio_subprocess_failure_is_secondary_after_post(self):
        p = pipeline.WHOOPPipeline.__new__(pipeline.WHOOPPipeline)
        p.run_date = '2030-01-01'
        p.output_dir = self.root
        p.base_dir = self.root
        p.post_to_instagram = p.portfolio_media_enabled = True
        p.daily_run = self.manager()
        p.daily_run.mark_posted(post_id='exact', permalink='https://instagram.com/p/exact/')
        notifier = Mock(include_stderr_lines=40)
        render = lambda *_args: p.safe_step('Render Portfolio Media', 'fixture.py')
        with patch.object(p, '_set_heartbeat_context'), patch('pipeline.pipeline_seconds_remaining', return_value=30), \
                patch('pipeline.run_bounded', return_value=subprocess.CompletedProcess([], 1, '', 'ffmpeg failed')), \
                patch('pipeline.get_notifier', return_value=notifier), \
                patch.object(p, 'step_16_render_portfolio_media', side_effect=render), \
                patch.object(p, 'step_17_upload_portfolio_vps') as upload:
            p._run_portfolio_media_secondary(art_path=self.root/'art', video_path=self.root/'video')
        self.assertEqual(p.daily_run.current_status(), 'POSTED')
        self.assertEqual(p.daily_run.load_state()['instagram_post_id'], 'exact')
        notifier.notify_error.assert_not_called()
        notifier.notify_warning.assert_called_once()
        self.assertIn('Script exited with code 1', notifier.notify_warning.call_args.kwargs['details_tail'])
        upload.assert_not_called()

    def test_fatal_error_is_typed_and_only_run_converts_it_to_exit(self):
        fatal_type = getattr(pipeline, 'PipelineFatalError', None)
        self.assertIsNotNone(fatal_type)
        p = pipeline.WHOOPPipeline.__new__(pipeline.WHOOPPipeline)
        p.post_to_instagram = True
        p.daily_run = self.manager()
        p.run_date = '2030-01-01'
        p.output_dir = self.root
        with patch('pipeline.get_notifier') as notifier:
            with self.assertRaises(fatal_type) as caught:
                p.log_error('fixture', 'fatal fixture')
            notifier.assert_not_called()
            with patch.object(p, '_run_owned', side_effect=caught.exception):
                with self.assertRaises(SystemExit) as exit_error:
                    p.run()
            self.assertEqual(exit_error.exception.code, 1)
            self.assertEqual(p.daily_run.current_status(), 'FAILED_FATAL')
            notifier.return_value.notify_error.assert_called_once()

    def test_top_level_finalization_failure_cannot_fail_a_confirmed_post(self):
        fatal_type = getattr(pipeline, 'PipelineFatalError', None)
        self.assertIsNotNone(fatal_type)
        for source in ('normal', 'emergency_fallback'):
            with self.subTest(source=source):
                p = pipeline.WHOOPPipeline.__new__(pipeline.WHOOPPipeline)
                p.post_to_instagram = True
                p.run_date = '2030-01-01'
                p.daily_run = DailyRunStateManager(run_date=p.run_date,
                    timezone_name='Asia/Kolkata', run_token='owner')
                p.daily_run.mark_posted_after_publish(post_id='exact', permalink='https://instagram.com/p/exact/',
                    extra={'asset_source': source})
                before = p.daily_run.state_path.read_bytes()
                with patch.object(p, '_run_owned', side_effect=fatal_type(stage='Archive', message='disk failure')), \
                        patch('pipeline.get_notifier', return_value=Mock()) as notifier, \
                        patch.object(p, '_report_fatal_error') as fatal:
                    p.run()
                self.assertEqual(p.daily_run.state_path.read_bytes(), before)
                fatal.assert_not_called()
                notifier.return_value.notify_warning.assert_called_once()


if __name__ == '__main__':
    unittest.main()

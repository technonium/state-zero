"""Credit-free checks at the verdict, post-publication and state-write boundaries."""
import json
import contextlib
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

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
                [], 1, json.dumps(payload), '')):
            with self.assertRaises(FlowCommandError) as caught:
                FlowMediaClient._run_json(['gflow', 'video'], 10, marker=marker)
        return caught.exception

    def test_unknown_verdict_before_submission_is_transient(self):
        for reasons in (['GENERATION_TIMEOUT'], ['NEW_REASON'], ['QUOTA', 'REFUSAL']):
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

    def test_malformed_reasons_cannot_erase_policy_or_authorize_spending(self):
        self.assertEqual(self.classify(['PUBLIC_ERROR_UNSAFE_CONTENT', None]).category, 'provider_rejected')
        for reasons in ({'bad': True}, [None], ['QUOTA', None], None, '', False, 0):
            for state in (None, 'forwarded', 'blocked_before_submission'):
                with self.subTest(reasons=reasons, state=state):
                    for failed in (False, True):
                        error = self.classify(reasons, state=state, failed=failed)
                        self.assertEqual(error.category, 'request_identity_unverified')
                        self.assertTrue(FlowMediaClient._stops_replacement(error))
        for name in FlowMediaClient._input_names(self.root):
            (self.root / name).write_text('{}' if name.endswith('.json') else 'original')
        os.environ['FLOW_API_FALLBACK_ENABLED'] = 'true'
        FlowMediaClient.freeze_inputs(self.root)
        payload = dict(status='error', failure_reasons=['PUBLIC_ERROR_UNSAFE_CONTENT', None],
                       error={'class': 'ServiceError', 'retryable': True})
        with patch('flow_media_client.run_bounded', return_value=subprocess.CompletedProcess(
                [], 1, json.dumps(payload), '')) as cli, \
                patch.object(FlowMediaClient, '_generation_seconds', return_value=7200), \
                patch('flow_media_client.GoogleImageClient') as api:
            with self.assertRaises(FlowCommandError) as caught:
                FlowMediaClient().generate_image({}, self.root / 'generated_art.png')
        self.assertEqual(caught.exception.category, 'provider_rejected')
        cli.assert_called_once()
        api.assert_not_called()

    def test_failed_readback_must_be_unique_and_preserve_known_identity(self):
        import flow_readback as readback
        from test_flow_video_readback import video_row, listing, context, MEDIA, OTHER
        with self.assertRaises(FlowCommandError) as caught:
            readback.select_video(listing(video_row(status=4)), context(), media_id=MEDIA)
        self.assertEqual(caught.exception.category, 'generation_failed')
        for rows, data in (([video_row(status=4), video_row(OTHER, OTHER)], context()),
                           ([video_row(status=4)], context() | {'frame_id': OTHER})):
            with self.subTest(rows=len(rows)), self.assertRaises(readback.ReadbackUnavailable):
                readback.select_video(listing(*rows), data)

    def test_failed_worker_cannot_authorize_replacement_without_binding_and_cleanup(self):
        from test_flow_video_readback import MEDIA, OTHER
        context_file = self.root / 'flow_video_attempt_1.context.json'
        context_file.write_text('{}')
        reply = dict(status='generation_failed', media_id=MEDIA,
            context_sha256=hashlib.sha256(b'{}').hexdigest(),
            worker={'processes': {'cleanup_verified': True}})
        for changed in ({'context_sha256': 'b'*64}, {'media_id': OTHER},
                        {'worker': {'processes': {'cleanup_verified': False}}}):
            with self.subTest(changed=changed), \
                    patch.object(FlowMediaClient, '_generation_seconds', return_value=7200), \
                    patch.object(FlowMediaClient, '_recovery_command', return_value=(3, reply | changed)):
                with self.assertRaises(FlowCommandError) as caught:
                    FlowMediaClient()._readback_media(context_file, 'a'*64,
                        self.root / 'video.mp4', 10**12, stage='video', known={'media_id': MEDIA})
                self.assertEqual(caught.exception.category,
                    'execution_busy' if 'worker' in changed else 'request_identity_unverified')
            (self.root / 'flow_video_diagnostics.json').unlink()

    def test_confirmed_failure_precedes_unknown_but_not_restriction_or_block(self):
        for reasons in (['GENERATION_TIMEOUT'], ['QUOTA', 'NEW_REASON']):
            with self.subTest(reasons=reasons):
                error = self.classify(reasons, state='forwarded', failed=True,
                                      error_class='UnknownFlowRefusalError')
                self.assertEqual(error.category, 'generation_failed')
                self.assertTrue(error.retryable)
        self.assertEqual(self.classify(['PUBLIC_ERROR_UNSAFE_CONTENT', None],
            state='forwarded', failed=True).category, 'provider_rejected')
        self.assertEqual(self.classify(['NEW_REASON'], state='blocked_before_submission', failed=True).category,
                         'blocked_before_submission')

    def test_exact_failed_clip_recovers_failure_then_one_flow_replacement(self):
        import flow_readback as readback
        from test_flow_video_readback import video_row, listing, context, PROJECT, MEDIA
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
        import flow_media_recover as worker
        os.environ.update(FLOW_PREFLIGHT_PROJECT_ID=PROJECT, FLOW_API_FALLBACK_ENABLED='true')
        output = self.root / 'generated_video.mp4'
        diagnostic = self.root / 'flow_video_diagnostics.json'
        diagnostic.write_text(json.dumps({'record_version':2, 'request_hash': 'a'*64, 'attempts': []}))
        (self.root / 'flow_inputs.json').write_text('{}')
        frame = self.root / 'flow_start_frame.png'
        frame.write_bytes(b'frozen synthetic frame')
        calls = []
        def generate(recovered):
            self.assertIsNone(recovered)
            calls.append('submit')
            number = len(calls)
            marker = self.root / f'flow_video_attempt_{number}.submit'
            data = context() | dict(attempt=number, request_hash='a'*64,
                frozen_inputs_sha256=hashlib.sha256(b'{}').hexdigest(),
                source_hash=hashlib.sha256(frame.read_bytes()).hexdigest())
            marker.with_suffix('.context.json').write_text(json.dumps(data))
            marker.write_text(json.dumps(dict(state='forwarded', actual_model=data['model'],
                                              readback_hash=readback.identity_hash(data))))
            if number == 1:
                return FlowMediaClient._run_json(['gflow', 'video'], 10, marker=marker)
            output.write_bytes(b'validated fixture replacement')
        async def download(_project, _media, target, **kwargs):
            return await readback.recover_context(Mock(_context=Mock()), kwargs['context_file'], PROJECT,
                kwargs['request_hash'], kwargs['deadline'], target, mode=kwargs['mode'])
        def process(command, **_kwargs):
            if command[0] == 'gflow':
                payload = dict(status='error', failure_reasons=['GENERATION_TIMEOUT'],
                               error={'class': 'UnknownFlowRefusalError'})
                return subprocess.CompletedProcess(command, 1, json.dumps(payload), '')
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.object(sys, 'argv', command[1:]), \
                    patch.object(worker, 'download_existing', side_effect=download), \
                    contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = worker.main()
            result = subprocess.CompletedProcess(command, code, stdout.getvalue(), stderr.getvalue())
            result.process_evidence = {'cleanup_verified': True}
            return result
        with patch('flow_media_client.run_bounded', side_effect=process), \
                patch.object(FlowMediaClient, '_generation_seconds', return_value=7200), \
                patch.object(FlowMediaClient, '_probe_video', return_value=(1080, 1920, 8, True)), \
                patch.object(FlowMediaClient, '_notify_transition'), \
                patch('flow_readback.snapshot', new=AsyncMock(return_value=listing(video_row(status=4)))), \
                patch('flow_media_client.GoogleVideoClient') as api:
            FlowMediaClient()._generate_daily('video', {'prompt': 'synthetic'}, output, generate, frame)
        self.assertEqual(calls, ['submit', 'submit'])
        saved = json.loads(diagnostic.read_text())
        self.assertEqual(saved['attempts'][0]['failure']['category'], 'generation_failed')
        self.assertEqual(saved['attempts'][0]['failure']['media_id'], MEDIA)
        self.assertEqual(saved['provider'], 'flow')
        api.assert_not_called()

    def test_blocked_marker_is_not_an_uncertain_forwarded_request(self):
        self.assertEqual(self.classify(['NEW_REASON'], state='blocked_before_submission').category,
                         'blocked_before_submission')

    def manager(self):
        manager = DailyRunStateManager(run_date='2030-01-01', timezone_name='Asia/Kolkata', run_token='owner')
        manager.acquire()
        return manager

    def test_alert_write_cannot_erase_concurrent_publication(self):
        manager = self.manager()
        entered, release, attempted, posted = (threading.Event() for _ in range(4))
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
                posted.set()
            except Exception as error:
                errors.append(error)
        alert = threading.Thread(target=manager.notify_once, args=('DEGRADED', notify))
        publish = threading.Thread(target=post)
        alert.start()
        try:
            self.assertTrue(entered.wait(2))
            publish.start()
            self.assertTrue(attempted.wait(2))
            self.assertTrue(posted.wait(2), 'Notification I/O must not hold the state lock')
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

    def test_fatals_inside_token_preflight_and_video_bypass_fallback_handlers(self):
        self.assertFalse(issubclass(pipeline.PipelineFatalError, Exception))
        prompt = self.root / 'video_prompt.txt'
        prompt.write_text('synthetic')
        for stage in ('token', 'video'):
            with self.subTest(stage=stage):
                p = pipeline.WHOOPPipeline.__new__(pipeline.WHOOPPipeline)
                p.post_to_instagram = True
                p.output_dir = self.root
                p.run_date = '2030-01-01'
                p.daily_run = self.manager()
                fatal = pipeline.PipelineFatalError(stage=stage, message='fatal fixture')
                step = lambda: (p.step_1b_validate_instagram_token() if stage == 'token' else
                                p.step_9_generate_video(self.root / 'art.png', prompt))
                with patch.object(p, '_set_heartbeat_context'), \
                        patch.object(p, '_current_generation_status', return_value='STARTING'), \
                        patch('instagram_token_manager.get_instagram_token_manager', side_effect=fatal), \
                        patch.object(FlowMediaClient, 'generate_api_stage', side_effect=fatal), \
                        patch.object(p, '_run_owned', side_effect=step), \
                        patch.object(p, '_handle_runtime_stage_error') as handler, \
                        patch.object(p, '_run_emergency_fallback') as fallback, \
                        patch.object(p, '_report_fatal_error') as report:
                    with self.assertRaises(SystemExit) as caught:
                        p.run()
                self.assertEqual(caught.exception.code, 1)
                report.assert_called_once_with(fatal)
                handler.assert_not_called()
                fallback.assert_not_called()
                p.daily_run.release_claim()
                p.daily_run.delete_state()

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

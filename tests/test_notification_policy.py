"""Reviewed notification policy; all delivery boundaries are mocked."""
import tests  # Explicit notification isolation before production imports.
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
import notifier
from flow_media_client import FlowMediaClient
from daily_run_state import DailyRunStateManager
from pipeline import PipelineStageError, WHOOPPipeline


class NotificationPolicyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.output = self.root / 'output'
        self.output.mkdir()
        self.state = self.root / 'state'
        (self.state / 'daily_runs').mkdir(parents=True)
        self.run_date = '2030-01-02'
        self.write(self.state / 'daily_runs' / f'{self.run_date}.json', {
            'claimed_at': '2030-01-02T10:00:00+05:30', 'posted_at': '2030-01-02T10:12:03+05:30',
            'fallback_trigger_stage': 'Video Generation'})
        for guard in (patch('notifier.get_state_root', return_value=self.state),
                      patch.object(notifier.Notifier, '_instance', None)):
            guard.start(); self.addCleanup(guard.stop)
        self.notifier = notifier.Notifier()
        self.notifier.notify_errors = self.notifier.notify_success = True

    @staticmethod
    def write(path, data):
        path.write_text(json.dumps(data))

    def test_internal_events_log_once_without_changing_generation_fields(self):
        path = self.output / 'flow_image_diagnostics.json'
        original = {'record_version': 2, 'attempts': [{'number': 1}], 'api_reserved': False,
                    'retrieval': {'attempts': 2, 'deadline': 123}}
        self.write(path, original)
        statuses = ['SAVED_RUN_PENDING_signature'] + [f'FLOW_{stage}_{suffix}'
            for stage in ('IMAGE', 'VIDEO') for suffix in ('RECOVERY_1', 'RECOVERED_1',
            'RECOVERY_UNAVAILABLE_1', 'REPLACEMENT', 'API_COMPLETE', 'IDENTIFIED_NOT_RETRIEVED')]
        with patch('notifier.safe_notify_status') as send, patch('builtins.print') as log:
            for _ in range(2):
                for status in statuses:
                    FlowMediaClient()._notify_transition(path, status, 'internal detail')
        send.assert_not_called()
        self.assertEqual(log.call_count, len(statuses))
        saved = json.loads(path.read_text())
        self.assertTrue(all(saved[k] == v for k, v in original.items()))
        self.assertEqual(set(saved['notification_events']), set(statuses))
        self.assertTrue(all(v['channel'] == 'log' for v in saved['notification_events'].values()))

    def test_paid_and_owner_alerts_remain_immediate_and_persist_delivery_only(self):
        path = self.output / 'flow_image_diagnostics.json'
        statuses = ['FLOW_IMAGE_API_FALLBACK', 'FLOW_VIDEO_API_FALLBACK', 'FLOW_BROWSER_CLEANUP_BLOCKED']
        with patch('notifier.safe_notify_status', return_value=True) as send:
            for _ in range(2):
                for status in statuses:
                    FlowMediaClient()._notify_transition(path, status, 'action needed')
        self.assertEqual(send.call_count, 3)
        saved = json.loads(path.read_text())
        self.assertTrue(all(v['channel'] == 'telegram' for v in saved['notification_events'].values()))
        with patch('notifier.safe_notify_status', side_effect=[False, True]) as send:
            for _ in range(2):
                FlowMediaClient()._notify_transition(path, 'NEW_ACTION', 'still needed')
        self.assertEqual(send.call_count, 2)

    def test_posted_keeps_document_link_and_truthful_summary(self):
        for stage in ('image', 'video'):
            self.write(self.output / f'flow_{stage}_diagnostics.json', {
                'record_version': 2, 'provider': 'flow', 'notification_events': {
                    'FLOW_IMAGE_RECOVERED_1': {'channel': 'log'}} if stage == 'image' else {}})
        self.write(self.output / 'flow_video_attempt_1.upload.json', {'state': 'recovered'})
        attempts = [{'submission': 'not_attempted'}, {'submission': 'forwarded'}]
        with patch.object(FlowMediaClient, '_daily_attempts', return_value=attempts), \
                patch.object(self.notifier, '_send_document', return_value=True) as document, \
                patch.object(self.notifier, '_send_message', return_value=True) as message:
            self.assertTrue(self.notifier.notify_success_posted(
                self.run_date, self.output / 'card_final.mp4', 'https://www.instagram.com/p/fixture/'))
        document.assert_called_once()
        text = message.call_args.args[0]
        for expected in ('Posted to Instagram', '12m03s', 'Flow', 'exact image recovered',
                         'exact upload recovered', 'replacements 0', 'MEDIA API: none recorded',
                         'https://www.instagram.com/p/fixture/'):
            self.assertIn(expected, text)

    def test_api_use_replacement_and_uncertain_reservation_are_distinct(self):
        self.write(self.output / 'flow_image_diagnostics.json', {'provider': 'google_api'})
        self.write(self.output / 'flow_video_diagnostics.json', {'provider': 'flow', 'api_reserved': True})
        with patch.object(FlowMediaClient, '_daily_attempts', return_value=[
                {'submission': 'forwarded'}, {'submission': 'forwarded'}]):
            summary = self.notifier._post_summary(self.run_date, self.output)
        self.assertIn('image used', summary)
        self.assertIn('video requested; acceptance/charge unconfirmed', summary)
        self.assertIn('replacements 2', summary)

    def test_emergency_and_missing_diagnostics_still_deliver(self):
        with patch.object(self.notifier, '_send_document', return_value=True), \
                patch.object(self.notifier, '_send_message', return_value=True) as message:
            self.assertTrue(self.notifier.notify_emergency_fallback_posted(
                self.run_date, self.output / 'card_final.mp4', 'https://www.instagram.com/p/fixture/',
                'error_404_v1'))
        text = message.call_args.args[0]
        self.assertIn('Emergency/404 error_404_v1', text)
        self.assertIn('trigger: Video Generation', text)
        self.write(self.output / 'flow_image_diagnostics.json', {'provider': 'flow'})
        self.write(self.output / 'flow_video_diagnostics.json', {'provider': 'flow'})
        with patch.object(FlowMediaClient, '_daily_attempts', side_effect=ValueError('invalid markers')):
            self.assertIn('replacements unavailable', self.notifier._post_summary(self.run_date, self.output))
        with patch.object(self.notifier, '_post_summary', side_effect=ValueError('corrupt evidence')), \
                patch.object(self.notifier, '_send_document', return_value=True), \
                patch.object(self.notifier, '_send_message', return_value=True) as message:
            self.assertTrue(self.notifier.notify_success_posted(
                self.run_date, self.output / 'card_final.mp4', 'https://www.instagram.com/p/fixture/'))
        self.assertIn('summary unavailable', message.call_args.args[0])

    def test_summary_uses_durable_worker_and_manual_evidence(self):
        self.write(self.output / 'flow_image_diagnostics.json', {'provider': 'flow',
            'recovery_workers': {'attempts': {'flow_image_attempt_1': {'invocations': 3}}}})
        with patch.object(FlowMediaClient, '_daily_attempts', return_value=[]):
            self.assertIn('image recovery workers used', self.notifier._post_summary(self.run_date, self.output))
        (self.output / 'flow_image_diagnostics.json').unlink()
        self.write(self.output / 'manual_session.json', {'status': 'COMPLETED'})
        self.assertNotIn('Manual', self.notifier._post_summary(self.run_date, self.output))
        self.write(self.output / 'instagram_publish_diagnostics.json', {
            'publish_context': {'asset_source': 'manual_telegram'}})
        self.assertTrue(self.notifier._post_summary(self.run_date, self.output).startswith('Manual'))
        self.write(self.state / 'daily_runs' / f'{self.run_date}.json', {
            'claimed_at': '2030-01-02T10:30:00+05:30', 'posted_at': '2030-01-02T10:12:03+05:30'})
        self.assertIn('duration unavailable', self.notifier._post_summary(self.run_date, self.output))

    def test_warnings_coalesce_but_manual_validation_keeps_each_response(self):
        with patch.object(self.notifier, '_send_message', return_value=True) as send:
            for _ in range(2):
                self.notifier.notify_warning(self.run_date, 'Portfolio Media', 'delivery failed')
                self.notifier.notify_warning(self.run_date, 'ManualImageValidation', 'resend image')
        self.assertEqual(send.call_count, 3)
        with patch.object(self.notifier, '_send_message', side_effect=[False, True]) as send:
            for _ in range(2):
                self.notifier.notify_warning(self.run_date, 'Portfolio Media', 'new failure')
        self.assertEqual(send.call_count, 2)

    def test_whoop_wait_is_persistently_coalesced_across_slots(self):
        with patch('daily_run_state.get_state_root', return_value=self.state):
            daily = DailyRunStateManager(run_date=self.run_date, timezone_name='Asia/Kolkata', run_token='fixture')
        p = WHOOPPipeline.__new__(WHOOPPipeline)
        p.run_date = self.run_date; p.daily_run = daily
        # Start with no run state; each actual retry acquires and releases its own claim.
        daily.state_path.unlink()
        delivery = Mock()
        delivery.notify_warning.return_value = True
        with patch.object(p, '_stop_heartbeat_thread'), patch.object(p, '_is_terminal_rescue_run', return_value=False), \
                patch.object(p, '_next_cron_slot_ist', side_effect=['10:30 IST', '11:00 IST']), \
                patch('pipeline.get_notifier', return_value=delivery):
            for _ in range(2):
                daily.acquire()
                with self.assertRaises(SystemExit):
                    p._handle_retryable_lookup_not_ready()
        delivery.notify_warning.assert_called_once()
        self.assertIn('10:30 IST', delivery.notify_warning.call_args.kwargs['message'])
        self.assertFalse(daily.claim_path.exists())
        self.assertEqual(daily.current_status(), 'FAILED_RETRYABLE')

    def test_retryable_prompt_status_is_logs_only_and_fatal_rescue_survives(self):
        p = WHOOPPipeline.__new__(WHOOPPipeline)
        p.run_date = self.run_date; p.output_dir = self.output; p.daily_run = Mock()
        with patch.object(p, '_stop_heartbeat_thread'), patch.object(p, '_next_cron_slot_ist', return_value='10:30 IST'), \
                patch('pipeline.get_notifier') as delivery, self.assertRaises(SystemExit):
            p._release_retryable_stage_failure(PipelineStageError('LLM Prompts', 'retryable'))
        delivery.assert_not_called()
        p.daily_run.mark_retryable_failure.assert_called_once()
        p.daily_run.release_claim.assert_called_once()
        p.post_to_instagram = True; p.in_emergency_fallback = False
        with patch.dict(os.environ, {'EMERGENCY_FALLBACK_ENABLED': 'true'}), \
                patch.object(p, '_is_terminal_rescue_run', return_value=True), \
                patch.object(p, '_run_emergency_fallback', return_value=True) as rescue, \
                patch('pipeline.get_notifier') as delivery:
            self.assertTrue(p._handle_runtime_stage_error(PipelineStageError('Flow', 'failed', fallback_eligible=True)))
        rescue.assert_called_once(); delivery.assert_not_called()

    def test_retry_cleanup_failure_still_alerts_for_lookup_and_stage(self):
        for lookup in (True, False):
            with self.subTest(lookup=lookup):
                p = WHOOPPipeline.__new__(WHOOPPipeline)
                p.run_date = self.run_date; p.output_dir = self.output; p.daily_run = Mock()
                p.daily_run.release_claim.side_effect = OSError('ownership cleanup failed')
                p.daily_run.notify_once.side_effect = lambda status, send: send()
                with patch.object(p, '_stop_heartbeat_thread'), \
                        patch.object(p, '_is_terminal_rescue_run', return_value=False), \
                        patch.object(p, '_next_cron_slot_ist', return_value='10:30 IST'), \
                        patch('pipeline.get_notifier'), patch('pipeline.safe_notify_status', return_value=True) as alert:
                    with self.assertRaises(SystemExit):
                        if lookup:
                            p._handle_retryable_lookup_not_ready()
                        else:
                            p._release_retryable_stage_failure(PipelineStageError('LLM Prompts', 'retryable'))
                self.assertEqual(alert.call_args.args[1], 'RETRY_CLEANUP_REQUIRED')

    def test_dry_run_logs_without_sending(self):
        with patch.object(self.notifier, '_send_message') as send:
            self.assertFalse(self.notifier.notify_dry_run_complete(self.run_date, output_dir=self.output))
        send.assert_not_called()

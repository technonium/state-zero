"""Approved Release 1 boundary cases; providers and notification transports are fake."""
import tests
import asyncio
import contextlib
import io
import inspect
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch
from zoneinfo import ZoneInfo

from daily_run_state import DailyRunStateManager
from flow_media_client import FlowMediaClient, FlowCommandError
from google_video_client import GoogleVideoClient
from google_key_router import GoogleAPIError
from instagram_token_manager import InstagramTokenManager, REFRESH_URL
from openrouter_client import OpenRouterClient, OpenRouterTimeoutError, LLMProviderError
from pipeline import WHOOPPipeline, PipelineFatalError
from whoop_token_manager import WhoopTokenPersistenceError
import lookups
import state_zero_models as models
import utils
from ops import instagram_token_healthcheck as health, run_rescue

ROOT = Path(__file__).resolve().parents[1]


class ReleaseOneBoundaryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        guard = patch.dict(os.environ, {'STATE_ZERO_PRIVATE_ROOT': str(self.root)}, clear=True)
        guard.start(); self.addCleanup(guard.stop)

    def pipeline(self, hour=12):
        p = object.__new__(WHOOPPipeline)
        p.run_date, p.post_to_instagram = '2030-01-02', True
        p._now = Mock(return_value=datetime(2030, 1, 2, int(hour), 30 if hour % 1 else 0, tzinfo=ZoneInfo('Asia/Kolkata')))
        p._is_terminal_rescue_run = Mock(return_value=hour >= 14)
        p.daily_run = DailyRunStateManager(run_date=p.run_date, timezone_name='Asia/Kolkata', run_token='test')
        return p

    def test_noon_entry_snapshot_restart_and_intervening_alert(self):
        p = self.pipeline()
        p.daily_run._write_json_atomic(p.daily_run.state_path, {'date':p.run_date,'status':'FAILED_RETRYABLE',
            'last_error_step':'Video','last_error_message':'Saved failure'})
        with patch('pipeline.safe_notify_status', return_value=True) as send:
            p._notify_noon_not_posted()
            p.daily_run.notify_once('OTHER', lambda: True)
            self.pipeline(13)._notify_noon_not_posted()
        send.assert_called_once()
        self.assertIn('Video. Saved failure', send.call_args.args[2])
        self.assertIn('14:00 IST rescue remains scheduled; normal retries continue', send.call_args.args[2])

    def test_noon_conditions_failed_delivery_and_late_owners(self):
        for hour in (12, 12.5, 13, 14):
            with self.subTest(hour=hour):
                p = self.pipeline(hour); p.daily_run.state_path.unlink(missing_ok=True)
                with patch('pipeline.safe_notify_status', side_effect=[False, True]) as send:
                    p._notify_noon_not_posted(); p._notify_noon_not_posted(); p._notify_noon_not_posted()
                self.assertEqual(send.call_count, 2)
                self.assertIn('unavailable', send.call_args.args[2])
                self.assertEqual('rescue is running now' in send.call_args.args[2], hour == 14)
        p = self.pipeline(11)
        with patch('pipeline.safe_notify_status') as send:
            p._notify_noon_not_posted()
            p._now.return_value = datetime(2030,1,2,12,tzinfo=ZoneInfo('Asia/Kolkata'))
            p.daily_run.mark_posted_after_publish(post_id='123',permalink=None,note='test')
            p._notify_noon_not_posted()
        send.assert_not_called()

    def test_noon_pending_and_manual_takeover_copy(self):
        p = self.pipeline()
        p.daily_run._write_json_atomic(p.daily_run.state_path, {'date':p.run_date,'status':'FAILED_RETRYABLE',
            'last_error_step':'WHOOP','last_error_message':'WHOOP not ready'})
        with patch('pipeline.safe_notify_status',return_value=True) as send:
            p._notify_noon_not_posted()
        self.assertIn('WHOOP not ready', send.call_args.args[2])
        self.assertIn('Manual deadline reached before assets received. Running automatic generation.',
            inspect.getsource(WHOOPPipeline.step_7_9_manual_or_fallback))
        source=inspect.getsource(WHOOPPipeline._claim_daily_run_or_exit)
        self.assertLess(source.index('_check_publication_before_claim'),source.index('_notify_noon_not_posted'))
        self.assertLess(source.index('_notify_noon_not_posted'),source.index('daily_run.acquire'))

    def test_graph_resolver_and_callers_oauth_unchanged(self):
        for value, expected in [(None,'v25.0'),('', 'v25.0'),('  ', 'v25.0'),(' v24.0 ', 'v24.0')]:
            with patch.dict(os.environ, {} if value is None else {'INSTAGRAM_GRAPH_API_VERSION':value},clear=True):
                self.assertEqual(utils.instagram_graph_api_version(),expected)
        self.assertEqual(REFRESH_URL,'https://graph.instagram.com/refresh_access_token')
        for rel in ('src/scripts/instagram_token_manager.py','src/scripts/instagram_poster.py','ops/instagram_publication_report.py'):
            self.assertIn('instagram_graph_api_version()', (ROOT/rel).read_text())
        manager=InstagramTokenManager(); manager.user_id='123'
        response=Mock(status_code=200)
        with patch.dict(os.environ, {'INSTAGRAM_GRAPH_API_VERSION':'v25.0'}), patch('instagram_token_manager.httpx.Client') as client:
            client.return_value.__enter__.return_value.get.return_value=response
            manager._validate_token_value('synthetic-token')
            self.assertEqual(client.return_value.__enter__.return_value.get.call_args.args[0],'https://graph.facebook.com/v25.0/123')

    def test_scored_percentages_reject_invalid_and_keep_zero(self):
        for field in ('recovery_score','sleep_performance_percentage'):
            for score in ({},None,[],{field:None},{field:'50'},{field:True},{field:float('nan')},
                          {field:float('inf')},{field:-1},{field:101}):
                with self.subTest(field=field,score=score), self.assertRaises(lookups.WhoopDailyDataNotReady):
                    lookups._scored_percentage({'score_state':'SCORED','score':score},field)
            for value in (0,50.5,100):
                self.assertEqual(lookups._scored_percentage({'score':{field:value}},field),value)

    def test_pending_scores_do_not_create_frozen_outputs(self):
        with patch('lookups.WHOOPClient') as client:
            client.return_value.local_tz=ZoneInfo('Asia/Kolkata')
            client.return_value.get_last_sleep=AsyncMock(return_value={'score_state':'SCORED','score':{}})
            with self.assertRaises(lookups.WhoopDailyDataNotReady):
                asyncio.run(lookups._fetch_whoop_data())
        self.assertFalse(list(self.root.rglob('daily_data.json')))
        self.assertEqual(lookups.LOOKUP_EXIT_WHOOP_NOT_READY,2)

    def test_persistence_exit_and_direct_revalidation_are_fatal(self):
        p = self.pipeline(); p.base_dir=ROOT; p.output_dir=self.root
        p._set_heartbeat_context=Mock()
        with patch('pipeline.subprocess.run',return_value=SimpleNamespace(returncode=5,stdout='',stderr='')):
            with self.assertRaises(PipelineFatalError): p.step_2_3_lookups()
        p._daily_data_has_whoop_metrics=Mock(return_value=True)
        p._whoop_revalidation_enabled=Mock(return_value=True)
        p._load_whoop_snapshot=Mock(return_value={})
        p._fetch_fresh_whoop_snapshot=Mock(side_effect=WhoopTokenPersistenceError('save failed'))
        with self.assertRaises(PipelineFatalError):
            p._revalidate_whoop_checkpoint({'recovery_pct':50},checkpoint='before_render')

    def test_unknown_expiry_warns_daily_and_retries_failed_send(self):
        manager=Mock(auto_refresh_mode='off'); manager.inspect_token_health.return_value={'valid':True,'days_to_expiry':None}
        send=Mock(); send.notify_warning.side_effect=[False,True]
        with patch.object(health,'get_instagram_token_manager',return_value=manager), \
                patch.object(health,'get_notifier',return_value=send), \
                patch.object(health,'get_pipeline_run_date_str',return_value='2030-01-02'):
            for _ in range(3): health.main()
        self.assertEqual(send.notify_warning.call_count,2)
        send.notify_status.assert_not_called()
        self.assertIn('expiry is unknown',send.notify_warning.call_args.kwargs['message'])

    def test_rescue_unexpected_failures_alert_once_nonzero_and_keep_child_codes(self):
        for error in (ImportError('bootstrap'),OSError('lock'),RuntimeError('launch')):
            with patch.object(run_rescue,'_run',side_effect=error),patch('notifier.safe_notify_status') as send:
                self.assertEqual(run_rescue.main(),1);send.assert_called_once()
        with patch.object(run_rescue,'_run',side_effect=RuntimeError('launch')), \
                patch('notifier.safe_notify_status',side_effect=RuntimeError('delivery')):
            self.assertEqual(run_rescue.main(),1)
        lock=Mock(); lock.fileno.return_value=99; lock.acquired_at=123
        owner=Mock(); owner.__enter__=Mock(return_value=lock); owner.__exit__=Mock(return_value=False)
        with patch.dict(os.environ,{'DISPLAY':':99'}), patch('utils.execution_lock',return_value=owner), \
                patch('ops.run_rescue.subprocess.run',side_effect=OSError('child launch')), \
                patch('notifier.safe_notify_status') as send:
            self.assertEqual(run_rescue.main(),1)
            owner.__exit__.assert_called_once(); send.assert_called_once()
        for code in (0,1,75):
            with patch.object(run_rescue,'_run',return_value=code),patch('notifier.safe_notify_status') as send:
                self.assertEqual(run_rescue.main(),code);send.assert_not_called()
        with patch.object(run_rescue,'_run',side_effect=KeyboardInterrupt),self.assertRaises(KeyboardInterrupt): run_rescue.main()

    def download(self, url, responses):
        client=object.__new__(GoogleVideoClient)
        with patch('google_video_client.requests.get',side_effect=responses) as get:
            client._download_video(url,'synthetic-key',self.root/'video.mp4')
        return get

    def test_download_allowed_host(self):
        get=self.download('https://generativelanguage.googleapis.com/video',[Mock(status_code=200,content=b'video')])
        self.assertFalse(get.call_args.kwargs['allow_redirects'])
        self.assertEqual((self.root/'video.mp4').read_bytes(),b'video')

    def test_download_allowed_redirect(self):
        get=self.download('https://generativelanguage.googleapis.com/start',[
            Mock(status_code=302,headers={'Location':'https://storage.googleapis.com/clip'}),Mock(status_code=200,content=b'video')])
        self.assertEqual(get.call_count,2)

    def test_download_off_domain_redirect_uses_no_key(self):
        get=self.download('https://generativelanguage.googleapis.com/start',[
            Mock(status_code=302,headers={'Location':'https://signed.example/clip?signature=fixture'}),
            Mock(status_code=200,content=b'video')])
        self.assertEqual(get.call_args_list[1].kwargs['headers'],{})

    def test_download_lookalike_host_gets_no_key(self):
        get=self.download('https://googleapis.com.evil.example/clip',[Mock(status_code=200,content=b'video')])
        self.assertEqual(get.call_args.kwargs['headers'],{})

    def test_download_http_downgrade_rejected(self):
        with self.assertRaises(GoogleAPIError):
            self.download('https://generativelanguage.googleapis.com/start',[Mock(status_code=302,headers={'Location':'http://storage.googleapis.com/clip'})])

    def test_download_key_never_sent_to_off_domain_host(self):
        with patch('google_video_client.requests.get',side_effect=[
                Mock(status_code=302,headers={'Location':'https://signed.example'}),
                Mock(status_code=200,content=b'video')]) as get:
            object.__new__(GoogleVideoClient)._download_video('https://storage.googleapis.com/start','synthetic-key',self.root/'clip')
        self.assertEqual(get.call_count,2)
        self.assertEqual(get.call_args_list[0].kwargs['headers'],{'x-goog-api-key':'synthetic-key'})
        self.assertEqual(get.call_args_list[1].kwargs['headers'],{})

    def test_instagram_logs_hide_tokens_and_raw_refresh_responses(self):
        manager=InstagramTokenManager(); manager.access_token='synthetic-old-token-prefix-value'
        manager._mark_refresh_attempt_started=Mock();manager._record_refresh_attempt=Mock()
        for response in (Mock(status_code=400,text='raw secret',json=Mock(return_value={'error':{'code':190,'error_subcode':460,'message':'raw secret'}})),
                         Mock(status_code=200,json=Mock(return_value={'unexpected':'raw secret'}))):
            manager._perform_refresh_request=AsyncMock(return_value=response)
            with self.assertLogs('instagram_token_manager',level='ERROR') as logs:
                self.assertFalse(asyncio.run(manager._refresh_token_async()))
            text=' '.join(logs.output);self.assertNotIn('raw secret',text);self.assertNotIn(manager.access_token[:20],text)
        manager._perform_refresh_request=AsyncMock(return_value=Mock(status_code=200,json=Mock(return_value={
            'access_token':'synthetic-new-token-prefix-value','expires_in':3600})))
        manager._inspect_token_health_for_value=Mock(return_value={'valid':True})
        with self.assertLogs('instagram_token_manager',level='INFO') as logs:
            self.assertTrue(asyncio.run(manager._refresh_token_async()))
        self.assertNotIn('synthetic-new-token', ' '.join(logs.output))
        self.assertNotIn('synthetic-old-token', ' '.join(logs.output))
        response=Mock(status_code=400,json=Mock(return_value={'error':{'code':190,'error_subcode':460}}))
        self.assertIn('code=190',manager._response_error(response))
        with patch('instagram_token_manager.httpx.Client',side_effect=RuntimeError('https://host/path?access_token=synthetic-secret')):
            manager.user_id='123'; self.assertNotIn('synthetic-secret',manager._validate_token_value('token')[1])

    def test_config_hygiene_and_production_settings(self):
        from ops.check_repo_hygiene import classify_path
        self.assertIsNotNone(classify_path('tmp/private.md'))
        self.assertIsNone(classify_path('src/assets/example.png'))
        self.assertIn('tmp/',(ROOT/'.gitignore').read_text())
        self.assertIn('IG/',(ROOT/'.dockerignore').read_text())
        example=(ROOT/'.env.example').read_text()
        keys=[line.lstrip('# ').split('=',1)[0] for line in example.splitlines() if '=' in line and line.lstrip('# ').split('=',1)[0].isupper()]
        self.assertEqual(len(keys),len(set(keys)))
        for key in ('INSTAGRAM_GRAPH_API_VERSION','WHOOP_QUIET_UPDATE_MINUTES','FLOW_PREFLIGHT_PROJECT_ID','PIPELINE_MANUAL_DEADLINE_LOCAL'):
            self.assertIn(key,keys)
        self.assertNotIn('FLOW_SHADOW_',example)
        self.assertFalse((ROOT/'ops/RELEASE_3B_CUTOVER.md').exists())

    def test_model_golden_contract_and_application_commands(self):
        self.assertEqual((models.FLOW_IMAGE_LABEL,models.FLOW_IMAGE_ALIAS,models.FLOW_IMAGE_ENUM,models.FLOW_IMAGE_WIRE),
                         ('Nano Banana 2.1','nano2','NARWHAL','BELUGA'))
        self.assertEqual((models.FLOW_VIDEO_LABEL,models.FLOW_VIDEO_ALIAS,models.FLOW_VIDEO_RESULT,models.FLOW_VIDEO_WIRE),
                         ('Veo 3.1 - Fast','veo-fast','veo_3_1_fast','veo_3_1_i2v_s_fast_portrait'))
        self.assertEqual(models.GOOGLE_VIDEO_MODEL,'gemini-omni-1.1-flash')
        self.assertEqual(models.GOOGLE_IMAGE_MODEL,'gemini-nano-banana-2.1')
        client=FlowMediaClient()
        self.assertIn('nano2',client.image_command({},self.root/'art.png'))
        self.assertIn('veo-fast',client.video_command('prompt',self.root/'art.png',self.root/'clip.mp4'))

    def test_patch_and_installed_model_literals_equal_constants_without_app_import(self):
        from gflow_cli.api.transports import migrated_composer as m
        from gflow_cli.api.image import Model
        from gflow_cli.api.video import VideoModel
        self.assertEqual(m.IMAGE_MODEL_MENU_MATCHERS[Model.NARWHAL].contains,models.FLOW_IMAGE_LABEL)
        self.assertEqual(m.VIDEO_MODEL_MENU_LABELS[VideoModel.VEO_3_1_FAST],models.FLOW_VIDEO_LABEL)
        self.assertEqual(Model.NARWHAL.value,models.FLOW_IMAGE_ENUM)
        self.assertEqual(VideoModel.VEO_3_1_FAST.value,models.FLOW_VIDEO_RESULT)
        patches='\n'.join(p.read_text() for p in (ROOT/'ops').glob('*.patch'))
        import re
        additions='\n'.join(line[1:] for line in patches.splitlines() if line.startswith('+') and not line.startswith('+++'))
        self.assertEqual(re.search(r'ImageModel\.NARWHAL: ModelMenuMatcher\("([^"]+)"',additions)[1],models.FLOW_IMAGE_LABEL)
        self.assertEqual(re.search(r'accepted = \{"([^"]+)"\} if model is ImageModel\.NARWHAL',additions)[1],models.FLOW_IMAGE_WIRE)
        self.assertEqual(re.search(r'accepted = \{"([^"]+)"\} if model is ImageModel\.NARWHAL',inspect.getsource(m))[1],models.FLOW_IMAGE_WIRE)
        self.assertNotIn('state_zero_models',patches)
        self.assertNotIn('state_zero_models',inspect.getsource(m))

    def test_distinct_model_absence_alert_preserves_failure_category(self):
        for stage in ('image','video'):
            marker=self.root/f'flow_{stage}_attempt_1.submit'
            result=SimpleNamespace(returncode=11,stdout=json.dumps({'error':{'class':'ConfigurationError','detail':'expected_model_not_offered: test'}}))
            with patch('flow_media_client.run_bounded',return_value=result), \
                    patch.object(FlowMediaClient,'_check_browser_ownership'), \
                    patch.object(FlowMediaClient,'_notify_transition') as send:
                with self.assertRaises(FlowCommandError) as exc:
                    FlowMediaClient._run_json(['gflow',stage],10,marker)
            self.assertEqual(exc.exception.category,'pre_submit_terminal')
            self.assertFalse(marker.exists())
            self.assertIn('Flow model changed (',send.call_args.args[2])
            result.stdout=json.dumps({'error':{'class':'ConfigurationError','detail':'menu unreadable'}})
            with patch('flow_media_client.run_bounded',return_value=result),patch.object(FlowMediaClient,'_check_browser_ownership'), \
                    patch.object(FlowMediaClient,'_notify_transition') as send:
                with self.assertRaises(FlowCommandError): FlowMediaClient._run_json(['gflow',stage],10,marker)
            send.assert_not_called()


class LLMTimingTests(unittest.TestCase):
    def test_one_log_per_logical_call_success_retry_and_fallback(self):
        for responses, fallback, provider, outcome in [(['ok'],None,'openrouter','success'),
            ([OpenRouterTimeoutError('timeout'),'ok'],None,'openrouter','success'),
            ([OpenRouterTimeoutError('timeout')]*2,'gemini','gemini','success'),
            ([OpenRouterTimeoutError('timeout')]*2,LLMProviderError('failed'),'none','error')]:
            client=OpenRouterClient(api_key='fake',fallback_api_key='fake-fallback')
            output=io.StringIO()
            with patch.dict(os.environ,{'OPENROUTER_CALL_DEADLINE_SECONDS':'200'}), \
                    patch.object(client,'_call_openrouter',side_effect=responses), \
                    patch.object(client,'_call_google_gemini',side_effect=fallback if isinstance(fallback,Exception) else None,return_value=fallback), \
                    contextlib.redirect_stdout(output):
                if outcome=='error':
                    with self.assertRaises(LLMProviderError): client.generate('synthetic prompt')
                else: client.generate('synthetic prompt')
            lines=[line for line in output.getvalue().splitlines() if line.startswith('LLM_CALL')]
            self.assertEqual(len(lines),1);self.assertIn('provider='+provider,lines[0]);self.assertIn('outcome='+outcome,lines[0])
            self.assertNotIn('synthetic prompt',lines[0])

    def test_direct_gemini_single_measurement_and_unchanged_arguments(self):
        from prompts import PromptOrchestrator
        p=object.__new__(PromptOrchestrator);p.google_api_key='fake';p.use_openrouter=False
        p._prompt_seconds_remaining=Mock(return_value=1000)
        output=io.StringIO()
        with patch('prompts.call_google_gemini_generate_content',return_value='ok') as call,contextlib.redirect_stdout(output):
            self.assertEqual(p.call_llm('synthetic'),'ok')
        self.assertEqual(output.getvalue().count('LLM_CALL'),1)
        self.assertEqual(call.call_args.kwargs['timeout'],90)
        self.assertEqual(call.call_args.kwargs['thinking_budget'],8000)
        self.assertEqual(call.call_args.kwargs['max_output_tokens'],12000)

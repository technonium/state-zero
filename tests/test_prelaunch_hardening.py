"""Launch regressions: observed progress, local work, and publication evidence."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
from flow_media_client import FlowMediaClient, FlowCommandError

try:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer
except ImportError:
    MigratedComposer = None


@unittest.skipIf(MigratedComposer is None, 'Requires the pinned ARM64 runtime')
class VideoPollTests(unittest.IsolatedAsyncioTestCase):
    async def test_unreadable_poll_does_not_abandon_accepted_generation(self):
        module = 'gflow_cli.api.transports.migrated_composer'
        callbacks = {}; routes = {}
        page = MagicMock()
        page.on.side_effect = lambda name, callback: callbacks.update({name: callback})
        page.route = AsyncMock(side_effect=lambda pattern, callback: routes.update(guard=callback))
        page.unroute = AsyncMock(side_effect=RuntimeError("closed page"))
        button = page.locator.return_value.filter.return_value.first
        button.count = AsyncMock(return_value=1); button.is_enabled = AsyncMock(return_value=True)
        composer = object.__new__(MigratedComposer)
        composer._shadow_video_context = {'response': {'detail': 'body unavailable'}}
        pending = SimpleNamespace(project_id='project', media_id='media', workflow_id='workflow',
            status=1, size_bytes=0, is_done=False, is_failed=False, video_url=None)
        done = SimpleNamespace(**(vars(pending) | dict(status=3, size_bytes=123, is_done=True, video_url='original')))
        request = MagicMock(url='https://flow.google.com/batchexecute?rpcids=eb1hJf', post_data='submit')
        async def click(*args, **kwargs):
            await routes['guard'](MagicMock(), request)
            for rpc, text, error in [('eb1hJf','submit',None), ('jwpduf','poll',RuntimeError('No data found')), ('jwpduf','complete',None)]:
                req = request if rpc=='eb1hJf' else MagicMock(post_data='workflow media')
                response = MagicMock(url=f'https://flow.google.com/batchexecute?rpcids={rpc}', request=req, status=200)
                response.text = AsyncMock(return_value=text, side_effect=error)
                await callbacks['response'](response)
        with patch.object(MigratedComposer, '_click', new=AsyncMock(side_effect=click)) as submit, \
                patch(module+'._mark_shadow_submit_attempt'), patch(module+'._submit_refusal', return_value=None), \
                patch(module+'.parse_frames', side_effect=lambda text: [('eb1hJf' if text=='submit' else 'jwpduf',text)]), \
                patch('gflow_cli.api.transports.batchexecute._generation_records', side_effect=lambda rpc,text: [done if text=='complete' else pending]), \
                patch('flow_readback.video_submit_rpc', return_value='eb1hJf'), \
                patch('flow_readback.guard_video', new=AsyncMock(return_value=None)), \
                patch('flow_readback.video_receipt'), patch('flow_readback.note_video_response'), patch('flow_readback.note_video_lifecycle'), patch('flow_readback.note_response'), \
                patch.dict(os.environ, {'GFLOW_SHADOW_SUBMIT_MARKER':'synthetic.submit'}):
            result = await composer.submit_and_observe(page, poll_timeout_s=1, on_started=None, project_id='project')
        self.assertIs(result, done)
        self.assertEqual(submit.await_count,1)


class TypedFailureTests(unittest.TestCase):
    def test_technical_error_text_cannot_manufacture_a_provider_block(self):
        payload={'status':'error','error':{'class':'TimeoutError','detail':'MODEL response could not be read; CREDIT page transport timeout','retryable':True}}
        with patch('flow_media_client.run_bounded', return_value=subprocess.CompletedProcess([],1,json.dumps(payload),'')):
            with self.assertRaises(FlowCommandError) as caught:
                FlowMediaClient._run_json(['gflow','video'],1)
        self.assertEqual(caught.exception.category,'pre_submit_transient')
        self.assertFalse(FlowMediaClient._stops_replacement(caught.exception))

    def test_generic_publish_4xx_stays_uncertain_until_container_verdict(self):
        from instagram_poster import InstagramPoster, InstagramPublicationUncertain
        with tempfile.TemporaryDirectory() as tmp:
            p=object.__new__(InstagramPoster);p.mock_mode=False;p.access_token='test';p.user_id='account'
            p.base_url='https://example.invalid';p.diagnostics_output_dir=Path(tmp);p.run_date='2026-10-22';p.publish_context=None
            response=MagicMock(status_code=400,headers={});response.json.return_value={'error':{'code':1,'message':'unknown'}}
            with patch('instagram_poster.requests.post',return_value=response), \
                    patch.object(p,'reconcile_publication',side_effect=InstagramPublicationUncertain('publish_media','unknown',{})) as reconcile:
                with self.assertRaises(InstagramPublicationUncertain):p.publish_media('container')
            self.assertEqual(reconcile.call_count,1)
            self.assertEqual(json.loads((Path(tmp)/'instagram_publish_diagnostics.json').read_text())['publication_state'],'uncertain')

class OwnershipTests(unittest.TestCase):
    def test_lock_is_canonical_and_not_in_validation_namespace(self):
        from utils import execution_lock_path
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'STATE_ZERO_PRIVATE_ROOT':tmp,'FLOW_SHADOW_VALIDATION_ID':'isolated'}, clear=False):
            self.assertEqual(execution_lock_path(), Path(tmp)/'runtime/state/pipeline-execution.lock')

    def test_flag_or_wrong_descriptor_cannot_bypass_ownership(self):
        from utils import execution_lock, ExecutionBusy
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'STATE_ZERO_PRIVATE_ROOT':tmp}, clear=False):
            with execution_lock() as lock:
                with patch.dict(os.environ, {'STATE_ZERO_EXECUTION_FD':'999999'}):
                    with self.assertRaises(ValueError):
                        with execution_lock(): pass
                with self.assertRaises(ExecutionBusy):
                    with execution_lock(): pass
                self.assertFalse(os.get_inheritable(lock.fileno()))

    def test_proven_lock_reclaims_fresh_orphan_without_clearing_evidence(self):
        from utils import execution_lock
        from daily_run_state import DailyRunStateManager
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'STATE_ZERO_PRIVATE_ROOT':tmp}, clear=False):
            old=DailyRunStateManager(run_date='2026-10-22',timezone_name='Asia/Kolkata',run_token='old')
            old.acquire();old.update_status(status='POSTING',extra={'submission_count':1})
            new=DailyRunStateManager(run_date='2026-10-22',timezone_name='Asia/Kolkata',run_token='new')
            with execution_lock() as lock:
                decision,state=new.acquire(execution_fd=lock.fileno())
            self.assertEqual(decision,'owner');self.assertEqual(state['submission_count'],1)

class LocalWorkTests(unittest.TestCase):
    def test_optional_tools_and_conversion_use_owned_process_cleanup(self):
        import inspect
        video=inspect.getsource(FlowMediaClient._generate_video)
        image=inspect.getsource(FlowMediaClient._generate_image)
        self.assertNotIn('subprocess.run(',video)
        self.assertNotIn('subprocess.run(',image)

    def test_failed_conversion_is_retryable_with_no_remote_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'generated_video.mp4';diag=root/'flow_video_diagnostics.json'
            original=root/'original.mp4';original.write_bytes(b'validated fixture')
            payload={'succeeded':True,'local_path':str(original),'media_id':'exact','request':{}}
            diag.write_text(json.dumps({'record_version':2, 'failure':{'category':'local_processing_failed'},'result':payload}))
            client=FlowMediaClient()
            with patch.object(client,'_daily_attempts',return_value=[{'number':1}]), \
                    patch.object(client,'_local_result',return_value=payload), \
                    patch.object(client,'_recover_pending') as remote, \
                    patch.object(client,'_finish_daily',return_value=out), \
                    patch.object(client,'_generation_seconds',return_value=-1):
                generation=MagicMock()
                self.assertEqual(client._generate_daily('video',{},out,generation,None),out)
                generation.assert_called_once_with(payload);remote.assert_not_called()

class PromptResumeTests(unittest.TestCase):
    def orchestrator(self, root):
        from prompts import PromptOrchestrator
        p=object.__new__(PromptOrchestrator);p.output_dir=root;p.templates_dir=root/'templates';p.templates_dir.mkdir(exist_ok=True)
        (p.templates_dir/'creature.md').write_text('template')
        p.latest_creature_fragment='';p.latest_creature_fragment_why_unique=''
        p._prompt_progress={'version':1,'stages':{}};p._prompt_base={'daily':{'date':'2026-10-22'}}
        return p

    def test_committed_stage_restores_fragment_and_detects_modified_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=self.orchestrator(Path(tmp));calls=[]
            def generate():
                calls.append(1);p.latest_creature_fragment='crown';p.latest_creature_fragment_why_unique='unique'
                p.save_output('creature_selected.txt','chosen');return 'chosen'
            p._run_prompt_stage('creature',[],generate)
            p.latest_creature_fragment=''
            p._run_prompt_stage('creature',[],generate)
            self.assertEqual(p.latest_creature_fragment,'crown');self.assertEqual(len(calls),1)
            (p.output_dir/'creature_selected.txt').write_text('changed')
            p._run_prompt_stage('creature',[],generate);self.assertEqual(len(calls),2)

    def test_failed_stage_does_not_certify_partial_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=self.orchestrator(Path(tmp))
            def fail():
                p.save_output('creature_selected.txt','partial');raise TimeoutError()
            with self.assertRaises(TimeoutError):p._run_prompt_stage('creature',[],fail)
            self.assertNotIn('creature',p._prompt_progress['stages'])

class RescueTests(unittest.TestCase):
    def test_rescue_budget_does_not_reopen_generation_window(self):
        import time
        from utils import pipeline_seconds_remaining
        from datetime import datetime, timezone
        with patch('utils.get_pipeline_deadline',return_value=(datetime.fromtimestamp(time.time()-1,timezone.utc),'test')), \
                patch.dict(os.environ,{'STATE_ZERO_LOCAL_ONLY_DEADLINE':str(time.time()+300)}):
            self.assertGreater(pipeline_seconds_remaining(),290)
            with self.assertRaises(TimeoutError):pipeline_seconds_remaining(generation=True)

    def test_local_only_mode_cannot_recover_remotely_or_select_api(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'STATE_ZERO_LOCAL_ONLY_DEADLINE':'1'}):
            out=Path(tmp)/'generated_video.mp4'
            out.with_name('flow_video_diagnostics.json').write_text('{"record_version":2}')
            c=FlowMediaClient()
            with patch.object(c,'_daily_attempts',return_value=[]),patch.object(c,'_recover_pending') as remote, \
                    patch('flow_media_client.GoogleVideoClient') as api,patch.object(c,'_generation_seconds',return_value=7200):
                with self.assertRaisesRegex(FlowCommandError,'local_completion_unavailable'):c._generate_daily('video',{},out,MagicMock(),None)
                remote.assert_not_called();api.assert_not_called()

class CheckpointCombinationsTests(unittest.TestCase):
    orchestrator = PromptResumeTests.orchestrator
    def test_timeout_after_each_checkpoint_reuses_only_committed_stages(self):
        names=['interpretation','creature','environment','image','title','scene','video']
        dependencies={'interpretation':[],'creature':['interpretation'],'environment':['interpretation'],
            'image':['creature','environment'],'title':['environment'],'scene':['image','environment'],'video':['image','environment']}
        for stopped in names:
            with self.subTest(stopped=stopped),tempfile.TemporaryDirectory() as tmp:
                p=self.orchestrator(Path(tmp));calls={n:0 for n in names};failed=[False]
                for filename in ('interpretation','environment','json_builder','title_builder','scene_description_builder','video'):
                    (p.templates_dir/(filename+'.md')).write_text(filename)
                def generate(name):
                    calls[name]+=1;p.save_output(name+'.txt',name)
                    if name==stopped and not failed[0]:
                        failed[0]=True;raise TimeoutError()
                    return name
                for _ in range(2):
                    try:
                        for name in names:p._run_prompt_stage(name,dependencies[name],lambda n=name:generate(n))
                    except TimeoutError:pass
                self.assertEqual(calls,{n:2 if n==stopped else 1 for n in names})
                self.assertEqual((Path(tmp)/'prompt_progress.json').stat().st_mode&0o777,0o600)

    def test_reused_environment_does_not_overwrite_new_creature_fragment(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=self.orchestrator(Path(tmp));(p.templates_dir/'environment.md').write_text('template')
            p.latest_creature_fragment='old'
            def environment():p.save_output('environment.txt','chosen');return 'chosen'
            p._run_prompt_stage('environment',[],environment)
            p.latest_creature_fragment='new'
            p._run_prompt_stage('environment',[],environment)
            self.assertEqual(p.latest_creature_fragment,'new')


class PublicationReportTests(unittest.TestCase):
    def test_read_only_report_keeps_expired_deadline_and_receipt_unchanged(self):
        from ops.instagram_publication_report import report
        import hashlib
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'STATE_ZERO_PRIVATE_ROOT':tmp,'INSTAGRAM_ACCESS_TOKEN':'synthetic'}):
            root=Path(tmp)/'runtime/output/2026-10-22';root.mkdir(parents=True)
            receipt=root/'instagram_publish_diagnostics.json'
            receipt.write_text(json.dumps({'publication_state':'uncertain','creation_id':'123','reconciliation_deadline':1}))
            before=hashlib.sha256(receipt.read_bytes()).hexdigest()
            response=MagicMock(status_code=200);response.json.return_value={'status_code':'PUBLISHED'}
            with patch('ops.instagram_publication_report.requests.get',return_value=response) as get:
                value=report('2026-10-22')
            self.assertEqual(value['container_status'],'PUBLISHED');self.assertEqual(value['reconciliation_deadline'],1)
            self.assertEqual(hashlib.sha256(receipt.read_bytes()).hexdigest(),before);get.assert_called_once()
            self.assertNotIn('synthetic',json.dumps(value))


@unittest.skipIf(MigratedComposer is None,'Requires the pinned ARM64 runtime')
class OptionalDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_editor_is_rejected_before_download_controls(self):
        from ops.flow_video_upscale import download_existing
        from flow_readback import ReadbackIdentityError
        client=MagicMock();client.__aenter__=AsyncMock(return_value=client);client.__aexit__=AsyncMock(return_value=False)
        client.page.goto=AsyncMock();client.page.url='https://flow.google.com/project/project/edit/wrong'
        with tempfile.TemporaryDirectory() as tmp, \
                patch('gflow_cli.api.client.FlowApiClient',return_value=client), \
                patch('flow_readback.snapshot',new=AsyncMock(return_value={})), \
                patch('flow_readback.select_video',return_value={'workflow_id':'workflow'}):
            original=Path(tmp)/'original.mp4';original.write_bytes(b'valid original')
            with self.assertRaises(ReadbackIdentityError):
                await download_existing('project','media',Path(tmp)/'candidate.mp4',workflow_id='workflow')
            self.assertEqual(original.read_bytes(),b'valid original')
        client.page.get_by_role.assert_not_called()

    async def test_other_clip_transfer_is_rejected_even_if_media_could_decode(self):
        from ops.flow_video_upscale import download_existing
        client=MagicMock();client.__aenter__=AsyncMock(return_value=client);client.__aexit__=AsyncMock(return_value=False)
        page=client.page;page.goto=AsyncMock();page.url='https://flow.google.com/project/project/edit/media'
        page.locator.return_value.evaluate_all=AsyncMock(return_value=['https://flow-content.google/video/workflow'])
        button=MagicMock();button.count=AsyncMock(return_value=1);button.click=AsyncMock()
        choice=MagicMock();choice.count=AsyncMock(return_value=1);choice.is_visible=AsyncMock(return_value=True)
        choice.is_enabled=AsyncMock(return_value=True);choice.inner_text=AsyncMock(return_value='1080p');choice.click=AsyncMock()
        page.get_by_role.side_effect=[button,choice]
        download=MagicMock(url='https://flow-content.google/video/other-workflow');download.save_as=AsyncMock()
        future=asyncio.get_running_loop().create_future();future.set_result(download)
        event=MagicMock(value=future)
        page.expect_download.return_value.__aenter__=AsyncMock(return_value=event)
        page.expect_download.return_value.__aexit__=AsyncMock()
        with tempfile.TemporaryDirectory() as tmp,patch('gflow_cli.api.client.FlowApiClient',return_value=client), \
                patch('flow_readback.snapshot',new=AsyncMock(return_value={})), \
                patch('flow_readback.select_video',return_value={'workflow_id':'workflow'}):
            result=await download_existing('project','media',Path(tmp)/'candidate.mp4',workflow_id='workflow')
        self.assertEqual(result,'transfer_identity_unverified');download.save_as.assert_not_awaited()

class AdditionalBoundariesTests(unittest.TestCase):
    def test_ownership_descriptor_does_not_reach_provider_descendants(self):
        from utils import execution_lock,run_bounded
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'STATE_ZERO_PRIVATE_ROOT':tmp}):
            with execution_lock() as lock:
                script='import os,sys\ntry: os.fstat(int(sys.argv[1]))\nexcept OSError: print("closed")\nelse: print("inherited")'
                result=run_bounded([sys.executable,'-c',script,str(lock.fileno())],timeout=5)
                self.assertEqual(result.stdout.strip(),'closed')

    def test_expired_rescue_budget_cannot_write_publish_intent(self):
        from instagram_poster import InstagramPoster,InstagramPublishDiagnosticsError
        p=object.__new__(InstagramPoster);p.mock_mode=False;p.diagnostics_output_dir=None;p.run_date=None
        with patch.dict(os.environ,{'STATE_ZERO_LOCAL_ONLY_DEADLINE':'1'}),patch.object(p,'_persist_diagnostics') as save, \
                patch('instagram_poster.requests.post') as post:
            with self.assertRaises(InstagramPublishDiagnosticsError):p.publish_media('123')
        save.assert_not_called();post.assert_not_called()

    def test_changed_template_or_daily_identity_invalidates_checkpoint(self):
        for changed in ('template','whoop_identity','settings'):
            with self.subTest(changed=changed),tempfile.TemporaryDirectory() as tmp:
                p=PromptResumeTests.orchestrator(self,Path(tmp));calls=[]
                def generate():calls.append(1);p.save_output('creature.txt','chosen');return 'chosen'
                p._run_prompt_stage('creature',[],generate)
                if changed=='template':(p.templates_dir/'creature.md').write_text('changed')
                else:p._prompt_base[changed]='changed'
                p._run_prompt_stage('creature',[],generate)
                self.assertEqual(len(calls),2)

class PromptBudgetTests(unittest.TestCase):
    def test_expired_budget_makes_no_provider_call_and_does_not_change_alarm_handler(self):
        import signal
        from openrouter_client import OpenRouterClient
        before=signal.getsignal(signal.SIGALRM)
        c=OpenRouterClient(api_key='synthetic',fallback_api_key='synthetic')
        with patch.dict(os.environ,{'STATE_ZERO_PROMPT_DEADLINE':'1','OPENROUTER_CALL_DEADLINE_SECONDS':'200'}), \
                patch.object(c,'_call_openrouter') as primary,patch.object(c,'_call_google_gemini') as fallback:
            with self.assertRaises(TimeoutError):c.generate('synthetic')
            primary.assert_not_called();fallback.assert_not_called()
        self.assertEqual(signal.getsignal(signal.SIGALRM),before)

    def test_retry_and_fallback_share_absolute_remaining_budget(self):
        from openrouter_client import OpenRouterClient,OpenRouterTimeoutError
        c=OpenRouterClient(api_key='synthetic',fallback_api_key='synthetic');remaining=[3]
        def timeout(*args):remaining[0]=0;raise OpenRouterTimeoutError('synthetic timeout')
        with patch.dict(os.environ,{'STATE_ZERO_PROMPT_DEADLINE':'1003','OPENROUTER_CALL_DEADLINE_SECONDS':'200'}), \
                patch('openrouter_client.time.time',side_effect=lambda:1003-remaining[0]), \
                patch.object(c,'_call_openrouter',side_effect=timeout) as primary, \
                patch.object(c,'_call_google_gemini') as fallback:
            with self.assertRaises(TimeoutError):c.generate('synthetic')
            self.assertEqual(primary.call_count,1);fallback.assert_not_called()

class ManualRescueTests(unittest.TestCase):
    def test_manual_receipt_requires_frozen_inputs_and_detects_changed_bytes(self):
        from pipeline import WHOOPPipeline,PipelineStageError
        with tempfile.TemporaryDirectory() as tmp:
            p=object.__new__(WHOOPPipeline);p.output_dir=Path(tmp);p.session_file=Path(tmp)/'manual_session.json'
            p._validate_image_file=MagicMock();p._validate_video_file=MagicMock()
            image=Path(tmp)/'generated_art.png';image.write_bytes(b'validated')
            (Path(tmp)/'flow_inputs.json').write_text('manifest')
            session={};p._remember_manual_assets(session,{'image':image})
            p.session_file.write_text(json.dumps(session))
            self.assertEqual(p._manual_local_assets(),{'image':image})
            image.write_bytes(b'changed')
            with self.assertRaises(PipelineStageError):p._manual_local_assets()

    def test_legacy_manual_session_does_not_certify_existing_files(self):
        from pipeline import WHOOPPipeline
        with tempfile.TemporaryDirectory() as tmp:
            p=object.__new__(WHOOPPipeline);p.output_dir=Path(tmp);p.session_file=Path(tmp)/'manual_session.json'
            p.session_file.write_text('{}');(Path(tmp)/'generated_art.png').write_bytes(b'existing')
            self.assertEqual(p._manual_local_assets(),{})

@unittest.skipIf(MigratedComposer is None,'Requires the pinned ARM64 runtime')
class DecodedVerdictTests(unittest.TestCase):
    def test_provider_restriction_retains_its_type_across_submission_guard(self):
        from gflow_cli.api.transports.migrated_composer import _raise_after_shadow_submission
        from gflow_cli.errors import ContentPolicyError,InsufficientCreditsError,RecaptchaError,WafRejectionError
        for kind in (ContentPolicyError,InsufficientCreditsError,RecaptchaError,WafRejectionError):
            with self.subTest(kind=kind.__name__),tempfile.TemporaryDirectory() as tmp:
                marker=Path(tmp)/'submit';marker.write_text('{"state":"forwarded"}')
                with patch.dict(os.environ,{'GFLOW_SHADOW_SUBMIT_MARKER':str(marker)}):
                    with self.assertRaises(kind):_raise_after_shadow_submission(kind(detail='decoded verdict'))

    def test_unknown_decoded_refusal_before_submit_is_transient_without_text_keywords(self):
        from gflow_cli.api.transports.migrated_composer import _submit_refusal
        record=SimpleNamespace(reasons=('NEW_PROVIDER_VERDICT',),rpcid='eb1hJf',code=7)
        with patch('gflow_cli.api.transports.migrated_composer.rpc_errors',return_value=[record]):
            verdict=_submit_refusal('private ignored body',('eb1hJf',))
        self.assertIsNotNone(verdict)
        self.assertEqual(type(verdict).__name__,'UnknownFlowRefusalError')
        payload={'status':'error','error':{'class':type(verdict).__name__,'detail':'unclassified'}}
        with patch('flow_media_client.run_bounded',return_value=subprocess.CompletedProcess([],1,json.dumps(payload),'')):
            with self.assertRaises(FlowCommandError) as result:FlowMediaClient._run_json(['gflow','video'],1)
        self.assertEqual(result.exception.category,'pre_submit_transient')

class RescuePublicationCombinationTests(unittest.TestCase):
    def test_local_rescue_with_uncertain_publication_never_calls_emergency(self):
        from pipeline import WHOOPPipeline,PipelineStageError
        import time
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'STATE_ZERO_PRIVATE_ROOT':tmp,'MEDIA_GENERATION_PROVIDER':'flow'}):
            p=object.__new__(WHOOPPipeline);p.output_dir=Path(tmp);p.run_date='2026-10-22'
            p.mode='automatic';p.post_to_instagram=True;p.media_mode='live_vps';p.run_token='synthetic';p.daily_run=MagicMock()
            for name in ('_claim_daily_run_or_exit','_ensure_owner_runtime_dirs','_stop_heartbeat_thread',
                    '_cleanup_non_authoritative_daily_state','step_1_validate','step_1b_validate_instagram_token',
                    '_revalidate_whoop_checkpoint','step_2_3_lookups','step_4_6_prompts','step_7_generate_image',
                    '_run_emergency_fallback','finalize_posted_run','step_10a_render_image','step_10b_render_video'):
                setattr(p,name,MagicMock())
            p._check_flow_submission_state=MagicMock(return_value=True)
            p._is_terminal_rescue_run=MagicMock(return_value=True)
            p._load_required_json=MagicMock(return_value={})
            p._load_required_text_outputs=MagicMock(return_value=('blend','creature','environment'))
            p._build_caption_or_raise=MagicMock(return_value='synthetic')
            p._prepare_instagram_media_urls=MagicMock(return_value=('video','image'))
            p.step_14_post_instagram=MagicMock(side_effect=PipelineStageError('Instagram Posting','uncertain',
                failure_classification='instagram_publication_uncertain',fallback_eligible=False,emergency_fallback_allowed=False))
            (Path(tmp)/'video_prompt.txt').write_text('synthetic')
            observed=[]
            def local(*args):observed.append(float(os.environ['STATE_ZERO_LOCAL_ONLY_DEADLINE']));return Path(tmp)/'media'
            with patch('pipeline._setup_global_exception_handler'),patch.object(FlowMediaClient,'local_pair_available',return_value=True), \
                    patch.object(FlowMediaClient,'generate_image',side_effect=local),patch.object(FlowMediaClient,'generate_video',side_effect=local):
                p.run()
            self.assertEqual(len(observed),2)
            self.assertTrue(all(abs(d-p.execution_acquired_at-300)<.1 for d in observed))
            p._run_emergency_fallback.assert_not_called();p.step_2_3_lookups.assert_not_called()
            p.step_4_6_prompts.assert_not_called();p.step_7_generate_image.assert_not_called()
            p.finalize_posted_run.assert_not_called()
            self.assertNotIn('STATE_ZERO_LOCAL_ONLY_DEADLINE',os.environ)

class SubmissionStateTests(unittest.TestCase):
    def test_remote_identity_without_marker_still_counts_as_uncertain_submission(self):
        payload={'status':'error','media_id':'accepted-exact-media','generation_status':'MEDIA_GENERATION_STATUS_FAILED',
            'error':{'class':'ServiceError','detail':'technical'}}
        with patch('flow_media_client.run_bounded',return_value=subprocess.CompletedProcess([],1,json.dumps(payload),'')):
            with self.assertRaises(FlowCommandError) as result:FlowMediaClient._run_json(['gflow','video'],1)
        self.assertEqual(result.exception.submission_state,'uncertain')

    def test_description_cannot_manufacture_submission_state(self):
        payload={'status':'error','error':{'class':'TimeoutError','retryable':True,'detail':'submitted_request_uncertain: MODEL CREDIT'}}
        with patch('flow_media_client.run_bounded',return_value=subprocess.CompletedProcess([],1,json.dumps(payload),'')):
            with self.assertRaises(FlowCommandError) as result:FlowMediaClient._run_json(['gflow','video'],1)
        self.assertEqual(result.exception.submission_state,'not_attempted')
        self.assertEqual(result.exception.category,'pre_submit_transient')

class InheritedClockTests(unittest.TestCase):
    def test_pipeline_child_preserves_parent_lock_acquisition_time(self):
        import time
        from utils import execution_lock
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'STATE_ZERO_PRIVATE_ROOT':tmp}):
            with execution_lock() as parent:
                acquired=time.time()-20
                with patch.dict(os.environ,{'STATE_ZERO_EXECUTION_FD':str(parent.fileno()),'STATE_ZERO_EXECUTION_ACQUIRED_AT':str(acquired)}):
                    with execution_lock() as child:self.assertEqual(child.acquired_at,acquired)

class OperationalReviewTests(unittest.TestCase):
    def test_worker_restarts_are_counted_across_the_original_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);context=root/'flow_video_attempt_1.context.json';context.write_text('{}')
            diagnostic=root/'flow_video_diagnostics.json';diagnostic.write_text('{"record_version":2}')
            client=FlowMediaClient()
            with patch.object(client,'_generation_seconds',return_value=7200), \
                    patch.object(client,'_recovery_command',return_value=(3,{'error_class':'TargetClosedError'})) as worker:
                self.assertIsNone(client._readback_media(context,'fingerprint',root/'video.mp4',__import__('time').time()+300,stage='video'))
                self.assertIsNone(client._readback_media(context,'fingerprint',root/'video.mp4',__import__('time').time()+600,stage='video'))
            evidence=json.loads(diagnostic.read_text())['recovery_workers']
            self.assertEqual(evidence['version'],1)
            self.assertEqual(evidence['attempts'][context.stem]['invocations'],2)
            self.assertEqual(evidence['attempts'][context.stem]['technical_failures'],2)
            worker.assert_called();self.assertEqual(worker.call_count,2)

    def test_repeated_busy_rescue_sends_one_actionable_alert(self):
        from ops.run_rescue import main
        from utils import ExecutionBusy,execution_lock_path
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'STATE_ZERO_PRIVATE_ROOT':tmp,'DISPLAY':':synthetic'}), \
                patch('utils.execution_lock',side_effect=ExecutionBusy()),patch('utils.get_pipeline_run_date_str',return_value='2026-10-22'), \
                patch('notifier.safe_notify_status',return_value=True) as notify:
            execution_lock_path().parent.mkdir(parents=True,exist_ok=True)
            self.assertEqual(main(),75);self.assertEqual(main(),75)
            self.assertEqual(notify.call_count,1)

"""Credit-free replay of completed Flow results surviving retrieval failures."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from unittest.mock import patch, Mock

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
from flow_media_client import FlowCommandError, FlowMediaClient
from utils import run_bounded

PROJECT = '11111111-1111-4111-8111-111111111111'
MEDIA = '22222222-2222-4222-8222-222222222222'


class ReleaseOneRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'GFLOW_PROFILE': 'shadow',
            'FLOW_PREFLIGHT_PROJECT_ID': PROJECT, 'FLOW_API_FALLBACK_ENABLED': 'true'}, clear=True)
        self.env.start(); self.addCleanup(self.env.stop)
        self.now = 100.0
        self.clock = patch('flow_media_client.time.time', side_effect=lambda: self.now)
        self.clock.start(); self.addCleanup(self.clock.stop)
        self.remaining = patch.object(FlowMediaClient, '_generation_seconds', side_effect=lambda: 7300-self.now)
        self.remaining.start(); self.addCleanup(self.remaining.stop)
        morning=patch('utils.get_pipeline_now',return_value=datetime(2026,10,23,10))
        morning.start();self.addCleanup(morning.stop)
        self.output = self.root/'generated_art.png'
        self.context = self.root/'flow_image_attempt_1.context.json'
        self.context.write_text('{}')
        self.marker = self.root/'flow_image_attempt_1.submit'
        self.marker.write_text(json.dumps(dict(state='forwarded',actual_model='BELUGA',
            expected_model='NARWHAL',selected_model='Nano Banana 2.1')))
        self.diagnostic = self.root/'flow_image_diagnostics.json'
        self.diagnostic.write_text(json.dumps({'request_hash':'a'*64,'attempts':[
            {'number':1,'submit_attempted':True,'status':'post_submit_error'}]}))
        self.result = dict(request_hash='a'*64,profile='shadow',project_id=PROJECT,count=1,model='NARWHAL',
            images=[dict(media_name=MEDIA,workflow_id=MEDIA,model_name_type='BELUGA',
                         aspect_ratio='IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR')])
        self.candidate = dict(kind='image',context_sha256=hashlib.sha256(self.context.read_bytes()).hexdigest(),
                              result=self.result)

    def complete(self):
        asset=self.root/'flow_recovered'/'original.png';asset.parent.mkdir(exist_ok=True)
        Image.new('RGB',(768,1024)).save(asset)
        return self.result | {'sha256':hashlib.sha256(asset.read_bytes()).hexdigest(),
            'images':[self.result['images'][0] | {'local_path':str(asset)}]}

    def test_oct23_candidate_near_search_expiry_has_three_persisted_retrieval_tries(self):
        calls=[]
        def worker(command, timeout):
            mode=command[command.index('--mode')+1];calls.append((mode,timeout))
            if mode=='search':
                self.now=396.0
                return 0,{'status':'candidate','candidate':self.candidate}
            if sum(mode=='retrieve' for mode,_ in calls)<3:
                self.now+=2
                return 3,{'error_class':'TargetClosedError'}
            return 0,{'result':self.complete()}
        before=(self.context.read_bytes(),self.marker.read_bytes())
        with patch.object(FlowMediaClient,'_recovery_command',side_effect=worker), \
                patch.object(FlowMediaClient,'_notify_transition'):
            result=FlowMediaClient()._readback_media(self.context,'a'*64,self.output,400)
        self.assertEqual([x[0] for x in calls],['search','retrieve','retrieve','retrieve'])
        self.assertTrue(result['images'][0]['local_path'])
        saved=json.loads(self.diagnostic.read_text())['retrieval']
        self.assertEqual(saved['deadline'],666)
        self.assertEqual(saved['attempts'],3)
        self.assertEqual(before,(self.context.read_bytes(),self.marker.read_bytes()))

    def test_retrieval_worker_cannot_change_the_committed_media(self):
        wrong=self.complete();wrong['images'][0]['media_name']='different'
        with patch.object(FlowMediaClient,'_recovery_command',side_effect=[
                (0,{'candidate':self.candidate}),(0,{'result':wrong})]):
            with self.assertRaises(FlowCommandError) as error:
                FlowMediaClient()._readback_media(self.context,'a'*64,self.output,400)
        self.assertEqual(error.exception.category,'request_identity_unverified')

    def test_retrieval_exhaustion_never_renews_on_next_slot(self):
        def worker(command,timeout):
            if command[command.index('--mode')+1]=='search':
                return 0,{'candidate':self.candidate}
            return 3,{'error_class':'TargetClosedError'}
        with patch.object(FlowMediaClient,'_recovery_command',side_effect=worker) as run, \
                patch.object(FlowMediaClient,'_notify_transition'):
            client=FlowMediaClient()
            self.assertIsNone(client._readback_media(self.context,'a'*64,self.output,400))
            saved=json.loads(self.diagnostic.read_text())['retrieval']
            self.now+=100
            self.assertIsNone(FlowMediaClient()._readback_media(self.context,'a'*64,self.output,800))
        self.assertEqual(run.call_count,4)
        again=json.loads(self.diagnostic.read_text())['retrieval']
        self.assertEqual((again['deadline'],again['attempts']),(saved['deadline'],3))

    def test_retrieval_is_capped_by_generation_cutoff(self):
        with patch.object(FlowMediaClient,'_generation_seconds',return_value=25), \
                patch.object(FlowMediaClient,'_notify_transition'), \
                patch.object(FlowMediaClient,'_recovery_command',side_effect=[
                    (0,{'candidate':self.candidate}),(3,{'error_class':'TargetClosedError'}),
                    (3,{'error_class':'TargetClosedError'}),(3,{'error_class':'TargetClosedError'})]) as run:
            FlowMediaClient()._readback_media(self.context,'a'*64,self.output,400)
        self.assertEqual(json.loads(self.diagnostic.read_text())['retrieval']['deadline'],125)
        self.assertTrue(all(call.args[1]<=25 for call in run.call_args_list[1:]))

    def test_search_worker_restarts_are_bounded_across_invocations(self):
        with patch.object(FlowMediaClient,'_recovery_command',return_value=(3,{'error_class':'TargetClosedError'})) as run, \
                patch('flow_media_client.time.sleep',side_effect=lambda seconds:setattr(self,'now',self.now+seconds)):
            self.assertIsNone(FlowMediaClient()._readback_media(self.context,'a'*64,self.output,400))
            self.assertIsNone(FlowMediaClient()._readback_media(self.context,'a'*64,self.output,500))
        self.assertEqual(run.call_count,2)

    def test_oct23_exhausted_retrieval_cannot_select_flow_replacement(self):
        saved=json.loads(self.diagnostic.read_text())
        saved['retrieval']={'version':1,'candidate':self.candidate,'deadline':99,'attempts':3}
        self.diagnostic.write_text(json.dumps(saved))
        generated=[]
        def generate(result):
            generated.append(result)
            raise AssertionError('A replacement is forbidden')
        with patch.object(FlowMediaClient,'_recover_pending',return_value=None), \
                patch('flow_media_client.GoogleImageClient') as api, \
                patch.object(FlowMediaClient,'_notify_transition'), \
                patch.object(FlowMediaClient,'_finish_daily',return_value=self.output):
            FlowMediaClient()._generate_daily('image',{},self.output,generate,None)
        self.assertEqual(generated,[])
        api.return_value.generate_from_json.assert_called_once()

    def test_oct21_unknown_submit_blocks_replacement_until_noon_api_fallback(self):
        with patch.object(FlowMediaClient,'_recover_pending',return_value=None), \
                patch('flow_media_client.GoogleImageClient') as api:
            with self.assertRaises(FlowCommandError) as error:
                FlowMediaClient()._generate_daily('image',{},self.output,
                    lambda result:self.fail('No replacement'),None)
        self.assertEqual(error.exception.category,'request_identity_unverified')
        api.assert_not_called()
        api_client=Mock()
        api_client.generate_from_json.side_effect=lambda request,path:Image.new('RGB',(768,1024)).save(path)
        with patch('utils.get_pipeline_now',return_value=datetime(2026,10,23,12)), \
                patch.object(FlowMediaClient,'_recover_pending',return_value=None), \
                patch.object(FlowMediaClient,'_notify_transition'), \
                patch('flow_media_client.GoogleImageClient',return_value=api_client):
            FlowMediaClient()._generate_daily('image',{},self.output,
                lambda result:self.fail('Unresolved Flow replacement is forbidden'),None)
        self.assertTrue(self.output.is_file())
        self.assertEqual(json.loads(self.diagnostic.read_text())['provider'],'google_api')
        api_client.generate_from_json.assert_called_once()

    def test_unresolved_search_reopens_next_slot_without_renewing_retrieval(self):
        calls=[]
        def unavailable(command,timeout):
            calls.append(command[command.index('--mode')+1])
            return 3,{'error_class':'TargetClosedError'}
        client=FlowMediaClient()
        with patch.object(client,'_recovery_command',side_effect=unavailable):
            self.assertIsNone(client._recover_pending('image',self.diagnostic,
                json.loads(self.diagnostic.read_text())['attempts'][0],self.output))
            client._recover_pending('image',self.diagnostic,
                json.loads(self.diagnostic.read_text())['attempts'][0],self.output)
            self.assertEqual(len(calls),2)
            self.now=1900
            client._recover_pending('image',self.diagnostic,
                json.loads(self.diagnostic.read_text())['attempts'][0],self.output)
        self.assertEqual(calls,['search']*4)

    def test_saved_cleanup_failure_rechecks_owner_then_resumes_same_attempt(self):
        saved=json.loads(self.diagnostic.read_text())
        saved['failure']={'category':'execution_busy'}
        saved['last_generation_process']={'cleanup_verified':False,'owned':[{'pid':424242,'start':'1'}]}
        self.diagnostic.write_text(json.dumps(saved))
        with patch('utils._process_snapshot',return_value={}), \
                patch.object(FlowMediaClient,'_recover_pending',return_value=self.complete()), \
                patch.object(FlowMediaClient,'_finish_daily',return_value=self.output), \
                patch.object(FlowMediaClient,'_notify_transition'):
            received=[]
            FlowMediaClient()._generate_daily('image',{},self.output,received.append,None)
        self.assertEqual(received[0]['images'][0]['media_name'],MEDIA)
        self.assertEqual(len(json.loads(self.diagnostic.read_text())['attempts']),1)

    def test_successful_cli_result_survives_cleanup_failure_but_next_browser_is_blocked(self):
        from utils import OwnedProcessCleanupError
        payload={'status':'ok','count':1,'images':[{'media_name':MEDIA}]}
        error=OwnedProcessCleanupError('Owned cleanup uncertain')
        error.returncode=0;error.stdout=json.dumps(payload);error.stderr=''
        error.process_evidence={'cleanup_verified':False,'owned':[{'pid':424242,'start':'1'}]}
        command=['gflow','image','t2i','synthetic','--profile','shadow']
        with patch('flow_media_client.run_bounded',side_effect=error), \
                patch.object(FlowMediaClient,'_notify_transition'):
            self.assertEqual(FlowMediaClient._run_json(command,30,self.marker),payload)
        with patch('utils._process_snapshot',return_value={424242:{'start':'1','state':'S'}}), \
                patch('flow_media_client.run_bounded') as run:
            with self.assertRaises(FlowCommandError):
                FlowMediaClient._run_json(command,30,self.marker)
        run.assert_not_called()

    def test_parent_rejects_retrieved_bytes_with_wrong_checksum(self):
        result=self.complete();result['sha256']='0'*64
        with patch.object(FlowMediaClient,'_recovery_command',side_effect=[
                (0,{'candidate':self.candidate}),(0,{'result':result})]):
            with self.assertRaises(FlowCommandError):
                FlowMediaClient()._readback_media(self.context,'a'*64,self.output,400)

    def test_live_owner_blocks_other_stage_without_consuming_a_retrieval_try(self):
        saved=json.loads(self.diagnostic.read_text())
        saved['retrieval']={'version':1,'candidate':self.candidate,'deadline':400,'attempts':0}
        video=self.root/'flow_video_diagnostics.json'
        video.write_text(json.dumps({'last_generation_process':{'cleanup_verified':False,
            'owned':[{'pid':424242,'start':'1'}]}}))
        self.diagnostic.write_text(json.dumps(saved))
        with patch('utils._process_snapshot',return_value={424242:{'start':'1','state':'S'}}), \
                patch.object(FlowMediaClient,'_recovery_command') as worker:
            with self.assertRaises(FlowCommandError) as error:
                FlowMediaClient()._readback_media(self.context,'a'*64,self.output,400)
        self.assertEqual(error.exception.submission_state,'not_attempted')
        worker.assert_not_called()
        self.assertEqual(json.loads(self.diagnostic.read_text())['retrieval']['attempts'],0)

    def test_real_worker_timeout_preserves_reply_and_parent_validates_local_bytes(self):
        result=self.complete()
        command=[sys.executable,'-c',
            'import json,sys,time;print(sys.argv[1],flush=True);time.sleep(30)',
            json.dumps({'status':'downloaded','result':result})]
        run=FlowMediaClient._recovery_command
        def worker(arguments,timeout):
            if arguments[arguments.index('--mode')+1]=='search':
                return 0,{'candidate':self.candidate}
            return run(command,6.3)
        with patch.object(FlowMediaClient,'_recovery_command',side_effect=worker):
            received=FlowMediaClient()._readback_media(self.context,'a'*64,self.output,400)
        self.assertEqual(received['sha256'],result['sha256'])
        saved=json.loads(self.diagnostic.read_text())
        self.assertTrue(saved['last_worker']['processes']['cleanup_verified'])
        self.assertEqual(saved['retrieval']['attempts'],1)

    def test_worker_reply_waits_for_cleanup_then_reuses_bytes_after_deadline(self):
        result=self.complete()
        blocked={'status':'downloaded','result':result,'error_class':'OwnedProcessCleanupError',
            'worker':{'processes':{'cleanup_verified':False,'owned':[{'pid':424242,'start':'1'}]}}}
        with patch.object(FlowMediaClient,'_recovery_command',side_effect=[
                (0,{'candidate':self.candidate}),(3,blocked)]) as worker:
            with self.assertRaises(FlowCommandError):
                FlowMediaClient()._readback_media(self.context,'a'*64,self.output,400)
            self.now=700
            with patch('utils._process_snapshot',return_value={}):
                received=FlowMediaClient()._readback_media(self.context,'a'*64,self.output,400)
        self.assertEqual(received['sha256'],result['sha256'])
        self.assertEqual(worker.call_count,2)
        self.assertEqual(json.loads(self.diagnostic.read_text())['retrieval']['attempts'],1)


class OwnedProcessTests(unittest.TestCase):
    def test_nonzero_exit_cleans_a_detached_owned_descendant(self):
        code="""import subprocess,sys,time
p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'],start_new_session=True,
                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
print(p.pid,flush=True)
time.sleep(.25)
sys.exit(7)
"""
        result=run_bounded([sys.executable,'-c',code],timeout=3)
        pid=int(result.stdout.strip())
        try:
            stat=Path(f'/proc/{pid}/stat')
            alive=stat.exists() and stat.read_text().rsplit(')',1)[1].split()[0]!='Z'
            self.assertFalse(alive,'Detached descendant survived nonzero exit')
        finally:
            try:os.kill(pid,signal.SIGKILL)
            except ProcessLookupError:pass


class RecoveryEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def test_complete_worker_reply_survives_teardown_timeout_only_after_verified_cleanup(self):
        payload={'status':'downloaded','result':{'sha256':'a'*64,'local_path':'synthetic'}}
        for verified in (True,False):
            with self.subTest(cleanup_verified=verified):
                error=subprocess.TimeoutExpired(['worker'],1,output=json.dumps(payload),stderr='')
                error.process_evidence={'cleanup_verified':verified}
                with patch('flow_media_client.run_bounded',side_effect=error):
                    code,reply=FlowMediaClient._recovery_command(['worker'],90)
                self.assertEqual(code,0 if verified else -1)
                self.assertEqual(reply['result']['sha256'],'a'*64)

    async def test_worker_emits_result_before_browser_teardown(self):
        import importlib.util,io
        from unittest.mock import AsyncMock
        path=Path(__file__).resolve().parents[1]/'ops/flow_media_recover.py'
        spec=importlib.util.spec_from_file_location('release_one_worker',path)
        worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
        stream=io.StringIO()
        class Client:
            def __init__(self,**kwargs):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*args):
                self_reply=json.loads(stream.getvalue())
                assert self_reply['result']['sha256']=='a'*64
        with patch('gflow_cli.api.client.FlowApiClient',Client), \
                patch('flow_readback.recover_context',new=AsyncMock(return_value={'sha256':'a'*64})):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp); record=root/'flow_image_diagnostics.json'
                record.write_text(json.dumps({'retrieval':{'candidate':{'kind':'image'}}}))
                await worker.download_existing(PROJECT,'',root/'original.png',context_file=root/'flow_image_attempt_1.context.json',
                    deadline=time.time()+90,request_hash='a'*64,mode='retrieve',record=record,result_stream=stream)

    async def test_editor_and_download_failures_have_distinct_last_phase(self):
        from flow_readback import download_video
        from unittest.mock import AsyncMock
        clip=dict(project_id=PROJECT,media_id=MEDIA,workflow_id=MEDIA,bytes=2048,video_url=None)
        for failed_at in ('editor','download'):
            with self.subTest(failed_at=failed_at),tempfile.TemporaryDirectory() as tmp:
                page=Mock(url=f'https://flow.google.com/project/{PROJECT}/edit/{MEDIA}')
                page.goto=AsyncMock(side_effect=TimeoutError() if failed_at=='editor' else None)
                phases=[]
                with patch('flow_readback.recovery_phase',side_effect=phases.append), \
                        patch('gflow_cli.api.transports.migrated_recover._download_original_verified',new=AsyncMock(side_effect=TimeoutError())):
                    with self.assertRaises(TimeoutError):
                        await download_video(Mock(page=page),clip,Path(tmp)/'original.mp4',deadline=time.time()+10)
                self.assertEqual(phases[-1],failed_at)

    async def test_search_and_retrieval_never_write_the_generating_childs_files(self):
        import test_flow_readback as fixtures
        from flow_readback import recover_context, context_path, receipt_path, decode_listing
        from unittest.mock import AsyncMock, Mock
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'GFLOW_PROFILE':'shadow'}):
            root=Path(tmp);marker=fixtures.ReadbackTests().saved_attempt(root)
            context=context_path(marker);receipt_path(context).write_text('{}')
            upload=root/'flow_image_attempt_1.upload.json';upload.write_text('{}')
            before={p.name:p.read_bytes() for p in root.iterdir()}
            async def download(client,image,output):
                Image.new('RGB',(768,1024)).save(output)
            with patch('flow_readback.snapshot',new=AsyncMock(return_value=decode_listing(fixtures.listing(),PROJECT))), \
                    patch('flow_readback.download_image',new=AsyncMock(side_effect=download)):
                candidate=await recover_context(Mock(),context,PROJECT,'a'*64,time.time()+10,root/'original.png',mode='search')
                self.assertFalse((root/'original.png').exists())
                for _ in range(2):
                    result=await recover_context(Mock(),context,PROJECT,'a'*64,time.time()+10,root/'original.png',
                                                 mode='retrieve',candidate=candidate)
            self.assertEqual(result['sha256'],hashlib.sha256((root/'original.png').read_bytes()).hexdigest())
            self.assertEqual(before,{name:(root/name).read_bytes() for name in before})
            self.assertEqual(FlowMediaClient()._local_result('image',result,root/'art.png',result['sha256']),result)

    async def test_search_cannot_displace_an_existing_image_receipt(self):
        import test_flow_readback as fixtures
        from flow_readback import recover_context, context_path, decode_listing, ReadbackIdentityError
        from unittest.mock import AsyncMock, Mock
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'GFLOW_PROFILE':'shadow'}):
            root=Path(tmp);context=context_path(fixtures.ReadbackTests().saved_attempt(root))
            with patch('flow_readback.snapshot',new=AsyncMock(return_value=decode_listing(fixtures.listing(),PROJECT))):
                with self.assertRaises(ReadbackIdentityError):
                    await recover_context(Mock(),context,PROJECT,'a'*64,time.time()+10,root/'original.png',
                                          mode='search',media_id='44444444-4444-4444-8444-444444444444')

    async def test_changed_exact_candidate_blocks_download(self):
        import test_flow_readback as fixtures
        from flow_readback import recover_context, context_path, decode_listing, ReadbackIdentityError
        from unittest.mock import AsyncMock, Mock
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'GFLOW_PROFILE':'shadow'}):
            root=Path(tmp);context=context_path(fixtures.ReadbackTests().saved_attempt(root))
            with patch('flow_readback.snapshot',new=AsyncMock(return_value=decode_listing(fixtures.listing(),PROJECT))), \
                    patch('flow_readback.download_image',new=AsyncMock()) as download:
                candidate=await recover_context(Mock(),context,PROJECT,'a'*64,time.time()+10,root/'original.png',mode='search')
                candidate['result']['images'][0]['media_name']='different'
                with self.assertRaises(ReadbackIdentityError):
                    await recover_context(Mock(),context,PROJECT,'a'*64,time.time()+10,root/'original.png',
                                          mode='retrieve',candidate=candidate)
                download.assert_not_awaited()

    def test_worker_diagnostics_never_retain_raw_stderr(self):
        process=subprocess.CompletedProcess([],3,'{"error_class":"TargetClosedError"}',
            'private prompt and https://example.test/?token=secret\nTargetClosedError\n'
            'STATE_ZERO_PHASE {"phase":"editor","at":123,"private":"secret"}\n')
        with patch('flow_media_client.run_bounded',return_value=process):
            code,payload=FlowMediaClient._recovery_command(['worker'],90)
        self.assertEqual(code,3)
        self.assertEqual(payload['worker']['stderr_category'],'TargetClosedError')
        self.assertEqual(payload['worker']['phases'],[{'phase':'editor','at':123}])
        self.assertNotIn('secret',json.dumps(payload))

    def test_lifecycle_failure_is_allowlisted_and_observers_cannot_abort_transport(self):
        from flow_readback import note_video_lifecycle, note_video_response
        from unittest.mock import Mock
        for value,expected in [('net::ERR_ABORTED','net::ERR_ABORTED'),('secret signed url','OTHER')]:
            data={}
            with patch('flow_readback.note_response',side_effect=OSError('disk unavailable')):
                note_video_lifecycle(data,'request_failed',request=Mock(failure=value))
                note_video_response(data,phase='submit',rpc='ogiZ0b',response=Mock(status=200),exc=RuntimeError('private'))
            self.assertEqual(data['observer']['events'][0]['failure'],expected)
            self.assertNotIn('secret',json.dumps(data))
        # Malformed telemetry cannot escape into a generation or its guard.
        note_video_response({'observer':[]},phase='submit',rpc='ogiZ0b',response=Mock(status=200))
        note_video_lifecycle({'observer':[]},'request_finished')

    def test_cleanup_does_not_kill_an_unrelated_owner(self):
        unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'],start_new_session=True)
        try:
            result=run_bounded([sys.executable,'-c','raise SystemExit(7)'],timeout=3)
            self.assertEqual(result.returncode,7)
            self.assertIsNone(unrelated.poll())
            self.assertTrue(result.process_evidence['cleanup_verified'])
        finally:
            unrelated.terminate();unrelated.wait(timeout=3)

class CleanupVerificationTests(unittest.TestCase):
    def test_new_unattributed_chrome_blocks_handoff_without_killing_it(self):
        from utils import OwnedProcessCleanupError
        calls=[0]
        def snapshot():
            calls[0]+=1
            return {} if calls[0]==1 else {999999:{'name':'chrome','ppid':1,'pgid':999999,
                'sid':999999,'state':'S','start':'123'}}
        with patch('utils._process_snapshot',side_effect=snapshot), \
                patch('utils.os.kill') as kill:
            with self.assertRaises(OwnedProcessCleanupError) as error:
                run_bounded([sys.executable,'-c','pass'],timeout=3)
        kill.assert_not_called()
        self.assertFalse(error.exception.process_evidence['cleanup_verified'])

class GracefulDescendantTests(unittest.TestCase):
    def test_finished_cli_allows_its_owned_browser_to_finish_releasing_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker=Path(tmp)/'owned-resource'
            marker.write_text('held')
            descendant="import pathlib,sys,time;time.sleep(.5);pathlib.Path(sys.argv[1]).unlink()"
            parent=("import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]],"
                    "start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);time.sleep(.25)")
            result=run_bounded([sys.executable,'-c',parent,descendant,str(marker)],timeout=3)
            self.assertEqual(result.returncode,0)
            self.assertFalse(marker.exists(),'Cleanup killed a naturally closing descendant before resource release')

class ReparentedGroupTests(unittest.TestCase):
    def test_reparented_group_member_ignoring_term_is_still_owned_and_reaped(self):
        import utils
        original=utils._process_snapshot
        def reparented():
            values=original()
            for pid,entry in values.items():
                if pid!=entry['pgid']:entry['ppid']=1
            return values
        with tempfile.TemporaryDirectory() as tmp:
            ready=Path(tmp)/'ready'
            descendant="import signal,time,pathlib,sys;signal.signal(signal.SIGTERM,signal.SIG_IGN);pathlib.Path(sys.argv[1]).touch();time.sleep(30)"
            parent=("import subprocess,sys,time,pathlib;p=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]],"
                    "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);print(p.pid,flush=True);"
                    "\nwhile not pathlib.Path(sys.argv[2]).exists():time.sleep(.005)\nraise SystemExit(7)")
            result=None
            try:
                with patch('utils._process_snapshot',side_effect=reparented):
                    result=run_bounded([sys.executable,'-c',parent,descendant,str(ready)],timeout=3)
                pid=int(result.stdout.strip());stat=Path(f'/proc/{pid}/stat')
                self.assertFalse(stat.exists() and stat.read_text().rsplit(')',1)[1].split()[0]!='Z')
            finally:
                if result:
                    try:os.kill(int(result.stdout.strip()),signal.SIGKILL)
                    except ProcessLookupError:pass

"""Exact-video recovery must survive missing browser responses without spending."""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
import flow_readback as readback

PROJECT = '11111111-1111-4111-8111-111111111111'
MEDIA = '22222222-2222-4222-8222-222222222222'
WORKFLOW = '33333333-3333-4333-8333-333333333333'
FRAME = '44444444-4444-4444-8444-444444444444'
OTHER = '55555555-5555-4555-8555-555555555555'


def video_row(media=MEDIA, workflow=WORKFLOW, frame=FRAME, prompt='synthetic', seed=123, status=3):
    info = [[None, 1, frame, [None, None, None, None]]]
    settings = [['veo_3_1_i2v_s_fast_portrait', 2, [1], None, 1, 1], info]
    detail = [[1792557770, 0], prompt, None, None, None,
              'https://example.invalid/poster', [None, settings, [], None, 1],
              None, [status], 1, 'https://example.invalid/poster', None, None, 2048]
    result = [None, seed, None, None, None, None, None, prompt,
              'https://flow-content.google/video/' + workflow + '?signed=private',
              None, None, None, 'veo_3_1_i2v_s_fast_portrait', '', None, False, 1]
    return [workflow, PROJECT, media, None, None, detail, None,
            [result, [None, None, [8]], [workflow], None, None, []]]


def listing(*rows):
    return readback.decode_listing([None, [], list(rows or [video_row()]), [], None, None, None, []], PROJECT)


def context():
    return dict(version=1, kind='video', project_id=PROJECT, profile='shadow', baseline=[],
                model='veo_3_1_i2v_s_fast_portrait', aspect='portrait', count=1, duration=8,
                wire_verified=True, seed=123, prompt_hash=hashlib.sha256(b'synthetic').hexdigest(), frame_id=FRAME)


class VideoSelectionTests(unittest.TestCase):
    def test_video_after_unrelated_records_is_selected_by_exact_identity(self):
        self.assertTrue(callable(getattr(readback, 'select_video', None)), 'Independent video selection is missing')
        data = listing(video_row(OTHER, OTHER, OTHER, 'other'), video_row())
        result = readback.select_video(data, context())
        self.assertEqual((result['media_id'], result['workflow_id']), (MEDIA, WORKFLOW))

    def test_known_media_and_workflow_are_not_listing_key_aliases(self):
        self.assertTrue(callable(getattr(readback, 'select_video', None)), 'Exact known-ID selection is missing')
        result = readback.select_video(listing(), context(), media_id=MEDIA, workflow_id=WORKFLOW)
        self.assertEqual(result['media_id'], MEDIA)
        with self.assertRaises(readback.ReadbackIdentityError):
            readback.select_video(listing(), context(), media_id=WORKFLOW, workflow_id=MEDIA)

    def test_wrong_inputs_old_assets_and_multiple_matches_cannot_be_adopted(self):
        self.assertTrue(callable(getattr(readback, 'select_video', None)), 'Video identity validation is missing')
        for changed in ({'frame_id': OTHER}, {'prompt_hash': 'a'*64}, {'seed': 124},
                        {'baseline': [WORKFLOW]}, {'baseline': None}, {'project_id': OTHER}):
            with self.subTest(changed=changed), self.assertRaises(readback.ReadbackUnavailable):
                readback.select_video(listing(), context() | changed)
        with self.assertRaises(readback.ReadbackUnavailable):
            readback.select_video(listing(video_row(), video_row(OTHER, OTHER)), context())

    def test_known_identity_conflicts_and_settings_block_recovery(self):
        self.assertTrue(callable(getattr(readback, 'select_video', None)), 'Video settings checks are missing')
        for field,value in (('model','wrong'),('aspect','landscape'),('count',2),('duration',4)):
            with self.subTest(field=field), self.assertRaises(readback.ReadbackIdentityError):
                readback.select_video(listing(), context() | {field:value}, media_id=MEDIA, workflow_id=WORKFLOW)

    def test_completed_exact_clip_without_url_can_use_original_download_control(self):
        row=video_row();row[7][0][8]=None
        result=readback.select_video(listing(row),context(),media_id=MEDIA,workflow_id=WORKFLOW)
        self.assertIsNone(result['video_url'])
        self.assertEqual(result['bytes'],2048)


class VideoDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_bytes_never_replace_a_valid_existing_file(self):
        self.assertTrue(callable(getattr(readback, 'download_video', None)), 'Verified original video download is missing')
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/'video.mp4'; output.write_bytes(b'valid-existing')
            response = Mock(status=200, body=AsyncMock(return_value=b'bad'))
            client = Mock(_context=Mock(request=Mock(get=AsyncMock(return_value=response))))
            clip = dict(media_id=MEDIA,workflow_id=WORKFLOW,bytes=2048,
                        video_url='https://flow-content.google/video/'+WORKFLOW)
            with self.assertRaises(readback.ReadbackUnavailable):
                await readback.download_video(client,clip,output,deadline=10**12)
            self.assertEqual(output.read_bytes(), b'valid-existing')

    async def test_non_original_transcode_url_is_never_requested_as_original(self):
        self.assertTrue(callable(getattr(readback, 'download_video', None)), 'Original-video URL validation is missing')
        with tempfile.TemporaryDirectory() as tmp:
            client=Mock(_context=Mock(request=Mock(get=AsyncMock())),
                        page=Mock(url=f'https://flow.google.com/project/{PROJECT}/edit/{MEDIA}',goto=AsyncMock()))
            clip=dict(media_id=MEDIA,workflow_id=WORKFLOW,project_id=PROJECT,bytes=2048,
                      video_url='https://lh3.googleusercontent.com/video=m22')
            with patch('gflow_cli.api.transports.migrated_recover._download_original_verified',new=AsyncMock(side_effect=readback.ReadbackUnavailable('unavailable'))):
                with self.assertRaises(readback.ReadbackUnavailable):
                    await readback.download_video(client,clip,Path(tmp)/'video.mp4',deadline=10**12)
            client._context.request.get.assert_not_called()

    async def test_native_original_control_requires_exact_clip_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            page=Mock(url=f'https://flow.google.com/project/{PROJECT}/edit/{OTHER}',goto=AsyncMock())
            clip=dict(media_id=MEDIA,workflow_id=WORKFLOW,project_id=PROJECT,bytes=2048,video_url=None)
            with patch('gflow_cli.api.transports.migrated_recover._download_original_verified',new=AsyncMock()) as download:
                with self.assertRaises(readback.ReadbackIdentityError):
                    await readback.download_video(Mock(page=page),clip,Path(tmp)/'video.mp4',deadline=10**12)
                download.assert_not_awaited()


class VideoSubmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_one_forward_and_actual_identity_is_saved_first(self):
        self.assertTrue(callable(getattr(readback, 'guard_video', None)), 'Video forwarding guard is missing')
        with tempfile.TemporaryDirectory() as tmp:
            marker=Path(tmp)/'flow_video_attempt_1.submit';marker.write_text('submit_attempted\n')
            data=context() | {'attempt':1,'request_hash':'a'*64,'frozen_inputs_sha256':'b'*64,
                              'source_hash':'c'*64,'baseline':[],'wire_verified':False}
            readback.save(marker.with_suffix('.context.json'),data)
            payload=[[[[None,None,[[['synthetic']]]],'veo_3_1_i2v_s_fast_portrait',1,None,[None,FRAME,None,None,None,[None,None,1,1]],[None]*6]], [None,None,None,None,None,PROJECT], [None,2]]
            body='f.req='+json.dumps([[['eb1hJf',json.dumps(payload),None,'generic']]])
            request=Mock(post_data=body)
            async def forwarded():
                saved=json.loads(marker.read_text());ctx=json.loads(marker.with_suffix('.context.json').read_text())
                self.assertEqual(saved['state'],'forwarded')
                self.assertTrue(ctx['wire_verified'])
                self.assertEqual(ctx['frame_id'],FRAME)
                self.assertNotIn('synthetic',marker.with_suffix('.context.json').read_text())
            route=Mock(continue_=AsyncMock(side_effect=forwarded),abort=AsyncMock())
            with patch.dict(os.environ,{'GFLOW_SHADOW_SUBMIT_MARKER':str(marker)}):
                self.assertIsNone(await readback.guard_video(route,request,data,'eb1hJf'))
                self.assertIsNotNone(await readback.guard_video(route,request,data,'eb1hJf'))
            self.assertEqual(route.continue_.await_count,1)
            self.assertEqual(route.abort.await_count,1)

    async def test_unrecognized_request_shape_cannot_forward(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker=Path(tmp)/'flow_video_attempt_1.submit';marker.write_text('submit_attempted\n')
            data=context() | dict(attempt=1,request_hash='a'*64,frozen_inputs_sha256='b'*64,source_hash='c'*64)
            row=[[None,None,[[['synthetic']]]],data['model'],1,None,[None,FRAME],[]]
            payload=[[row+[None]],[None]*5+[PROJECT],[]]
            body='f.req='+json.dumps([[['eb1hJf',json.dumps(payload),None,'generic']]])
            route=Mock(abort=AsyncMock(),continue_=AsyncMock())
            with patch.dict(os.environ,{'GFLOW_SHADOW_SUBMIT_MARKER':str(marker)}):
                self.assertIsNotNone(await readback.guard_video(route,Mock(post_data=body),data,'eb1hJf'))
            route.continue_.assert_not_awaited()

    async def test_known_request_with_multiple_outputs_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker=Path(tmp)/'flow_video_attempt_1.submit';marker.write_text('submit_attempted\n')
            data=context() | dict(attempt=1,request_hash='a'*64,frozen_inputs_sha256='b'*64,source_hash='c'*64)
            row=[[None,None,[[['synthetic']]]],data['model'],1,None,[None,FRAME],[]]
            payload=[[row,row],[None]*5+[PROJECT],[]]
            body='f.req='+json.dumps([[['eb1hJf',json.dumps(payload),None,'generic']]])
            route=Mock(abort=AsyncMock(),continue_=AsyncMock())
            with patch.dict(os.environ,{'GFLOW_SHADOW_SUBMIT_MARKER':str(marker)}):
                self.assertIsNotNone(await readback.guard_video(route,Mock(post_data=body),data,'eb1hJf'))
            route.continue_.assert_not_awaited()

    async def test_second_billable_rpc_cannot_hide_inside_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker=Path(tmp)/'flow_video_attempt_1.submit';marker.write_text('submit_attempted\n')
            data=context() | dict(attempt=1,request_hash='a'*64,frozen_inputs_sha256='b'*64,source_hash='c'*64)
            row=[[None,None,[[['synthetic']]]],data['model'],1,None,[None,FRAME],[]]
            payload=[[row],[None]*5+[PROJECT],[]]
            envelope=[[['eb1hJf',json.dumps(payload),None,'generic'],['nprQif','[]',None,'generic']]]
            route=Mock(abort=AsyncMock(),continue_=AsyncMock())
            with patch.dict(os.environ,{'GFLOW_SHADOW_SUBMIT_MARKER':str(marker)}):
                self.assertIsNotNone(await readback.guard_video(route,Mock(post_data='f.req='+json.dumps(envelope)),data,'eb1hJf'))
            route.continue_.assert_not_awaited()

    def test_submit_rpc_detected_after_non_generation_rpc(self):
        self.assertTrue(callable(getattr(readback,'video_submit_rpc',None)))
        request=Mock(url='https://flow.google.com/data/batchexecute?rpcids=other',
                     post_data='f.req='+json.dumps([[['other','[]'],['eb1hJf','[]']]]))
        self.assertEqual(readback.video_submit_rpc(request),'eb1hJf')

    async def test_evidence_write_failure_prevents_forwarding(self):
        self.assertTrue(callable(getattr(readback, 'guard_video', None)), 'Video forwarding guard is missing')
        route=Mock(continue_=AsyncMock(),abort=AsyncMock())
        with tempfile.TemporaryDirectory() as tmp:
            marker=Path(tmp)/'flow_video_attempt_1.submit';marker.write_text('submit_attempted\n')
            with patch.dict(os.environ,{'GFLOW_SHADOW_SUBMIT_MARKER':str(marker)}), \
                 patch('flow_readback.save',side_effect=readback.ReadbackIdentityError('disk unavailable')):
                self.assertIsNotNone(await readback.guard_video(route,Mock(post_data='unrecognized'),context(),'eb1hJf'))
        route.continue_.assert_not_awaited();route.abort.assert_awaited_once()


class VideoContextRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_recovers_exact_result_under_original_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); marker=root/'flow_video_attempt_1.submit'; ctx=marker.with_suffix('.context.json')
            (root/'flow_inputs.json').write_text('frozen'); (root/'flow_start_frame.png').write_bytes(b'frame')
            data=context() | dict(baseline=None,attempt=1, request_hash='a'*64, frozen_inputs_sha256=hashlib.sha256(b'frozen').hexdigest(),
                source_hash=hashlib.sha256(b'frame').hexdigest(), recovery_deadline=1000)
            readback.save(ctx,data); readback.save(marker,dict(state='forwarded',actual_model=data['model'],readback_hash=readback.identity_hash(data)))
            client=Mock()
            async def download(client,clip,output,**kwargs):
                saved=json.loads(readback.receipt_path(ctx).read_text())
                self.assertEqual((saved['media_id'],saved['workflow_id']),(MEDIA,WORKFLOW))
                self.assertEqual(saved['local_path'],str(output))
                self.assertLessEqual(kwargs['deadline'],1000)
                output.write_bytes(b'original')
            with patch.dict(os.environ,{'GFLOW_PROFILE':'shadow'}), patch('flow_readback.time.time',return_value=900), \
                 patch('flow_readback.snapshot',new=AsyncMock(return_value=listing())) as snap, \
                 patch('flow_readback.download_video',new=AsyncMock(side_effect=download)) as fetch:
                result=await readback.recover_context(client,ctx,PROJECT,'a'*64,2000,root/'saved.mp4',media_id=MEDIA)
            self.assertEqual(result['local_path'],str(root/'saved.mp4'))
            self.assertEqual(json.loads(ctx.read_text())['recovery_deadline'],1000)
            snap.assert_awaited_once();fetch.assert_awaited_once()

    async def test_changed_frame_or_evidence_never_starts_readback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); marker=root/'flow_video_attempt_1.submit'; ctx=marker.with_suffix('.context.json')
            (root/'flow_inputs.json').write_text('frozen'); (root/'flow_start_frame.png').write_bytes(b'changed')
            data=context() | dict(attempt=1,request_hash='a'*64,frozen_inputs_sha256=hashlib.sha256(b'frozen').hexdigest(),source_hash='a'*64)
            readback.save(ctx,data);readback.save(marker,dict(state='forwarded',actual_model=data['model'],readback_hash=readback.identity_hash(data)))
            with patch.dict(os.environ,{'GFLOW_PROFILE':'shadow'}), patch('flow_readback.snapshot',new=AsyncMock()) as snap:
                with self.assertRaises(readback.ReadbackIdentityError):
                    await readback.recover_context(Mock(),ctx,PROJECT,'a'*64,10**12,root/'saved.mp4')
            snap.assert_not_awaited()

class AdapterKnownIdentityTests(unittest.TestCase):
    def test_failure_media_identity_reaches_worker_without_catalog_output(self):
        import time
        from flow_media_client import FlowMediaClient
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); diagnostic=root/'flow_video_diagnostics.json'
            diagnostic.write_text(json.dumps({'request_hash':'a'*64}))
            (root/'flow_video_attempt_1.context.json').write_text('{}')
            attempt=dict(number=1,recovery_deadline=time.time()+20,failure={'media_id':MEDIA})
            client=FlowMediaClient()
            with patch.object(client,'_catalog_result',return_value=None), \
                 patch.object(client,'_generation_seconds',return_value=3600), \
                 patch.object(client,'_notify_transition'), patch.object(client,'_readback_media',return_value=None) as worker:
                self.assertIsNone(client._recover_pending('video',diagnostic,attempt,root/'video.mp4'))
            self.assertEqual(worker.call_args.kwargs['known']['media_id'],MEDIA)


class WorkerIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_known_identity_in_context_mode_is_blocking(self):
        from ops.flow_media_recover import download_existing
        with patch('gflow_cli.api.client.FlowApiClient') as browser:
            for media,workflow in (('corrupt',None),(MEDIA,'corrupt')):
                with self.subTest(media=media,workflow=workflow), self.assertRaises(readback.ReadbackIdentityError):
                    await download_existing(PROJECT,media,Path('/tmp/video.mp4'),context_file=Path('/tmp/context'),
                                            workflow_id=workflow)
            browser.assert_not_called()


class InstalledVideoDecoderTests(unittest.TestCase):
    def test_mixed_response_decodes_every_video(self):
        from gflow_cli.api.transports import batchexecute
        self.assertTrue(callable(getattr(batchexecute,'_generation_records',None)))
        rows=batchexecute._generation_records('as29s',[video_row(OTHER,OTHER),video_row()])
        self.assertEqual([row.media_id for row in rows],[OTHER,MEDIA])

class InstalledVideoObserverTests(unittest.IsolatedAsyncioTestCase):
    async def observe(self, root, *, lose_submit=False, lose_completion=False, unrelated_bare=False):
        from unittest.mock import MagicMock
        from gflow_cli.api.transports.migrated_composer import MigratedComposer
        handlers={}; callback=None
        page=MagicMock(route=AsyncMock(),unroute=AsyncMock())
        page.on.side_effect=lambda event,fn:handlers.update({event:fn})
        button=page.locator.return_value.filter.return_value.first
        button.count=AsyncMock(return_value=1);button.is_enabled=AsyncMock(return_value=True)
        async def register(pattern,fn):
            nonlocal callback
            callback=fn
        page.route.side_effect=register
        data=context() | dict(attempt=1,request_hash='a'*64,frozen_inputs_sha256='b'*64,source_hash='c'*64)
        composer=MigratedComposer();composer._shadow_video_context=data
        wire=[[[[None,None,[[['synthetic']]]],data['model'],1,123,[None,FRAME],[]]], [None]*5+[PROJECT], []]
        request=Mock(url='https://flow.google.com/data/batchexecute?rpcids=eb1hJf',post_data='f.req='+json.dumps([[['eb1hJf',json.dumps(wire),None,'generic']]]))
        route=Mock(abort=AsyncMock(),continue_=AsyncMock())
        def response(rpc,payload,req,error=False):
            text=json.dumps([['wrb.fr',rpc,json.dumps(payload),None]])
            return Mock(url='https://flow.google.com/data/batchexecute?rpcids='+rpc,request=req,status=200,
                text=AsyncMock(**({'side_effect':RuntimeError('Network.getResponseBody: No data found https://secret.invalid/?token=x')}
                    if error else {'return_value':text})))
        async def click(*args,**kwargs):
            self.assertIsNotNone(callback,'Video route guard was not installed')
            await callback(route,request)
            if unrelated_bare:
                foreign=response('eb1hJf',video_row(OTHER,OTHER,status=6),Mock())
                foreign.url='https://flow.google.com/data/batchexecute'
                await handlers['response'](foreign)
            await handlers['response'](response('eb1hJf',video_row(status=6),request,lose_submit))
            if not lose_submit:
                poll=Mock(post_data=WORKFLOW)
                await handlers['response'](response('as29s',[video_row(OTHER,OTHER),video_row()],poll,lose_completion))
        with patch.dict(os.environ,{'GFLOW_SHADOW_SUBMIT_MARKER':str(root/'flow_video_attempt_1.submit')},clear=True), \
             patch.object(composer,'_click',new=AsyncMock(side_effect=click)) as clicks:
            result=await composer.submit_and_observe(page,poll_timeout_s=.1,on_started=None,project_id=PROJECT,expect_media_id=FRAME)
        clicks.assert_awaited_once();route.continue_.assert_awaited_once();page.unroute.assert_awaited_once()
        return result

    async def test_known_video_after_unrelated_poll_record_completes(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=await self.observe(Path(tmp))
        self.assertEqual((result.media_id,result.workflow_id),(MEDIA,WORKFLOW))

    async def test_unowned_bare_submit_cannot_capture_current_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=await self.observe(Path(tmp),unrelated_bare=True)
        self.assertEqual((result.media_id,result.workflow_id),(MEDIA,WORKFLOW))

    async def test_lost_submit_response_hands_off_with_original_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with self.assertRaisesRegex(Exception,'video_response_read_failed.*RuntimeError'):
                await self.observe(root,lose_submit=True)
            data=json.loads((root/'flow_video_attempt_1.context.json').read_text())
            self.assertEqual(data['original_response_failure']['error_class'],'RuntimeError')
            self.assertNotIn('secret.invalid',json.dumps(data))
            self.assertFalse((root/'flow_video_attempt_1.result.json').exists())

    async def test_completion_body_loss_retains_remote_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with self.assertRaisesRegex(Exception,'video_response_read_failed'):
                await self.observe(root,lose_completion=True)
            saved=json.loads((root/'flow_video_attempt_1.result.json').read_text())
            self.assertEqual((saved['media_id'],saved['workflow_id']),(MEDIA,WORKFLOW))
            self.assertNotIn('https://',json.dumps(saved))


if __name__ == '__main__':
    unittest.main()

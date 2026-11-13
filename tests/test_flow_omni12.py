"""Offline Omni Flow contracts; no browser or provider calls."""
import tests
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

from flow_media_client import FlowCommandError, FlowMediaClient
from flow_fixture import freeze, video_receipt
from state_zero_models import FLOW_VIDEO_LABEL, flow_video_model_matches
from ops.flow_omni_probe import run_probe
from utils import execution_lock


class Omni12Tests(unittest.IsolatedAsyncioTestCase):
    def test_installed_i2v_cli_accepts_the_backported_alias(self):
        from gflow_cli.cli_video import i2v
        from state_zero_models import FLOW_VIDEO_ALIAS
        option = next(p for p in i2v.params if p.name == 'model')
        self.assertEqual(option.type.convert(FLOW_VIDEO_ALIAS, option, None), FLOW_VIDEO_ALIAS)

    async def test_aspect_readback_uses_rendered_radio_text(self):
        from gflow_cli.api.transports.migrated_composer import MigratedComposer
        radios=Mock(all_text_contents=AsyncMock(return_value=['videocamVideo','crop_freeFrames','crop_9_169:16','720p','x1']),
                    all_inner_texts=AsyncMock(return_value=['videocam\nVideo','crop_free\nFrames','crop_9_16\n9:16','720p','x1']))
        pane=Mock()
        pane.get_by_role.return_value.inner_text=AsyncMock(return_value='Omni 1.2 Flash\narrow_drop_down')
        def locator(selector):
            if selector=='[role="radio"][aria-checked="true"]':return radios
            if selector=='flow-duration-slider input[type="range"]':return Mock(input_value=AsyncMock(return_value='5'))
            if selector=='flow-duration-slider .duration-slider-value':return Mock(inner_text=AsyncMock(return_value='8 sec'))
            raise AssertionError(selector)
        pane.locator.side_effect=locator
        composer=MigratedComposer()
        with patch.object(composer,'_open_pane',new=AsyncMock(return_value=pane)), \
                patch.object(composer,'_close_pane',new=AsyncMock()):
            result=await composer._omni12_readback(Mock())
        self.assertEqual(result,{'model':'Omni 1.2 Flash','duration':8,'resolution':'720p','aspect':'9:16','count':1})

    def test_wire_family_is_narrow_and_duration_resolution_are_explicit(self):
        for key in ('macaron_cookie_i2v_8s', 'macaron_cookie_i2v_8s_portrait'):
            self.assertTrue(flow_video_model_matches(key), key)
        for key in ('macaron_cookie_i2v_fl_8s', 'macaron_cookie_i2v_8s_360p',
                    'macaron_cookie_i2v_10s', 'veo_3_1_i2v_s_fast_portrait',
                    'macaron_cookie_r2v_8s', 'macaron_cookie_i2v_8s_1080p',
                    'macaron_cookie_i2v_8s_4k', 'macaron_cookie_t2v_8s', 'macaron_cookie_i2v_extend_8s'):
            self.assertFalse(flow_video_model_matches(key), key)
        with patch.dict(os.environ, {'FLOW_VIDEO_EXPLICIT_DURATION': 'false'}):
            command = FlowMediaClient().video_command('move', Path('frame.png'), Path('out.mp4'))
        self.assertEqual(command[command.index('--duration') + 1], '8')
        self.assertEqual(command[command.index('--resolution') + 1], '720p')
        self.assertIn('move\nSingle continuous shot from the provided first frame. No cuts, no scene changes.', command)

    async def test_readback_or_key_mismatch_aborts_before_forwarding_with_evidence(self):
        import flow_readback as rb
        from test_flow_video_readback import context, FRAME, PROJECT
        for change in ({'model': 'Omni 1.1 Flash'}, {'duration': 10}, {'resolution': '360p'},
                       {'aspect': '16:9'}, {'count': 2}, {'wire': 'macaron_cookie_i2v_fl_8s'}, {'rpc': 'unmeasured'}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                marker = Path(tmp) / 'flow_video_attempt_1.submit'
                marker.write_text('submit_attempted\n')
                data = context() | dict(attempt=1, request_hash='a'*64, frozen_inputs_sha256='b'*64, source_hash='c'*64)
                data['ui_readback'].update({k: v for k, v in change.items() if k not in {'wire', 'rpc'}})
                key = change.get('wire', 'macaron_cookie_i2v_8s')
                row = [[None,None,[[['synthetic']]]], key, 1, None, [None,FRAME], []]
                payload = [[row], [None]*5+[PROJECT], []]
                rpc = change.get('rpc', 'eb1hJf')
                body = 'f.req='+json.dumps([[[rpc,json.dumps(payload),None,'generic']]])
                route = Mock(abort=AsyncMock(), continue_=AsyncMock())
                with patch.dict(os.environ, {'GFLOW_SHADOW_SUBMIT_MARKER':str(marker)}):
                    request = Mock(post_data=body)
                    self.assertEqual(rb.video_submit_rpc(request), rpc)
                    self.assertIsNotNone(await rb.guard_video(route, request, data, rpc))
                route.abort.assert_awaited_once(); route.continue_.assert_not_awaited()
                saved = json.loads(marker.read_text())
                self.assertEqual(saved['state'], 'blocked_before_submission')
                self.assertEqual(saved['actual_model'], key)
                self.assertEqual(saved['ui_readback'], data['ui_readback'])

    async def test_installed_picker_and_slider_bind_exact_omni12_eight_seconds(self):
        from gflow_cli.api.video import VideoModel
        from gflow_cli.api.transports.migrated_composer import MigratedComposer, VIDEO_MODEL_MENU_MATCHERS
        model = VideoModel.from_cli('omni-1.2-flash')
        matcher = VIDEO_MODEL_MENU_MATCHERS[model]
        self.assertTrue(matcher.matches(FLOW_VIDEO_LABEL))
        self.assertFalse(matcher.matches('Omni 1.1 Flash'))
        slider = Mock(first=Mock(wait_for=AsyncMock()), count=AsyncMock(return_value=1),
            get_attribute=AsyncMock(side_effect=['0','12','1']), is_enabled=AsyncMock(return_value=True),
            press=AsyncMock(), input_value=AsyncMock(return_value='5'))
        component = Mock()
        component.locator.side_effect = [slider, Mock(inner_text=AsyncMock(return_value='8 sec'))]
        pane = Mock(locator=Mock(return_value=component))
        page = Mock(); page.locator.return_value.first.wait_for = AsyncMock()
        await MigratedComposer()._select_omni12_duration(page, pane, 8)
        self.assertEqual(slider.press.await_count, 6)
        self.assertEqual(slider.press.call_args_list[0].args, ('Home',))

    def test_cut_fails_attempt_and_cannot_be_reaccepted_by_recovery(self):
        from PIL import Image
        media = '22222222-2222-4222-8222-222222222222'
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'FLOW_API_FALLBACK_ENABLED':'false'}), \
                patch.object(FlowMediaClient, '_generation_seconds', return_value=7200), \
                patch.object(FlowMediaClient, '_omni_final_slot', return_value=False), \
                patch.object(FlowMediaClient, '_probe_video', return_value=(720,1280,8,True)), \
                patch('google_video_client.first_video_cut', return_value=1.5):
            root = Path(tmp); art = root/'input_art.png'; Image.new('RGB',(1080,1440)).save(art)
            freeze(root, video='move')
            client = FlowMediaClient(); output = root/'generated_video.mp4'
            def generated(command, timeout, marker):
                raw = root/'clip.mp4'; raw.write_bytes(b'original')
                request_hash = client._read_diagnostics(root/'flow_video_diagnostics.json')['request_hash']
                video_receipt(root, request_hash, media)
                return dict(succeeded=True, media_id=media, local_path=str(raw),
                    request={'count':1,'model':'omni_1_2_flash','mode':'i2v','aspect':'portrait'})
            with patch.object(client, '_run_json', side_effect=generated):
                with self.assertRaises(FlowCommandError) as failure:
                    client.generate_video('move', art, output)
            self.assertEqual(failure.exception.error_class, 'FlowVideoCut')
            saved = client._read_diagnostics(root/'flow_video_diagnostics.json')
            attempt = saved['attempts'][0]
            self.assertEqual(attempt['failure']['media_id'], media)
            with patch.object(client, '_local_result') as local, patch.object(client, '_readback_media') as recovery:
                self.assertIsNone(client._recover_pending('video', root/'flow_video_diagnostics.json', attempt, output))
            local.assert_not_called(); recovery.assert_not_called()
            with self.assertRaises(FlowCommandError):
                client._generate_video('move', art, output, generated(None,None,None))
            self.assertFalse(output.exists())


    async def test_unrelated_post_passes_before_and_after_submit(self):
        from unittest.mock import MagicMock
        from gflow_cli.api.transports.migrated_composer import MigratedComposer
        class StopInspection(Exception):
            pass
        callback = {}
        page = MagicMock(route=AsyncMock(), unroute=AsyncMock())
        page.context.browser = None
        button = Mock(count=AsyncMock(return_value=1), is_enabled=AsyncMock(return_value=True))
        page.locator.return_value.filter.return_value.first = button
        page.route.side_effect = lambda pattern, guard: callback.update(guard=guard)
        composer = MigratedComposer()
        composer._shadow_video_context = {'wire_verified': False}
        body = 'f.req='+json.dumps([[['Background',json.dumps([[[None,'opaque-value']]]),None,'generic']]])
        request = Mock(method='POST', url='https://flow.google.com/batchexecute?rpcids=Background',post_data=body)
        route = Mock(continue_=AsyncMock(), abort=AsyncMock())
        async def click(*args, **kwargs):
            await callback['guard'](route, request)
            composer._shadow_video_context['wire_verified'] = True
            await callback['guard'](route, request)
            raise StopInspection()
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                'GFLOW_SHADOW_SUBMIT_MARKER': str(Path(tmp)/'flow_video_attempt_1.submit')}), \
                patch('flow_readback.guard_video', new_callable=AsyncMock) as guard, \
                patch.object(composer,'_click',new=AsyncMock(side_effect=click)):
            with self.assertRaises(StopInspection):
                await composer.submit_and_observe(page,poll_timeout_s=1,on_started=None,project_id='project')
        self.assertEqual(route.continue_.await_count, 2)
        route.abort.assert_not_awaited(); guard.assert_not_awaited()

    def test_structure_evidence_retains_paths_without_private_contents(self):
        import flow_readback as rb
        frame='11111111-1111-4111-8111-111111111111'
        project='22222222-2222-4222-8222-222222222222'
        media='33333333-3333-4333-8333-333333333333'
        prompt='private fixture narrative'
        data={'frame_id':frame,'project_id':project,'media_id':media,'prompt_hash':hashlib.sha256(prompt.encode()).hexdigest()}
        private=['https://private.invalid/signed?token=secret','private-token','private-cookie']
        payload=[[[prompt,'macaron_cookie_i2v_8s',frame,project,media,private]]]
        request='f.req='+json.dumps([[['Unmeasured',json.dumps(payload),None,'generic']]])
        reply=json.dumps([['wrb.fr','eb1hJf',json.dumps(payload),None,None,'generic']])
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                'GFLOW_SHADOW_SUBMIT_MARKER':str(Path(tmp)/'flow_video_attempt_1.submit')}):
            rb.dump_video_structure([rb.video_structure(data,'request','Unmeasured',request),
                                     rb.video_structure(data,'reply','eb1hJf',reply)])
            path=Path(tmp)/'flow_video_attempt_1.structure.json'
            text=path.read_text(); saved=json.loads(text)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(saved['events'][0]['rpc_ids'], ['Unmeasured'])
        self.assertTrue(saved['events'][0]['model_keys'])
        roles={p['role'] for event in saved['events'] for p in event['identity_paths']}
        self.assertTrue({'prompt','frame_id','project_id','media_id','uuid'} <= roles)
        for value in [prompt,frame,project,media,*private]:
            self.assertNotIn(value,text)
        self.assertIn('macaron_cookie_i2v_8s',text)


class ProbeTests(unittest.TestCase):
    def now(self, hour=0, minute=0):
        return datetime(2026,11,14,hour,minute,tzinfo=ZoneInfo('Asia/Kolkata'))

    def test_window_refusal_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'STATE_ZERO_PRIVATE_ROOT':tmp}, clear=True):
            for hour, minute in ((9,30), (10,0), (14,30)):
                with self.assertRaisesRegex(ValueError, '09:30'):
                    run_probe('fixture', Path('unused'), Path('unused'), now=self.now(hour,minute))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_busy_canonical_lock_refuses_without_probe_files(self):
        from utils import ExecutionBusy
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'STATE_ZERO_PRIVATE_ROOT':tmp}, clear=True):
            with execution_lock():
                with self.assertRaises(ExecutionBusy):
                    run_probe('fixture', Path('unused'), Path('unused'), now=self.now())
            self.assertFalse((Path(tmp)/'runtime/probe').exists())

    def test_one_attempt_ever_and_outputs_stay_in_probe_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); private = root/'private'; frame = root/'frame.png'; prompt = root/'prompt.txt'
            frame.write_bytes(b'frame'); prompt.write_text('move')
            env = {'STATE_ZERO_PRIVATE_ROOT':str(private)}
            def generated(self, text, source, output):
                output.write_bytes(b'clip'); return output
            with patch.dict(os.environ, env, clear=True), \
                    patch('ops.flow_omni_probe.ProbeClient.generate_video', new=generated), \
                    patch('ops.flow_omni_probe.probe_video', return_value=(1080,1920,8,True)):
                result = run_probe('fixture',frame,prompt,now=self.now())
            folder = private/'runtime/probe/fixture'
            self.assertEqual(result['status'], 'complete')
            self.assertTrue((folder/'report.json').is_file())
            for path in private.rglob('*'):
                if path.is_file():
                    self.assertTrue(path.is_relative_to(folder) or path == private/'runtime/state/pipeline-execution.lock', str(path))
            with patch.dict(os.environ, env, clear=True), patch('ops.flow_omni_probe.ProbeClient') as client:
                with self.assertRaises(FileExistsError):
                    run_probe('fixture',frame,prompt,now=self.now())
                client.assert_not_called()

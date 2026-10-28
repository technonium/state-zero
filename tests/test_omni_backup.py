"""One-creation Omni contract and unchanged text settings; all HTTP is mocked."""
import tests
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import requests
from PIL import Image
from google_video_client import GoogleVideoClient
from google_key_router import GoogleAPIError
from flow_media_client import FlowMediaClient
from flow_fixture import freeze
from utils import probe_video, video_1080_command


class OmniBackupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.output = self.root/'generated_video.mp4'
        self.art = self.root/'art.png'; Image.new('RGB',(768,1024),'navy').save(self.art)
        env=patch.dict(os.environ,{'GOOGLE_API_KEY_PRIMARY':'fixture-primary',
            'GOOGLE_API_KEY_FALLBACK':'fixture-fallback','GOOGLE_API_FALLBACK_ENABLED':'true'},clear=True)
        env.start();self.addCleanup(env.stop)
        self.client=GoogleVideoClient()

    def reply(self, *, status='completed', part=None, identity='interaction/opaque?value'):
        if part is None:part={'type':'video','mime_type':'video/mp4','data':base64.b64encode(b'original-video').decode()}
        return {'id':identity,'status':status,'steps':[{'type':'model_output','content':[part]}]}

    def response(self, data, code=200):
        return Mock(status_code=code,json=Mock(return_value=data))

    def saved(self):
        return json.loads((self.root/'google_video_diagnostics.json').read_text())

    @staticmethod
    def process(original, target):
        target.write_bytes(b'processed');return target

    def test_request_reservation_id_and_inline_primary(self):
        def post(url, **kwargs):
            saved=self.saved();self.assertTrue(saved['submission_started']);self.assertEqual(saved['protocol'],'interactions')
            self.assertTrue(self.client.router.submission_started)
            self.assertEqual(url,'https://generativelanguage.googleapis.com/v1beta/interactions')
            self.assertFalse(kwargs['allow_redirects']);self.assertLessEqual(kwargs['timeout'],120)
            body=kwargs['json'];self.assertEqual(body['model'],'gemini-omni-1.1-flash')
            self.assertEqual(body['response_format'],{'type':'video','aspect_ratio':'9:16','resolution':'720p','duration':'8s','delivery':'inline'})
            self.assertEqual(body['generation_config'],{'video_config':{'task':'image_to_video'}})
            self.assertTrue(body['background']);self.assertTrue(body['store'])
            self.assertEqual(body['input'][1],{'type':'text','text':'unchanged prompt'})
            frame,_=self.client._to_letterboxed_png_bytes(self.art)
            self.assertEqual(base64.b64decode(body['input'][0]['data']),frame)
            return self.response(self.reply(part={'type':'video','mime_type':'video/mp4',
                'data':base64.b64encode(b'original-video').decode(),'uri':'https://unused.example'}))
        def process(original,target):
            self.assertEqual(self.saved()['interaction_id'],'interaction/opaque?value')
            self.assertEqual(original.read_bytes(),b'original-video')
            return self.process(original,target)
        with patch('google_video_client.requests.post',side_effect=post) as create, \
                patch('google_video_client.requests.get') as get,patch.object(self.client,'_process_original',side_effect=process):
            self.assertEqual(self.client.generate_from_image('unchanged prompt',self.art,self.output),self.output)
        create.assert_called_once();get.assert_not_called()
        self.assertEqual(self.saved()['original_sha256'],hashlib.sha256(b'original-video').hexdigest())

    def test_lost_creation_response_and_rejections_never_repeat_or_switch_key(self):
        for failure in [requests.Timeout('lost'),401,403,429,500,'missing-id','bad-json']:
            with self.subTest(failure=failure):
                (self.root/'google_video_diagnostics.json').unlink(missing_ok=True)
                client=GoogleVideoClient()
                response=self.response({} if failure=='missing-id' else None, failure if type(failure) is int else 200)
                if failure=='bad-json':response.json.side_effect=ValueError('bad-json')
                with patch('google_video_client.requests.post',side_effect=failure if isinstance(failure,Exception) else None,
                           return_value=response) as create,patch('google_video_client.requests.get') as get:
                    with self.assertRaises(Exception):client.generate_from_image('prompt',self.art,self.output)
                    with self.assertRaises(GoogleAPIError):GoogleVideoClient().generate_from_image('prompt',self.art,self.output)
                    with self.assertRaises(GoogleAPIError):GoogleVideoClient().resume_operation(self.output)
                create.assert_called_once();get.assert_not_called();self.assertTrue(self.saved()['submission_started'])

    def test_failed_reservation_write_blocks_post_and_failed_id_save_blocks_retry(self):
        with patch.object(self.client,'_write_diagnostics',side_effect=OSError('save')), \
                patch('google_video_client.requests.post') as post:
            with self.assertRaises(OSError):self.client.generate_from_image('prompt',self.art,self.output)
        post.assert_not_called()
        original=self.client._write_diagnostics
        def save(path, update):
            if 'interaction_id' in update:raise OSError('ID save failed')
            original(path,update)
        with patch.object(self.client,'_write_diagnostics',side_effect=save), \
                patch('google_video_client.requests.post',return_value=self.response(self.reply())) as post:
            with self.assertRaises(OSError):self.client.generate_from_image('prompt',self.art,self.output)
            with self.assertRaises(GoogleAPIError):GoogleVideoClient().generate_from_image('prompt',self.art,self.output)
        post.assert_called_once();self.assertNotIn('interaction_id',self.saved())

    def test_restart_get_only_and_original_deadline_not_renewed(self):
        with patch('google_video_client.requests.post',return_value=self.response(self.reply(status='in_progress'))) as create, \
                patch('google_video_client.requests.get',side_effect=requests.Timeout('poll')),patch('google_video_client.time.sleep'):
            with self.assertRaises(requests.Timeout):self.client.generate_from_image('prompt',self.art,self.output)
        deadline=self.saved()['generation_deadline'];resumed=GoogleVideoClient();resumed.generation_deadline=deadline+5000
        with patch('google_video_client.requests.post') as post, \
                patch('google_video_client.requests.get',return_value=self.response(self.reply())) as get, \
                patch.object(resumed,'_process_original',side_effect=self.process):
            resumed.resume_operation(self.output)
        post.assert_not_called();self.assertEqual(resumed.generation_deadline,deadline)
        self.assertIn('interaction%2Fopaque%3Fvalue',get.call_args.args[0]);create.assert_called_once()

    def test_expired_deadline_cannot_poll_but_local_original_can_process(self):
        self.client._write_diagnostics(self.output,{'protocol':'interactions','interaction_id':'id','submission_started':True,
            'generation_deadline':time.time()-1,'key_label':'primary','key_sha256':hashlib.sha256(b'fixture-primary').hexdigest()})
        with patch('google_video_client.requests.post') as post,patch('google_video_client.requests.get') as get:
            with self.assertRaises(GoogleAPIError):self.client.resume_operation(self.output)
        post.assert_not_called();get.assert_not_called()
        original=self.output.with_suffix('.original.mp4');original.write_bytes(b'local')
        self.client._write_diagnostics(self.output,{'original_sha256':hashlib.sha256(b'local').hexdigest()})
        with patch.object(self.client,'_process_original',side_effect=self.process),patch('google_video_client.requests.get') as get:
            self.client.resume_operation(self.output)
        get.assert_not_called()
        original.write_bytes(b'changed')
        with self.assertRaises(GoogleAPIError):self.client.resume_operation(self.output)

    def test_terminal_states_identity_and_ambiguous_output_never_create_again(self):
        cases=[self.reply(status=s) for s in ('failed','cancelled','incomplete','requires_action','budget_exceeded','unknown')]
        bad=self.reply();bad['steps'][0]['content']*=2;cases += [bad,self.reply(part={'type':'video','mime_type':'video/mp4','data':'!!!'})]
        for data in cases:
            with self.subTest(status=data['status']):
                (self.root/'google_video_diagnostics.json').unlink(missing_ok=True)
                with patch('google_video_client.requests.post',return_value=self.response(data)) as create:
                    with self.assertRaises(Exception):GoogleVideoClient().generate_from_image('prompt',self.art,self.output)
                create.assert_called_once()
        saved=self.saved();saved['interaction_id']='expected';(self.root/'google_video_diagnostics.json').write_text(json.dumps(saved))
        with patch('google_video_client.requests.get',return_value=self.response(self.reply(identity='wrong'))):
            with self.assertRaises(GoogleAPIError):self.client.resume_operation(self.output)

    def test_corrupt_record_and_changed_original_key_fail_closed(self):
        path=self.root/'google_video_diagnostics.json';path.write_text('bad')
        with patch('google_video_client.requests.post') as post:
            with self.assertRaises(GoogleAPIError):self.client.generate_from_image('prompt',self.art,self.output)
        post.assert_not_called();path.unlink()
        self.client._write_diagnostics(self.output,{'protocol':'interactions','interaction_id':'id','generation_deadline':time.time()+100,
            'key_label':'primary','key_sha256':'changed'})
        with patch('google_video_client.requests.get') as get:
            with self.assertRaises(GoogleAPIError):self.client.resume_operation(self.output)
        get.assert_not_called()

    def test_uri_backup_processing_and_signed_download_have_scoped_key(self):
        responses=[self.response({'state':'PROCESSING'}),self.response({'state':'ACTIVE','downloadUri':'https://signed.example/clip?signature=fixture'}),
                   Mock(status_code=200,content=b'original-video')]
        part={'type':'video','mime_type':'video/mp4','uri':'https://generativelanguage.googleapis.com/v1beta/files/exact:download?alt=media'}
        with patch('google_video_client.requests.post',return_value=self.response(self.reply(part=part))), \
                patch('google_video_client.requests.get',side_effect=responses) as get,patch('google_video_client.time.sleep'), \
                patch.object(self.client,'_process_original',side_effect=self.process):
            self.client.generate_from_image('prompt',self.art,self.output)
        self.assertEqual(get.call_count,3)
        for call in get.call_args_list[:2]:self.assertEqual(call.kwargs['headers'],{'x-goog-api-key':'fixture-primary'})
        self.assertEqual(get.call_args_list[2].kwargs['headers'],{})

    def test_uri_file_failure_and_redirect_limit_downgrade_and_lookalike(self):
        with patch('google_video_client.requests.get',return_value=self.response({'state':'FAILED'})):
            with self.assertRaises(GoogleAPIError):self.client._active_file_uri('https://generativelanguage.googleapis.com/v1beta/files/exact','key')
        with patch('google_video_client.requests.get',return_value=Mock(status_code=302,headers={'Location':'https://signed.example/next'})) as get:
            with self.assertRaises(GoogleAPIError):self.client._download_video('https://storage.googleapis.com/start','key',self.output)
        self.assertEqual(get.call_count,6)
        self.assertEqual(get.call_args_list[1].kwargs['headers'],{})
        for uri in ('http://storage.googleapis.com/clip','https://user@storage.googleapis.com/clip','https://signed.example/clip?key=fixture-primary'):
            with patch('google_video_client.requests.get') as get:
                with self.assertRaises(GoogleAPIError):self.client._download_video(uri,'fixture-primary',self.output)
            get.assert_not_called()
        with patch('google_video_client.requests.get',return_value=Mock(status_code=200,content=b'video')) as get:
            self.client._download_video('https://googleapis.com.example/clip','fixture-primary',self.output)
        self.assertEqual(get.call_args.kwargs['headers'],{})

    def test_duration_dimensions_audio_rejection_preserves_original_without_retry(self):
        original=self.output.with_suffix('.original.mp4');original.write_bytes(b'original')
        for properties in ((720,1280,7.49,True),(720,1280,8.51,True),(1280,720,8,True),(720,1280,8,False)):
            with patch('google_video_client.probe_video',return_value=properties),patch('google_video_client.requests.post') as post, \
                    patch('google_video_client.run_bounded') as scale:
                with self.assertRaises(GoogleAPIError):self.client._process_original(original,self.output)
            post.assert_not_called();scale.assert_not_called();self.assertTrue(original.exists())

    def test_real_fixture_scaling_uses_existing_flow_command_without_mask(self):
        original=self.output.with_suffix('.original.mp4')
        subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-f','lavfi','-i','color=c=blue:s=720x1280:r=2',
            '-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t','8','-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p','-c:a','aac',str(original)],check=True)
        self.client._process_original(original,self.output)
        width,height,duration,audio=probe_video(self.output)
        self.assertEqual((width,height,audio),(1080,1920,True));self.assertTrue(7.5<=duration<=8.5)
        command=video_1080_command(original,self.output)
        self.assertEqual(command,['ffmpeg','-nostdin','-y','-i',str(original),'-vf','scale=1080:1920:flags=lanczos',
            '-c:v','libx264','-preset','medium','-crf','18','-pix_fmt','yuv420p','-c:a','copy','-movflags','+faststart',str(self.output)])

    def test_api_default_local_original_reprocesses_after_remote_cutoff(self):
        freeze(self.root,video='prompt')
        parent=FlowMediaClient()
        with patch.object(parent,'_generation_seconds',return_value=7200), \
                patch('google_video_client.requests.post',return_value=self.response(self.reply())) as create, \
                patch.object(GoogleVideoClient,'_process_original',side_effect=TimeoutError('encoding interrupted')):
            with self.assertRaises(TimeoutError):parent.generate_api_stage('video','prompt',self.output,self.art)
        saved=self.saved();saved['generation_deadline']=time.time()-1
        (self.root/'google_video_diagnostics.json').write_text(json.dumps(saved))
        with patch.object(parent,'_generation_seconds',return_value=0), \
                patch('google_video_client.requests.post') as post,patch('google_video_client.requests.get') as get, \
                patch.object(GoogleVideoClient,'_process_original',side_effect=self.process), \
                patch.object(parent,'_probe_video',return_value=(1080,1920,8,True)):
            parent.generate_api_stage('video','prompt',self.output,self.art)
        create.assert_called_once();post.assert_not_called();get.assert_not_called()
        self.assertTrue(self.saved()['selected_sha256'])

    def test_api_default_parent_reservation_precedes_single_post_and_resumes_id(self):
        freeze(self.root,video='prompt')
        def post(*args,**kwargs):
            self.assertEqual(self.saved()['logical_attempt'],'reserved')
            return self.response(self.reply(status='in_progress'))
        parent=FlowMediaClient()
        with patch.object(parent,'_generation_seconds',return_value=7200), \
                patch('google_video_client.requests.post',side_effect=post) as create, \
                patch('google_video_client.requests.get',side_effect=requests.Timeout('poll')),patch('google_video_client.time.sleep'):
            with self.assertRaises(requests.Timeout):parent.generate_api_stage('video','prompt',self.output,self.art)
        with patch.object(parent,'_generation_seconds',return_value=7200), \
                patch('google_video_client.requests.post') as post, \
                patch('google_video_client.requests.get',return_value=self.response(self.reply())), \
                patch.object(GoogleVideoClient,'_process_original',side_effect=self.process), \
                patch.object(parent,'_probe_video',return_value=(1080,1920,8,True)):
            parent.generate_api_stage('video','prompt',self.output,self.art)
        create.assert_called_once();post.assert_not_called();self.assertTrue(self.saved()['selected_sha256'])

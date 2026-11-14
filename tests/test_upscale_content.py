"""Optional Flow upscale: strict UI/origin checks and timestamp-based content proof."""
import tests
import asyncio
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from flow_media_client import FlowMediaClient
from ops.flow_video_upscale import download_existing
from playwright.async_api import async_playwright


class UpscaleContentTests(unittest.TestCase):
    def test_recovered_video_uses_same_upscale_method_and_records_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'original.mp4';source.write_bytes(b'original')
            candidate=root/'upscale.mp4';candidate.write_bytes(b'upscale');out=root/'video.mp4'
            payload={'succeeded':True,'local_path':str(source),'media_id':'media','workflow_id':'workflow',
                     'request':{'count':1,'model':'omni_1_2_flash','mode':'i2v','aspect':'portrait'}}
            c=FlowMediaClient()
            with patch.object(c,'_validate_original',return_value=(720,1280,8,True)), \
                    patch('google_video_client.first_video_cut',return_value=None), \
                    patch.object(c,'_try_flow_video_upscale',return_value=(candidate,'validated',{'ssim':[.95]*3})) as upscale:
                c._generate_video('move',root/'art.png',out,recovered=payload)
            self.assertEqual(upscale.call_args.args[1],payload)
            self.assertEqual(out.read_bytes(),b'upscale')
            report=json.loads((root/'flow_video_diagnostics.json').read_text())
            self.assertEqual(report['upscale_status'],'validated');self.assertEqual(report['upscale_evidence']['ssim'],[.95]*3)

    def test_content_validation_and_optional_failures_keep_original(self):
        cases=[('matching',(1080,1920,8,True),[.95,.96,.94],'validated'),
               ('different',(1080,1920,8,True),[.6,.5,.7],'content_mismatch'),
               ('size',(720,1280,8,True),[.95]*3,'invalid_media'),
               ('duration',(1080,1920,8.6,True),[.95]*3,'invalid_media'),
               ('audio',(1080,1920,8,False),[.95]*3,'invalid_media'),
               ('timeout',(1080,1920,8,True),[.95]*3,'unavailable'),
               ('low_budget',(1080,1920,8,True),[.95]*3,'low_budget')]
        for name,probe,scores,expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{
                    'FLOW_VIDEO_UPSCALE_1080P':'true','FLOW_PREFLIGHT_PROJECT_ID':'project','FLOW_UPSCALE_MIN_SSIM':'.90'}):
                root=Path(tmp);source=root/'original.mp4';source.write_bytes(b'original');out=root/'out.mp4'
                c=FlowMediaClient()
                def run(command,**kw):
                    self.assertEqual(command[2:4],['project','media'])
                    self.assertEqual(command[command.index('--workflow')+1],'workflow')
                    self.assertEqual(kw['timeout'],240)
                    if name=='timeout':raise subprocess.TimeoutExpired(command,240)
                    Path(command[4]).write_bytes(b'candidate')
                    return subprocess.CompletedProcess(command,0,stdout='prefixed info\n{"status":"downloaded"}\n')
                with patch.object(c,'_generation_seconds',return_value=239 if name=='low_budget' else 300), \
                        patch.object(c,'_check_browser_ownership'),patch.object(c,'_probe_video',return_value=probe), \
                        patch.object(c,'_upscale_ssim',return_value=scores), \
                        patch('flow_media_client.run_bounded',side_effect=run) as helper:
                    selected,status,evidence=c._try_flow_video_upscale(source,{'media_id':'media','workflow_id':'workflow'},
                        out,root/'flow_video_diagnostics.json',width=720,duration=8,has_audio=True)
                self.assertEqual(status,expected)
                self.assertEqual(selected==source,expected!='validated')
                self.assertEqual(source.read_bytes(),b'original')
                if name=='low_budget':helper.assert_not_called()

    def test_ffmpeg_compares_three_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name,color,size in [('original','red','720x1280'),('match','red','1080x1920'),('other','white','720x1280')]:
                subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'color=c={color}:s={size}:r=1',
                    '-t','8','-c:v','libx264','-preset','ultrafast','-threads','1',str(root/(name+'.mp4'))],check=True)
            positive=FlowMediaClient._upscale_ssim(root/'original.mp4',root/'match.mp4',time.time()+60)
            negative=FlowMediaClient._upscale_ssim(root/'other.mp4',root/'match.mp4',time.time()+60)
            self.assertEqual(len(positive),3);self.assertTrue(all(x>=.90 for x in positive),positive)
            self.assertTrue(all(x<=.75 for x in negative),negative)

"""In-page export capture never invokes Chrome downloads and always restores hooks."""
import tests
import base64
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock,MagicMock,patch

from flow_media_client import FlowMediaClient
from ops.flow_video_upscale import download_existing

class CaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_success_timeout_invalid_and_exception_restore_hooks(self):
        for case in ('valid','timeout','non_mp4','failure'):
            with self.subTest(case=case),tempfile.TemporaryDirectory() as tmp:
                client=MagicMock();client.__aenter__=AsyncMock(return_value=client);client.__aexit__=AsyncMock(return_value=False)
                page=client.page;page.goto=AsyncMock();page.url='https://flow.google.com/project/project/edit/media'
                button=MagicMock();button.locator.return_value=button;button.first.wait_for=AsyncMock()
                button.count=AsyncMock(return_value=1);button.click=AsyncMock()
                choice=MagicMock();choice.locator.return_value=choice;choice.count=AsyncMock(return_value=1)
                choice.is_visible=AsyncMock(return_value=True);choice.is_enabled=AsyncMock(return_value=True)
                choice.inner_text=AsyncMock(return_value='1080p Upscaled');choice.click=AsyncMock()
                page.get_by_role.side_effect=[button,choice]
                calls=[]
                async def evaluate(js,*args):
                    calls.append(js)
                    if js=='() => window._gflowCapturedBase64':
                        if case=='failure':raise RuntimeError('closed')
                        return None if case=='timeout' else base64.b64encode(
                            b'0000ftyp'+b'0'*1200 if case=='valid' else b'not an mp4').decode()
                page.evaluate=AsyncMock(side_effect=evaluate)
                output=Path(tmp)/'out.mp4'
                with patch('gflow_cli.api.client.FlowApiClient',return_value=client), \
                        patch('flow_readback.snapshot',new=AsyncMock(return_value={})), \
                        patch('flow_readback.select_video',return_value={'workflow_id':'workflow'}), \
                        patch.object(FlowMediaClient,'_probe_video',return_value=(1080,1920,8,True)):
                    kwargs={'workflow_id':'workflow','deadline':time.time()+(.02 if case=='timeout' else 3)}
                    if case=='failure':
                        with self.assertRaises(RuntimeError) as caught:
                            await download_existing('project','media',output,**kwargs)
                        self.assertEqual(caught.exception.upscale_step,'capture')
                    else:
                        self.assertEqual(await download_existing('project','media',output,**kwargs),
                                         {'valid':'downloaded','timeout':'timeout','non_mp4':'invalid_media'}[case])
                self.assertEqual(output.exists(),case=='valid')
                self.assertIn('window._gflowOrigCreateObjectURL',calls[0])
                self.assertIn('delete window._gflowCapturedBase64',calls[-1])
                page.expect_download.assert_not_called()

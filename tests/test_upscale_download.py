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


class UpscaleDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_canvas_menu_and_transfer_boundaries(self):
        cases = [(1,'1080p Upscaled','downloaded'),(2,'1080p Upscaled','ambiguous_control'),
                 (1,'','not_offered'),(1,'1080p Upscaled Upgrade','not_offered')]
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(channel='chrome', headless=True, args=['--no-sandbox'])
            try:
                for buttons, label, expected in cases:
                    with self.subTest(buttons=buttons,label=label), tempfile.TemporaryDirectory() as tmp:
                        page = await browser.new_page()
                        html = '<button>Download media</button>'*buttons
                        html += (f'<button role="menuitem">{label}</button>' if label else '')
                        html += '<button role="menuitem">4K Upscaled <span>Upgrade</span></button>'
                        async def serve(route):
                            await route.fulfill(content_type='text/html',body=html)
                        await page.route('**/*',serve)
                        client = MagicMock(page=page)
                        client.__aenter__ = AsyncMock(return_value=client);client.__aexit__ = AsyncMock(return_value=False)
                        original_goto=page.goto
                        async def goto(*args,**kw):
                            await original_goto(*args,**kw)
                            await page.evaluate("""() => {
                                window.savedCreate=URL.createObjectURL;
                                window.savedClick=HTMLAnchorElement.prototype.click;
                                const choice=document.querySelector('[role=menuitem]');
                                if(choice)choice.onclick=()=>{
                                    const bytes=new Uint8Array(1200);bytes.set([102,116,121,112],4);
                                    const a=document.createElement('a');a.download='clip.mp4';
                                    a.href=URL.createObjectURL(new Blob([bytes],{type:'video/mp4'}));a.click();
                                };
                            }""")
                        downloads=[];page.on('download',lambda d:downloads.append(d))
                        output = Path(tmp)/'candidate.mp4'
                        with patch('gflow_cli.api.client.FlowApiClient',return_value=client), \
                                patch('flow_readback.snapshot',new=AsyncMock(return_value={})), \
                                patch('flow_readback.select_video',return_value={'workflow_id':'workflow'}), \
                                patch.object(page,'goto',side_effect=goto), \
                                patch.object(FlowMediaClient,'_probe_video',return_value=(1080,1920,8,True)):
                            self.assertEqual(await download_existing('project','media',output,workflow_id='workflow'),expected)
                        self.assertEqual(output.exists(),expected=='downloaded')
                        self.assertEqual(downloads,[])
                        self.assertTrue(await page.evaluate('() => URL.createObjectURL===window.savedCreate && HTMLAnchorElement.prototype.click===window.savedClick && !window._capturing'))
                        await page.close()
            finally:
                await browser.close()

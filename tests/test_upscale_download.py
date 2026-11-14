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
        cases = [
            ('blob:https://flow.google.com/uuid', 1, '1080p Upscaled', 'downloaded'),
            ('https://flow-content.google/video/workflow', 1, '1080p Upscaled', 'downloaded'),
            ('blob:https://elsewhere.test/uuid', 1, '1080p Upscaled', 'transfer_identity_unverified'),
            ('https://elsewhere.test/video/workflow', 1, '1080p Upscaled', 'transfer_identity_unverified'),
            ('https://flow-content.google/video/other', 1, '1080p Upscaled', 'transfer_identity_unverified'),
            ('blob:https://flow.google.com/uuid', 2, '1080p Upscaled', 'ambiguous_control'),
            ('blob:https://flow.google.com/uuid', 1, '', 'not_offered'),
            ('blob:https://flow.google.com/uuid', 1, '1080p Upscaled Upgrade', 'not_offered'),
        ]
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(channel='chrome', headless=True, args=['--no-sandbox'])
            try:
                for url, buttons, label, expected in cases:
                    with self.subTest(url=url,buttons=buttons,label=label), tempfile.TemporaryDirectory() as tmp:
                        page = await browser.new_page()
                        html = '<button>Download media</button>'*buttons
                        html += (f'<button role="menuitem">{label}</button>' if label else '')
                        html += '<button role="menuitem">4K Upscaled <span>Upgrade</span></button>'
                        async def serve(route):
                            await route.fulfill(content_type='text/html',body=html)
                        await page.route('**/*',serve)
                        client = MagicMock(page=page)
                        client.__aenter__ = AsyncMock(return_value=client);client.__aexit__ = AsyncMock()
                        download = MagicMock(url=url)
                        async def save(path):Path(path).write_bytes(b'upscale')
                        download.save_as = AsyncMock(side_effect=save)
                        future = asyncio.get_running_loop().create_future();future.set_result(download)
                        event = MagicMock();event.__aenter__ = AsyncMock(return_value=MagicMock(value=future))
                        event.__aexit__ = AsyncMock()
                        output = Path(tmp)/'candidate.mp4'
                        with patch('gflow_cli.api.client.FlowApiClient',return_value=client), \
                                patch('flow_readback.snapshot',new=AsyncMock(return_value={})), \
                                patch('flow_readback.select_video',return_value={'workflow_id':'workflow'}), \
                                patch.object(page,'expect_download',return_value=event), \
                                patch.object(FlowMediaClient,'_probe_video',return_value=(1080,1920,8,True)):
                            self.assertEqual(await download_existing('project','media',output,workflow_id='workflow'),expected)
                        self.assertEqual(output.exists(),expected=='downloaded')
                        await page.close()
            finally:
                await browser.close()


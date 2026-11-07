"""Offline DOM fixtures exercise the installed original-blob helper; no provider traffic."""
import tests
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from gflow_cli.api.transports.migrated_recover import _download_original_verified
from gflow_cli.errors import WireFormatError
from playwright.async_api import async_playwright


class OriginalBlobTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixture_download_and_failures_restore_hooks_without_native_download(self):
        body=b'0000ftyp00000000'
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(channel='chrome',headless=True,args=['--no-sandbox'])
            try:
                for size,mime in ((16,'video/mp4'),(15,'video/mp4'),(16,'text/plain')):
                    with self.subTest(size=size,mime=mime):
                        page=await browser.new_page()
                        async def serve(route):
                            await route.fulfill(status=200, content_type='text/html', body=
                                '<button>Download media</button><button role="menuitem" hidden>720p Original size</button>')

                        await page.route('**/*', serve)
                        await page.goto('https://flow.google.com/project/project/edit/media')
                        await page.evaluate("""({bytes, mime}) => {
                            window.originalCreate = URL.createObjectURL;
                            window.originalClick = HTMLAnchorElement.prototype.click;
                            const [menu, original] = document.querySelectorAll('button');
                            menu.onclick = () => { original.hidden = false; };
                            original.onclick = () => {
                                const a = document.createElement('a');
                                a.href = URL.createObjectURL(new Blob([new Uint8Array(bytes)], {type:mime}));
                                a.download = 'original.mp4';
                                a.click();
                                URL.revokeObjectURL(a.href);
                            };
                        }""", {'bytes': list(body[:size]), 'mime': mime})
                        downloads=[];page.on('download',lambda d:downloads.append(d))
                        work = _download_original_verified(page, expected=16, media_id='media')
                        if (size,mime)==(16,'video/mp4'):
                            self.assertEqual(await asyncio.wait_for(work, 8), body)
                        else:
                            with self.assertRaises(WireFormatError):
                                await asyncio.wait_for(work, 8)
                        self.assertTrue(await page.evaluate('() => URL.createObjectURL===window.originalCreate && HTMLAnchorElement.prototype.click===window.originalClick && !window.__stateZeroOriginalDownload'))
                        self.assertEqual(downloads,[])
                        await page.close()
            finally:
                await browser.close()

    async def test_click_failure_still_restores_capture(self):
        page=MagicMock(url='https://flow.google.com/project/project/edit/media')
        page.evaluate=AsyncMock()
        page.get_by_role.return_value.click=AsyncMock(side_effect=TimeoutError('click timeout'))
        with self.assertRaisesRegex(TimeoutError,'click timeout'):
            await _download_original_verified(page,expected=16,media_id='media')
        self.assertEqual(page.evaluate.await_count,2)
        self.assertIn('delete window.__stateZeroOriginalDownload',page.evaluate.call_args.args[0])
        page.expect_download.assert_not_called()

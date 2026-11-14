#!/usr/bin/env python3
"""Try one proven clip's optional provider download; preserve the original on failure."""
import argparse
import base64
import asyncio
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
from state_zero_models import FLOW_VIDEO_RESULT


async def install_capture_hooks(page, expected_type) -> None:
    """Keep the blob Flow hands to its download link, and stop the link reaching Downloads.

    The page reads the reply with ``fetch``, so the blob survives when Chrome evicts the
    DevTools copy of the body (#1027). ``expected_type`` is matched against the blob MIME.
    """
    await page.evaluate(
        """(expected) => {
            window._gflowCapturedBase64 = null;
            window._capturing = true;
            window._gflowOrigCreateObjectURL = URL.createObjectURL;
            window._gflowOrigAnchorClick = HTMLAnchorElement.prototype.click;

            URL.createObjectURL = function(blob) {
                if (window._capturing && blob && blob.size > 1000) {
                    const mime = (blob.type || '').toLowerCase();
                    if (mime.includes(expected) || (expected === 'video' && mime.includes('mp4'))) {
                        const reader = new FileReader();
                        reader.onloadend = function() {
                            if (!window._capturing) return;  // a late read from a finished run
                            window._gflowCapturedBase64 = reader.result.split(',')[1];
                        };
                        reader.readAsDataURL(blob);
                    }
                }
                return window._gflowOrigCreateObjectURL.call(URL, blob);
            };

            HTMLAnchorElement.prototype.click = function() {};
        }""",
        expected_type,
    )

async def restore_capture_hooks(page) -> None:
    try:
        await page.evaluate("""() => {
            window._capturing = false;
            delete window._gflowCapturedBase64;
            if (window._gflowOrigCreateObjectURL) {
                URL.createObjectURL = window._gflowOrigCreateObjectURL;
                delete window._gflowOrigCreateObjectURL;
            }
            if (window._gflowOrigAnchorClick) {
                HTMLAnchorElement.prototype.click = window._gflowOrigAnchorClick;
                delete window._gflowOrigAnchorClick;
            }
        }""")
    except Exception:  # noqa: BLE001 - best-effort restore on a page that may be gone
        pass

async def download_existing(project_id, media_id, output, *, workflow_id=None, deadline=None):
    step='setup'
    try:
        from gflow_cli.api.client import FlowApiClient
        from gflow_cli.config import get_settings
        from flow_readback import snapshot, select_video, ReadbackIdentityError
        from flow_media_client import FlowMediaClient
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        if not workflow_id:
            return 'identity_unavailable'
        deadline = deadline or time.time()+240
        profile = get_settings().profile_subdir(os.getenv('GFLOW_PROFILE', 'shadow'))
        async with FlowApiClient(profile_dir=profile, headless=False) as client:
            step='listing'
            listing = await snapshot(client._context, project_id, min(deadline,time.time()+30))
            clip = select_video(listing, dict(project_id=project_id,model=FLOW_VIDEO_RESULT,
                aspect='portrait',count=1,duration=8),media_id=media_id,workflow_id=workflow_id)
            step='editor'
            page=client.page
            route=f'/project/{project_id}/edit/{media_id}'
            await page.goto('https://flow.google.com'+route,wait_until='domcontentloaded',
                            timeout=max(1,(deadline-time.time())*1000))
            current=urlsplit(page.url)
            if current.scheme!='https' or current.hostname!='flow.google.com' or current.path!=route:
                raise ReadbackIdentityError('Optional download editor identity differs')
            step='button'
            button=page.get_by_role('button',name='Download media',exact=True).locator('visible=true')
            try:
                await button.first.wait_for(state='visible',timeout=max(1,min(30,deadline-time.time())*1000))
            except PlaywrightTimeoutError:
                return 'ambiguous_control'
            if await button.count()!=1:
                return 'ambiguous_control'
            await button.click()
            step='menu'
            choice=page.get_by_role('menuitem',name=re.compile(r'^1080p',re.I)).locator('visible=true')
            if await choice.count()!=1 or not await choice.is_visible() or not await choice.is_enabled() or 'upgrade' in (await choice.inner_text()).lower():
                return 'not_offered'
            staged=output.with_name(output.stem+'.download.mp4')
            try:
                step='install_capture'
                capture_deadline=min(deadline,time.time()+150)
                started=time.monotonic()
                try:
                    await install_capture_hooks(page,'video')
                    step='click_1080p'
                    await choice.click()
                    step='capture'
                    captured=None
                    while time.time()<capture_deadline:
                        await asyncio.sleep(min(1,max(0,capture_deadline-time.time())))
                        remaining=capture_deadline-time.time()
                        if remaining<=0:
                            break
                        transfer_started=time.monotonic()
                        try:
                            captured=await asyncio.wait_for(
                                page.evaluate('() => window._gflowCapturedBase64'),remaining)
                        except asyncio.TimeoutError:
                            return 'timeout'
                        transfer_seconds=time.monotonic()-transfer_started
                        if captured:
                            break
                    if not captured:
                        return 'timeout'
                finally:
                    await restore_capture_hooks(page)
                step='decode'
                try:
                    video=base64.b64decode(captured,validate=True)
                except (ValueError,TypeError):
                    return 'invalid_media'
                if len(video)<8 or video[4:8]!=b'ftyp':
                    return 'invalid_media'
                print(json.dumps({'capture_bytes':len(video),'capture_base64_chars':len(captured),
                    'capture_seconds':time.monotonic()-started,'capture_transfer_seconds':transfer_seconds}),flush=True)
                staged.write_bytes(video)
                step='probe'
                width,height,duration,audio=FlowMediaClient._probe_video(staged,timeout=max(.001,min(30,deadline-time.time())))
                if (width,height)!=(1080,1920) or not 7.5<=duration<=8.5 or not audio:
                    return 'invalid_media'
                staged.replace(output)
                step='cleanup'
                return 'downloaded'
            finally:
                staged.unlink(missing_ok=True)
    except asyncio.CancelledError as cancelled:
        exc=TimeoutError()
        exc.upscale_step=step
        raise exc from cancelled
    except Exception as exc:
        exc.upscale_step=step
        raise


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('project');parser.add_argument('media');parser.add_argument('output',type=Path)
    parser.add_argument('--workflow');parser.add_argument('--deadline',type=float,default=None)
    args=parser.parse_args()
    if not all(re.fullmatch(r'[A-Za-z0-9_-]+',v or '') for v in (args.project,args.media,args.workflow)):
        return 2
    deadline=args.deadline or time.time()+240
    try:
        status=asyncio.run(asyncio.wait_for(download_existing(args.project,args.media,args.output,
            workflow_id=args.workflow,deadline=deadline),max(.001,deadline-time.time())))
    except Exception as exc:
        status='unavailable_'+type(exc).__name__+'@'+getattr(exc,'upscale_step','setup')
    print(json.dumps({'status':status}))
    return 0 if status=='downloaded' else 3


if __name__=='__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Try one proven clip's optional provider download; preserve the original on failure."""
import argparse
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


async def download_existing(project_id, media_id, output, *, workflow_id=None, deadline=None):
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
        listing = await snapshot(client._context, project_id, min(deadline,time.time()+30))
        clip = select_video(listing, dict(project_id=project_id,model=FLOW_VIDEO_RESULT,
            aspect='portrait',count=1,duration=8),media_id=media_id,workflow_id=workflow_id)
        page=client.page
        route=f'/project/{project_id}/edit/{media_id}'
        await page.goto('https://flow.google.com'+route,wait_until='domcontentloaded',
                        timeout=max(1,(deadline-time.time())*1000))
        current=urlsplit(page.url)
        if current.scheme!='https' or current.hostname!='flow.google.com' or current.path!=route:
            raise ReadbackIdentityError('Optional download editor identity differs')
        # The canvas has no video source; bind the listing/editor, then verify bytes in the caller.
        expected_path='/video/'+clip['workflow_id']
        button=page.get_by_role('button',name='Download media',exact=True).locator('visible=true')
        try:
            await button.first.wait_for(state='visible',timeout=max(1,min(30,deadline-time.time())*1000))
        except PlaywrightTimeoutError:
            return 'ambiguous_control'
        if await button.count()!=1:
            return 'ambiguous_control'
        await button.click()
        choice=page.get_by_role('menuitem',name=re.compile(r'^1080p',re.I)).locator('visible=true')
        if await choice.count()!=1 or not await choice.is_visible() or not await choice.is_enabled() or 'upgrade' in (await choice.inner_text()).lower():
            return 'not_offered'
        async with page.expect_download(timeout=max(1,(deadline-time.time())*1000)) as event:
            await choice.click()
        download=await event.value
        transfer=urlsplit(download.url)
        if transfer.scheme=='blob':
            origin=urlsplit(transfer.path)
            allowed=(origin.scheme=='https' and origin.netloc=='flow.google.com')
        else:
            allowed=(transfer.scheme=='https' and transfer.hostname=='flow-content.google'
                and transfer.path==expected_path and not transfer.username and not transfer.password
                and transfer.port in (None,443))
        if not allowed:
            return 'transfer_identity_unverified'
        staged=output.with_name(output.stem+'.download.mp4')
        try:
            await download.save_as(staged)
            width,height,duration,audio=FlowMediaClient._probe_video(staged,timeout=max(.001,min(30,deadline-time.time())))
            if (width,height)!=(1080,1920) or not 7.5<=duration<=8.5 or not audio:
                return 'invalid_media'
            staged.replace(output)
            return 'downloaded'
        finally:
            staged.unlink(missing_ok=True)


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
        status='unavailable_'+type(exc).__name__
    print(json.dumps({'status':status}))
    return 0 if status=='downloaded' else 3


if __name__=='__main__':
    sys.exit(main())

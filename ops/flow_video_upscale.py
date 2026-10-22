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


async def download_existing(project_id, media_id, output, *, workflow_id=None, deadline=None):
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.config import get_settings
    from flow_readback import snapshot, select_video, ReadbackIdentityError
    from flow_media_client import FlowMediaClient
    if not workflow_id:
        return 'identity_unavailable'
    deadline = deadline or time.time()+240
    profile = get_settings().profile_subdir(os.getenv('GFLOW_PROFILE', 'shadow'))
    async with FlowApiClient(profile_dir=profile, headless=False) as client:
        listing = await snapshot(client._context, project_id, min(deadline,time.time()+30))
        clip = select_video(listing, dict(project_id=project_id,model='veo_3_1_i2v_s_fast_portrait',
            aspect='portrait',count=1,duration=8),media_id=media_id,workflow_id=workflow_id)
        page=client.page
        route=f'/project/{project_id}/edit/{media_id}'
        await page.goto('https://flow.google.com'+route,wait_until='domcontentloaded',
                        timeout=max(1,(deadline-time.time())*1000))
        current=urlsplit(page.url)
        if current.scheme!='https' or current.hostname!='flow.google.com' or current.path!=route:
            raise ReadbackIdentityError('Optional download editor identity differs')
        # The visible media must belong to this exact accepted workflow as well as its editor.
        sources=await page.locator('video').evaluate_all('(videos) => videos.map(v => v.currentSrc || v.src)')
        expected_path='/video/'+clip['workflow_id']
        if len(sources)!=1 or urlsplit(sources[0]).hostname!='flow-content.google' or urlsplit(sources[0]).path!=expected_path:
            return 'active_clip_unverified'
        button=page.get_by_role('button',name=re.compile(r'^download$',re.I))
        if await button.count()!=1:
            return 'ambiguous_control'
        await button.click()
        choice=page.get_by_role('menuitem',name=re.compile(r'1080p',re.I))
        if await choice.count()==0:
            choice=page.get_by_role('button',name=re.compile(r'1080p',re.I))
        if await choice.count()!=1 or not await choice.is_visible() or not await choice.is_enabled() or 'upgrade' in (await choice.inner_text()).lower():
            return 'not_offered'
        async with page.expect_download(timeout=max(1,(deadline-time.time())*1000)) as event:
            await choice.click()
        download=await event.value
        transfer=urlsplit(download.url)
        if (transfer.scheme!='https' or transfer.hostname!='flow-content.google' or transfer.path!=expected_path
                or transfer.username or transfer.password or transfer.port not in (None,443)):
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

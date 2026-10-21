#!/usr/bin/env python3
"""Download one identified original image or video; never invoke a generation endpoint."""
import asyncio
from contextlib import redirect_stdout
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))


async def download_existing(project_id: str, media_id: str, output: Path, *, context_file=None, deadline=None, request_hash=None, video=False, workflow_id=None) -> dict | None:
    import time
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.api.video import is_media_uuid
    from gflow_cli.config import get_settings
    from flow_readback import ReadbackUnavailable, ReadbackIdentityError, download_image, download_video, select_video, recover_context, snapshot, save
    if (not is_media_uuid(project_id) or (media_id and not is_media_uuid(media_id))
            or (context_file is None and not is_media_uuid(media_id))
            or (workflow_id is not None and not is_media_uuid(workflow_id))):
        raise ReadbackIdentityError('Invalid media identity')
    profile = get_settings().profile_subdir(os.getenv('GFLOW_PROFILE', 'shadow'))
    async with FlowApiClient(profile_dir=profile, headless=False) as client:
        if context_file is not None:
            return await recover_context(client, context_file, project_id, request_hash, deadline, output,
                                         media_id=media_id or None, workflow_id=workflow_id)
        deadline = deadline or time.time()+120
        listing = await snapshot(client._context, project_id, min(time.time()+60,deadline))
        if video:
            clip=select_video(listing,dict(project_id=project_id,model='veo_3_1_i2v_s_fast_portrait',aspect='portrait',count=1,duration=8),
                              media_id=media_id,workflow_id=workflow_id)
            result=dict(succeeded=True,media_id=media_id,workflow_id=clip['workflow_id'],project_id=project_id,
                        request={'count':1,'model':'veo_3_1_fast','mode':'i2v','aspect':'portrait'})
            receipt=output.with_suffix('.result.json');save(receipt,result)
            await asyncio.wait_for(download_video(client,clip,output,deadline=deadline),max(.001,deadline-time.time()))
            result['local_path']=str(output);save(receipt,result)
            return result
        matches = [image for image in listing['images'] if image.media_id == media_id and image.project_id == project_id]
        if len(matches) != 1:
            raise ReadbackUnavailable('Exact image unavailable')
        image = matches[0]
        details = listing['media'][media_id][6][0]
        if len(details) != 15 or details[8] != 36 or details[14] != 4:
            raise ReadbackIdentityError('Image settings differ')
        await download_image(client, image, output)


def main():
    try:
        with redirect_stdout(sys.stderr):
            if sys.argv[2] == '--context':
                result = asyncio.run(download_existing(sys.argv[1], sys.argv[7] if len(sys.argv)>7 else '', Path(sys.argv[4]),
                    context_file=Path(sys.argv[3]), deadline=float(sys.argv[5]), request_hash=sys.argv[6],
                    workflow_id=sys.argv[8] if len(sys.argv)>8 else None))
            else:
                import argparse
                parser=argparse.ArgumentParser()
                parser.add_argument('project');parser.add_argument('media');parser.add_argument('output',type=Path)
                parser.add_argument('--video',action='store_true');parser.add_argument('--workflow')
                parser.add_argument('--deadline',type=float)
                args=parser.parse_args()
                result = asyncio.run(download_existing(args.project,args.media,args.output,video=args.video,
                    workflow_id=args.workflow,deadline=args.deadline))
    except (ValueError, KeyError, TypeError) as exc:
        from flow_readback import ReadbackUnavailable, ReadbackIdentityError
        print(json.dumps({'status': 'identity_unverified' if isinstance(exc, ReadbackIdentityError)
                          or not isinstance(exc, ReadbackUnavailable) else 'recovery_unavailable',
                          'error_class': type(exc).__name__}))
        return 3
    except Exception as exc:
        print(json.dumps({'status': 'recovery_unavailable', 'error_class': type(exc).__name__}))
        return 3
    print(json.dumps({'status': 'downloaded', 'result': result}))
    return 0


if __name__ == '__main__':
    sys.exit(main())

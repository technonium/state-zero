#!/usr/bin/env python3
"""Download one identified original image or video; never invoke a generation endpoint."""
import asyncio
from contextlib import redirect_stdout
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))


async def download_existing(project_id: str, media_id: str, output: Path, *, context_file=None, deadline=None, request_hash=None,
                            video=False, workflow_id=None, mode=None, record=None, result_stream=None) -> dict | None:
    import time
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.api.video import is_media_uuid
    from gflow_cli.config import get_settings
    from flow_readback import ReadbackUnavailable, ReadbackIdentityError, download_image, download_video, select_video, recover_context, snapshot, save, recovery_phase
    if (not is_media_uuid(project_id) or (media_id and not is_media_uuid(media_id))
            or (context_file is None and not is_media_uuid(media_id))
            or (workflow_id is not None and not is_media_uuid(workflow_id))):
        raise ReadbackIdentityError('Invalid media identity')
    profile = get_settings().profile_subdir(os.getenv('GFLOW_PROFILE', 'shadow'))
    class ObservedClient(FlowApiClient):
        async def _launch_persistent_context(self, kwargs):
            recovery_phase('lease')
            context=await super()._launch_persistent_context(kwargs)
            recovery_phase('browser')
            return context
    candidate=None
    if mode=='retrieve':
        if record is None or record.resolve()!=context_file.with_name('flow_'+('video' if 'video' in context_file.name else 'image')+'_diagnostics.json').resolve():
            raise ReadbackIdentityError('Parent retrieval record differs')
        candidate=json.loads(record.read_text())['retrieval']['candidate']
    recovery_phase('launch')
    async with ObservedClient(profile_dir=profile, headless=False) as client:
        if context_file is not None:
            result = await recover_context(client, context_file, project_id, request_hash, deadline, output,
                                         media_id=media_id or None, workflow_id=workflow_id, mode=mode,candidate=candidate)
        else:
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
            else:
                matches = [image for image in listing['images'] if image.media_id == media_id and image.project_id == project_id]
                if len(matches) != 1:
                    raise ReadbackUnavailable('Exact image unavailable')
                image = matches[0]
                details = listing['media'][media_id][6][0]
                if len(details) != 15 or details[8] != 36 or details[14] != 4:
                    raise ReadbackIdentityError('Image settings differ')
                await download_image(client, image, output)
                result=None
        # Deliver evidence before browser teardown can consume the remaining budget.
        if result_stream is not None:
            print(json.dumps({'status':'candidate','candidate':result} if mode=='search' else
                {'status':'downloaded','result':result}),file=result_stream,flush=True)
        return result


def main():
    result_stream=sys.stdout
    try:
        with redirect_stdout(sys.stderr):
            if sys.argv[2] == '--context':
                import argparse
                parser=argparse.ArgumentParser()
                parser.add_argument('context',type=Path);parser.add_argument('output',type=Path)
                parser.add_argument('deadline',type=float);parser.add_argument('request_hash')
                parser.add_argument('media',nargs='?',default='');parser.add_argument('workflow',nargs='?')
                parser.add_argument('--mode',choices=('search','retrieve','recover'),default='recover');parser.add_argument('--record',type=Path)
                args=parser.parse_args(sys.argv[3:])
                result = asyncio.run(download_existing(sys.argv[1], args.media,args.output,
                    context_file=args.context, deadline=args.deadline, request_hash=args.request_hash,
                    workflow_id=args.workflow,mode=args.mode,record=args.record,result_stream=result_stream))
            else:
                import argparse
                parser=argparse.ArgumentParser()
                parser.add_argument('project');parser.add_argument('media');parser.add_argument('output',type=Path)
                parser.add_argument('--video',action='store_true');parser.add_argument('--workflow')
                parser.add_argument('--deadline',type=float)
                args=parser.parse_args()
                result = asyncio.run(download_existing(args.project,args.media,args.output,video=args.video,
                    workflow_id=args.workflow,deadline=args.deadline,result_stream=result_stream))
    except (ValueError, KeyError, TypeError) as exc:
        from flow_readback import ReadbackUnavailable, ReadbackIdentityError, ReadbackUnsupported
        print(json.dumps({'status': 'identity_unverified' if isinstance(exc, ReadbackIdentityError)
                          or not isinstance(exc, ReadbackUnavailable) else
                          'recovery_unsupported' if isinstance(exc, ReadbackUnsupported) else 'recovery_unavailable',
                          'error_class': type(exc).__name__}))
        return 3
    except Exception as exc:
        print(json.dumps({'status': 'recovery_unavailable', 'error_class': type(exc).__name__}))
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())

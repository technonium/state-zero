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
                            workflow_id=None, mode=None, record=None, result_stream=None) -> dict | None:
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.api.video import is_media_uuid
    from gflow_cli.config import get_settings
    from flow_readback import ReadbackIdentityError, recover_context, recovery_phase
    if (not is_media_uuid(project_id) or (media_id and not is_media_uuid(media_id))
            or (workflow_id is not None and not is_media_uuid(workflow_id))):
        raise ReadbackIdentityError('Invalid media identity')
    if context_file is None or mode not in {'search', 'retrieve'}:
        raise ReadbackIdentityError('Exact attempt context and recovery mode are required')
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
        result = await recover_context(client, context_file, project_id, request_hash, deadline, output,
                                       media_id=media_id or None, workflow_id=workflow_id, mode=mode,candidate=candidate)
        # Deliver evidence before browser teardown can consume the remaining budget.
        if result_stream is not None:
            print(json.dumps({'status':'candidate','candidate':result} if mode=='search' else
                {'status':'downloaded','result':result}),file=result_stream,flush=True)
        return result


def main():
    result_stream=sys.stdout
    try:
        with redirect_stdout(sys.stderr):
            import argparse
            parser=argparse.ArgumentParser()
            parser.add_argument('project')
            parser.add_argument('--context',type=Path,required=True)
            parser.add_argument('output',type=Path);parser.add_argument('deadline',type=float)
            parser.add_argument('request_hash');parser.add_argument('media',nargs='?',default='')
            parser.add_argument('workflow',nargs='?')
            parser.add_argument('--mode',choices=('search','retrieve'),required=True)
            parser.add_argument('--record',type=Path)
            args=parser.parse_args()
            result = asyncio.run(download_existing(args.project,args.media,args.output,
                context_file=args.context,deadline=args.deadline,request_hash=args.request_hash,
                workflow_id=args.workflow,mode=args.mode,record=args.record,result_stream=result_stream))
    except (ValueError, KeyError, TypeError) as exc:
        from flow_readback import ReadbackUnavailable, ReadbackIdentityError, ReadbackUnsupported
        print(json.dumps({'status': 'identity_unverified' if isinstance(exc, ReadbackIdentityError)
                          or not isinstance(exc, ReadbackUnavailable) else
                          'recovery_unsupported' if isinstance(exc, ReadbackUnsupported) else 'recovery_unavailable',
                          'error_class': type(exc).__name__}))
        return 3
    except Exception as exc:
        from flow_media_client import FlowCommandError
        if isinstance(exc, FlowCommandError) and exc.category == 'generation_failed':
            import hashlib
            print(json.dumps({'status': 'generation_failed', 'error_class': type(exc).__name__, 'media_id': exc.media_id,
                'operation_id': exc.operation_id, 'context_sha256': hashlib.sha256(args.context.read_bytes()).hexdigest()
                if getattr(args, 'context', None) else None}))
            return 3
        print(json.dumps({'status': 'recovery_unavailable', 'error_class': type(exc).__name__}))
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())

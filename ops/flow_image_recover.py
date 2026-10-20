#!/usr/bin/env python3
"""Download one identified original image; never invoke a generation endpoint."""
import asyncio
from contextlib import redirect_stdout
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))


async def download_existing(project_id: str, media_id: str, output: Path, *, context_file=None, deadline=None, request_hash=None) -> None:
    import time
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.api.video import is_media_uuid
    from gflow_cli.config import get_settings
    from flow_readback import ReadbackUnavailable, ReadbackIdentityError, download_image, recover_context, snapshot
    if not is_media_uuid(project_id) or (context_file is None and not is_media_uuid(media_id)):
        raise ValueError('Invalid media identity')
    profile = get_settings().profile_subdir(os.getenv('GFLOW_PROFILE', 'shadow'))
    async with FlowApiClient(profile_dir=profile, headless=False) as client:
        if context_file is not None:
            return await recover_context(client, context_file, project_id, request_hash, deadline, output)
        listing = await snapshot(client._context, project_id, time.time() + 60)
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
                result = asyncio.run(download_existing(sys.argv[1], '', Path(sys.argv[4]),
                    context_file=Path(sys.argv[3]), deadline=float(sys.argv[5]), request_hash=sys.argv[6]))
            else:
                result = asyncio.run(download_existing(sys.argv[1], sys.argv[2], Path(sys.argv[3])))
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

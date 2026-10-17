#!/usr/bin/env python3
"""Download one identified original image; never invoke a generation endpoint."""
import asyncio
import json
import os
import sys
from pathlib import Path


async def download_existing(project_id: str, media_id: str, output: Path) -> None:
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.api.dto import GeneratedImage
    from gflow_cli.api.video import is_media_uuid
    from gflow_cli.config import get_settings
    if not is_media_uuid(project_id) or not is_media_uuid(media_id):
        raise ValueError('Invalid media identity')
    profile = get_settings().profile_subdir(os.getenv('GFLOW_PROFILE', 'shadow'))
    async with FlowApiClient(profile_dir=profile, headless=False) as client:
        listing = await client.fetch_project_listing(project_id)
        media = listing['result']['data']['json']['projectContents']['media']
        matches = [row for row in media if row.get('name') == media_id and row.get('projectId') == project_id]
        if len(matches) != 1:
            raise ValueError('Exact image unavailable')
        image = GeneratedImage.from_response_item(matches[0])
        if image.model_name_type != 'BELUGA' or image.aspect_ratio != 'IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR':
            raise ValueError('Image settings differ')
        await client.download_image(image, output)


if __name__ == '__main__':
    try:
        asyncio.run(download_existing(sys.argv[1], sys.argv[2], Path(sys.argv[3])))
    except Exception:
        print(json.dumps({'status': 'recovery_unavailable'}))
        sys.exit(3)
    print(json.dumps({'status': 'downloaded'}))

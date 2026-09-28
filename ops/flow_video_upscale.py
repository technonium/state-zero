#!/usr/bin/env python3
"""Try Flow's 1080p download for an existing clip; never submit generation."""

import asyncio
import json
import os
import re
import sys
from pathlib import Path


async def download_existing(project_id: str, media_id: str, output: Path) -> str:
    from gflow_cli.browser_manager import channel_for_profile
    from gflow_cli.config import get_settings
    from gflow_cli.profile_lease import ProfileLease
    from playwright.async_api import async_playwright

    profile = get_settings().profile_subdir(os.getenv("GFLOW_PROFILE", "shadow"))
    if not profile.is_dir() or channel_for_profile(profile) != "chrome":
        return "profile_unavailable"
    async with ProfileLease(profile), async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile), channel="chrome", headless=False, accept_downloads=True,
            viewport={"width": 1280, "height": 720}, locale="en-US",
            ignore_default_args=["--enable-automation", "--no-sandbox"],
            args=["--password-store=basic", "--disable-blink-features=AutomationControlled",
                  "--disable-dev-shm-usage"],
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(f"https://flow.google.com/project/{project_id}/edit/{media_id}",
                            wait_until="domcontentloaded", timeout=60000)
            if "/about" in page.url or "accounts.google.com" in page.url:
                return "reauth_required"
            download_button = page.get_by_role("button", name=re.compile(r"download", re.I)).first
            try:
                await download_button.wait_for(state="visible", timeout=15000)
                await download_button.click()
                choice = page.get_by_role("menuitem", name=re.compile(r"1080p", re.I)).first
                if not await choice.count():
                    choice = page.get_by_role("button", name=re.compile(r"1080p", re.I)).first
                if not await choice.count() or not await choice.is_visible():
                    return "not_offered"
                async with page.expect_download(timeout=180000) as event:
                    await choice.click()
                download = await event.value
                await download.save_as(output)
                return "downloaded"
            except Exception:
                return "download_unavailable"
        finally:
            await context.close()


def main() -> int:
    if len(sys.argv) != 4 or not all(re.fullmatch(r"[A-Za-z0-9_-]+", value) for value in sys.argv[1:3]):
        return 2
    project_id, media_id, raw_output = sys.argv[1:]
    output = Path(raw_output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    status = asyncio.run(download_existing(project_id, media_id, output))
    print(json.dumps({"status": status}))
    return 0 if status == "downloaded" else 3


if __name__ == "__main__":
    sys.exit(main())

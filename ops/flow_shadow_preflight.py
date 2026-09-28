#!/usr/bin/env python3
"""Inspect the shadow Flow editor without submitting or spending credits."""

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


def classify_editor(
    url: str, trigger_present: bool, trigger_visible: bool,
    chip_present: bool, chip_pressed: bool | None, panel_open: bool,
) -> str:
    parsed = urlsplit(url)
    if parsed.hostname != "flow.google.com" or parsed.path in {"/about", "/unavailable"}:
        return "reauth_required"
    if not parsed.path.startswith("/project/"):
        return "unknown_editor"
    if trigger_visible:
        return "ready"
    if chip_present and chip_pressed:
        return "agent_toggle_on"
    if trigger_present and not chip_present:
        return "agent_panel_only" if panel_open else "unsupported_agent_view"
    return "unknown_editor"


async def inspect_editor(project_id: str, profile: str) -> tuple[str, str | None, str | None]:
    from gflow_cli.api.image import Aspect as ImageAspect, GenerateImageRequest
    from gflow_cli.api.video import Aspect as VideoAspect, GenerateVideoRequest, Mode, VideoModel
    from gflow_cli.browser_manager import channel_for_profile
    from gflow_cli.config import get_settings
    from gflow_cli.profile_lease import ProfileLease
    from gflow_cli.api.transports.migrated_composer import MigratedComposer
    from playwright.async_api import async_playwright

    profile_dir = get_settings().profile_subdir(profile)
    if not profile_dir.is_dir() or channel_for_profile(profile_dir) != "chrome":
        return "chrome_profile_missing", None, None
    async with ProfileLease(profile_dir), async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile_dir), channel="chrome", headless=False,
            viewport={"width": 1280, "height": 720}, locale="en-US",
            ignore_default_args=["--enable-automation", "--no-sandbox"],
            args=["--password-store=basic", "--disable-blink-features=AutomationControlled",
                  "--disable-dev-shm-usage"],
        )
        try:
            async def ready_page() -> tuple[object | None, str | None]:
                page = await context.new_page()
                await page.goto(f"https://flow.google.com/project/{project_id}",
                                wait_until="domcontentloaded", timeout=45000)
                recovery_attempted = False
                for _ in range(40):
                    trigger = page.locator(".settings-trigger-button").first
                    chip = page.locator("button.agent-mode-chip").first
                    trigger_present = bool(await trigger.count())
                    trigger_visible = trigger_present and await trigger.is_visible()
                    chip_present = bool(await chip.count())
                    chip_pressed = await chip.get_attribute("aria-pressed") == "true" if chip_present else None
                    panel_open = bool(await page.locator("flow-agent-panel").count())
                    status = classify_editor(page.url, trigger_present, trigger_visible,
                                             chip_present, chip_pressed, panel_open)
                    if status in {"agent_panel_only", "agent_toggle_on"} and not recovery_attempted:
                        recovery_attempted = True
                        await MigratedComposer._exit_agent_mode(page)
                        try:
                            await trigger.wait_for(state="visible", timeout=5000)
                        except Exception:
                            pass  # Reclassify the actual state; never submit from this gate.
                        continue
                    if status == "ready":
                        return page, None
                    if status != "unknown_editor":
                        await page.close()
                        return None, status
                    await page.wait_for_timeout(500)
                await page.close()
                return None, "unknown_editor"

            # Flow retains composer mode between operations in one editor. Probe each
            # mode from a fresh project page, as the image and video CLI steps do.
            checks = (
                ("image_settings", lambda page: MigratedComposer().apply_image_settings(
                    page, GenerateImageRequest(prompt="preflight only",
                                               aspect=ImageAspect.PORTRAIT_THREE_FOUR, count=1))),
                ("video_settings", lambda page: MigratedComposer().apply_video_settings(
                    page, GenerateVideoRequest(prompt="preflight only", mode=Mode.I2V,
                                               aspect=VideoAspect.PORTRAIT,
                                               model=VideoModel.VEO_3_1_FAST, count=1,
                                               start_image=Path("/preflight/no-upload.png")))),
            )
            for check_name, check in checks:
                page, status = await ready_page()
                if status is not None:
                    return status, check_name, None
                try:
                    await check(page)
                except Exception as exc:
                    return "controls_unavailable", check_name, type(exc).__name__
                finally:
                    await page.close()
            return "ready", None, None
        finally:
            await context.close()


def main() -> int:
    os.umask(0o077)
    private_root = Path(os.environ["STATE_ZERO_PRIVATE_ROOT"]).resolve()
    project_id = os.getenv("FLOW_PREFLIGHT_PROJECT_ID", "").strip()
    profile = os.getenv("GFLOW_PROFILE", "shadow").strip()
    report_path = private_root / "runtime" / "state" / "flow_shadow" / "preflight.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if not project_id:
        status, failed_check, error_type = "project_id_missing", None, None
    elif not re.fullmatch(r"[A-Za-z0-9_-]+", project_id) or not re.fullmatch(r"[A-Za-z0-9_-]+", profile):
        status, failed_check, error_type = "invalid_configuration", None, None
    else:
        try:
            status, failed_check, error_type = asyncio.run(inspect_editor(project_id, profile))
        except Exception as exc:
            status = "browser_error"
            failed_check = None
            error_type = type(exc).__name__
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "status": status}
    if failed_check:
        report["failed_check"] = failed_check
    if error_type:
        report["error_type"] = error_type
    report_path.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    report_path.chmod(0o600)
    print(f"Flow shadow preflight: {status}")
    return 0 if status == "ready" else 3


if __name__ == "__main__":
    sys.exit(main())

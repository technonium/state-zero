"""Run in the shadow image to check the pinned CLI panel recovery patch."""
import unittest
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

try:
    from gflow_cli.api.transports.migrated_composer import MigratedComposer
except ImportError:
    MigratedComposer = None


@unittest.skipIf(MigratedComposer is None, "Requires the shadow image's pinned gflow")
class PanelRecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def test_submit_boundary_creates_one_private_marker(self):
        from gflow_cli.api.transports.migrated_composer import _mark_shadow_submit_attempt
        with tempfile.TemporaryDirectory() as tmpdir:
            marker = Path(tmpdir) / "submit"
            with patch.dict(os.environ, {"GFLOW_SHADOW_SUBMIT_MARKER": str(marker)}):
                _mark_shadow_submit_attempt()
                with self.assertRaises(FileExistsError):
                    _mark_shadow_submit_attempt()
            self.assertEqual(marker.read_text(), "submit_attempted\n")
            self.assertEqual(marker.stat().st_mode & 0o777, 0o600)

    async def test_download_failure_recovers_same_clip_without_generation(self):
        from pathlib import Path
        from types import SimpleNamespace
        record = SimpleNamespace(video_url="https://flow-content.google/video/workflow",
            poster_url=None, project_id="project", media_id="media")
        composer = object.__new__(MigratedComposer)
        with patch.object(MigratedComposer, "_fetch_mp4", side_effect=OSError), patch(
            "gflow_cli.api.transports.migrated_recover.recover_clip",
            new_callable=AsyncMock, return_value=SimpleNamespace(path=Path("/tmp/original.mp4"))) as recover:
            result = await composer.download(MagicMock(), record, Path("/tmp"))
        self.assertEqual(result, Path("/tmp/original.mp4"))
        self.assertEqual(recover.call_args.kwargs["media_id"], "media")
        self.assertEqual(recover.call_args.kwargs["project_id"], "project")

    async def test_recovery_uses_original_video_for_the_matching_workflow(self):
        from gflow_cli.api.transports.migrated_recover import _await_signed_record
        callbacks = {}
        page = MagicMock()
        page.on.side_effect = lambda event, callback: callbacks.update({event: callback})
        page.wait_for_timeout = AsyncMock()

        async def navigate(*args, **kwargs):
            callbacks["request"](MagicMock(url="https://flow-content.google/video/other"))
            callbacks["request"](MagicMock(url="https://flow-content.google/video/workflow?signature=test"))
            response = MagicMock(url="https://flow.google.com/batchexecute")
            response.text = AsyncMock(return_value="status payload")
            await callbacks["response"](response)

        page.goto = AsyncMock(side_effect=navigate)
        def collect(text, *, media_id, into):
            into.update(url="https://lh3.googleusercontent.com/preview", workflow_id="workflow", size=123)
        with patch("gflow_cli.api.transports.migrated_recover._collect", side_effect=collect):
            found = await _await_signed_record(page, media_id="media", project_id="project", wait_s=1)
        self.assertEqual(found["url"], "https://flow-content.google/video/workflow?signature=test")
        self.assertEqual(found["size"], 123)
        self.assertEqual(page.remove_listener.call_count, 2)

    async def test_close_panel_before_probing_hidden_chip(self):
        closed = False

        async def close_panel(**kwargs):
            nonlocal closed
            closed = True

        async def pressed(page):
            return closed  # The chip is absent until the expanded panel closes.

        panel = MagicMock()
        panel.count = AsyncMock(return_value=1)
        panel.is_visible = AsyncMock(return_value=True)
        panel.click = AsyncMock(side_effect=close_panel)
        chip = MagicMock()
        chip.click = AsyncMock()
        page = MagicMock()
        panel_list = MagicMock()
        panel_list.filter.return_value.first = panel
        chip_list = MagicMock()
        chip_list.first = chip
        page.locator.side_effect = lambda css: panel_list if css == "flow-agent-panel button" else chip_list
        with patch.object(MigratedComposer, "_agent_chip_pressed", side_effect=pressed):
            state, error = await MigratedComposer._exit_agent_mode(page)
        self.assertEqual(state, "clicked")
        self.assertIsNone(error)
        panel.click.assert_awaited_once()
        chip.click.assert_awaited_once()

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

    async def test_original_download_allows_only_flow_blob_origin(self):
        from gflow_cli.api.transports.migrated_recover import _is_original_download_url
        self.assertTrue(_is_original_download_url("blob:https://flow.google.com/123"))
        self.assertFalse(_is_original_download_url("blob:https://evil.example/123"))
        self.assertFalse(_is_original_download_url("blob:https://flow.google.com.evil.example/123"))

    async def test_original_download_refuses_missing_record_size(self):
        from gflow_cli.api.transports.migrated_recover import _download_original_verified
        from gflow_cli.exceptions import WireFormatError
        page = MagicMock()
        with self.assertRaises(WireFormatError):
            await _download_original_verified(page, expected=None, media_id="media")
        page.get_by_role.assert_not_called()

    async def test_original_download_rejects_wrong_size(self):
        from gflow_cli.api.transports.migrated_recover import _verify
        from gflow_cli.exceptions import WireFormatError
        with self.assertRaises(WireFormatError):
            _verify(b"0000ftyp00000000", expected=17, media_id="media")

    async def test_recovery_uses_original_menu_without_request_context(self):
        from types import SimpleNamespace
        from gflow_cli.api.transports.migrated_recover import recover_clip
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "gflow_cli.api.transports.migrated_recover._await_signed_record",
            new_callable=AsyncMock, return_value={"url": "https://flow-content.google/video/workflow", "size": 16, "workflow_id": "workflow"}), patch(
            "gflow_cli.api.transports.migrated_recover._fetch_verified",
            new_callable=AsyncMock) as request, patch(
            "gflow_cli.api.transports.migrated_recover._download_original_verified",
            new_callable=AsyncMock, return_value=b"0000ftyp00000000") as download:
            result = await recover_clip(MagicMock(), project_id="project", media_id="media", out_dir=Path(tmpdir))
            self.assertEqual(result.path.read_bytes(), b"0000ftyp00000000")
            self.assertEqual(download.call_args.kwargs, {"expected": 16, "media_id": "media"})
            request.assert_not_awaited()

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

@unittest.skipIf(MigratedComposer is None, "Requires the shadow image's pinned gflow")
class VideoRecordTest(unittest.TestCase):
    def record(self, model="veo_3_1_i2v_s_fast_portrait", marker=None):
        ids = ["11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222", "33333333-3333-4333-8333-333333333333"]
        details = [None] * 14
        details[6] = [None, [[model, 2, [1], None, 1, 1]]]
        details[8] = [3]
        details[13] = 1718989
        media = [None] * 13
        media[8] = "https://flow-content.google/video/workflow"
        media[12] = model
        return ids + [marker, None, details, None, [media, [None, None, [8]]]]

    def test_null_marker_video_reply_is_decoded(self):
        from gflow_cli.api.transports.batchexecute import generation_record
        result = generation_record("as29s", self.record())
        self.assertEqual(result.status, 3)
        self.assertEqual(result.size_bytes, 1718989)
        self.assertEqual(result.video_url, "https://flow-content.google/video/workflow")

    def test_null_marker_without_video_model_is_rejected(self):
        from gflow_cli.api.transports.batchexecute import generation_record
        from gflow_cli.exceptions import WireFormatError
        with self.assertRaises(WireFormatError):
            generation_record("as29s", self.record(model="image-model"))

    def test_original_cae_reply_still_decodes(self):
        from gflow_cli.api.transports.batchexecute import generation_record
        self.assertEqual(generation_record("as29s", self.record(marker="CAE")).status, 3)

@unittest.skipIf(MigratedComposer is None, 'Requires pinned gflow')
class ImageSubmissionGuardTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def body(model, prompt='cloud'):
        import json
        row = [None, None, None, 123, 4, model, None, None, [[[prompt]]]]
        return 'f.req=' + json.dumps([[['ogiZ0b', json.dumps([None, [row], 1]), None, 'generic']]])

    def test_model_is_validated_at_request_field_not_in_prompt(self):
        from gflow_cli.api.image import Model
        from gflow_cli.api.transports.migrated_composer import _image_body_problem
        for model in ('NARWHAL', 'BELUGA'):
            self.assertIsNone(_image_body_problem(self.body(model), (), Model.NARWHAL))
        for body in (self.body('GEM_PIX_2', 'NARWHAL BELUGA'), '', 'NARWHAL', self.body('UNKNOWN')):
            self.assertIsNotNone(_image_body_problem(body, (), Model.NARWHAL))

    async def test_guard_aborts_mismatch_before_forwarding(self):
        from gflow_cli.api.image import Model
        from gflow_cli.api.transports.migrated_composer import _guard_image_submit
        route = MagicMock(abort=AsyncMock(), continue_=AsyncMock())
        raw = MagicMock(post_data=self.body('GEM_PIX_2'))
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'submit'
            marker.write_text('submit_attempted\n')
            with patch.dict(os.environ, {'GFLOW_SHADOW_SUBMIT_MARKER':str(marker)}):
                self.assertIsNotNone(await _guard_image_submit(route, raw, (), Model.NARWHAL))
            import json
            self.assertEqual(json.loads(marker.read_text())['state'], 'blocked_before_submission')
        route.abort.assert_awaited_once()
        route.continue_.assert_not_awaited()

    async def test_guard_records_forwarded_before_continuing(self):
        from gflow_cli.api.image import Model
        from gflow_cli.api.transports.migrated_composer import _guard_image_submit
        route = MagicMock(abort=AsyncMock(), continue_=AsyncMock())
        raw = MagicMock(post_data=self.body('BELUGA'))
        with tempfile.TemporaryDirectory() as tmp:
            marker=Path(tmp)/'submit';marker.write_text('submit_attempted\n')
            with patch.dict(os.environ, {'GFLOW_SHADOW_SUBMIT_MARKER':str(marker)}):
                self.assertIsNone(await _guard_image_submit(route,raw,(),Model.NARWHAL))
            import json
            self.assertEqual(json.loads(marker.read_text())['state'],'forwarded')
        route.continue_.assert_awaited_once()
        route.abort.assert_not_awaited()

    async def test_wrong_aspect_or_count_is_aborted_before_forwarding(self):
        import json
        from gflow_cli.api.image import Model
        from gflow_cli.api.transports.migrated_composer import _guard_image_submit
        for aspect, count in ((1, 1), (4, 2)):
            row = [None, None, None, 123, aspect, 'BELUGA', None, None, [[['cloud']]]]
            body = 'f.req=' + json.dumps([[['ogiZ0b', json.dumps([None, [row], count]), None, 'generic']]])
            route = MagicMock(abort=AsyncMock(), continue_=AsyncMock())
            self.assertIsNotNone(await _guard_image_submit(route, MagicMock(post_data=body), (), Model.NARWHAL))
            route.abort.assert_awaited_once()
            route.continue_.assert_not_awaited()

    async def test_text_to_image_registers_guard_and_blocks_wrong_model(self):
        import json
        from gflow_cli.api.image import Model, GenerateImageRequest
        from gflow_cli.exceptions import WireFormatError
        handlers = {}
        page = MagicMock(route=AsyncMock(), unroute=AsyncMock())
        async def register(predicate, callback):
            handlers['guard'] = callback
        page.route.side_effect = register
        submit = MagicMock(is_enabled=AsyncMock(return_value=True))
        route = MagicMock(abort=AsyncMock(), continue_=AsyncMock())
        async def click(*args, **kwargs):
            self.assertIn('guard', handlers, 'T2I must install the network guard before clicking')
            await handlers['guard'](route, MagicMock(post_data=self.body('GEM_PIX_2')))
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            'GFLOW_SHADOW_SUBMIT_MARKER': str(Path(tmp) / 'submit')
        }), patch.object(MigratedComposer, '_pre_submit_gate', new=AsyncMock(return_value=submit)), patch.object(
            MigratedComposer, '_click', new=AsyncMock(side_effect=click)):
            with self.assertRaises(WireFormatError):
                await MigratedComposer().submit_images_and_observe(page,
                    GenerateImageRequest(prompt='NARWHAL BELUGA', model=Model.NARWHAL))
            state = json.loads((Path(tmp) / 'submit').read_text())
            self.assertEqual(state['state'], 'blocked_before_submission')
        route.abort.assert_awaited_once()
        route.continue_.assert_not_awaited()
        page.unroute.assert_awaited_once()

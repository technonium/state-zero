"""Synthetic current-host listing and lost-response recovery boundaries."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
from flow_readback import (ReadbackUnavailable, bind_request, context_path, decode_listing,
                           fingerprint, prepare_image, recover_upload, select_image, snapshot)

PROJECT = '11111111-1111-4111-8111-111111111111'
MEDIA = '22222222-2222-4222-8222-222222222222'
WORKFLOW = '33333333-3333-4333-8333-333333333333'


def listing(media=MEDIA, seed=123, prompt='synthetic', model=36, aspect=4):
    details = [None] * 15
    details[1] = seed; details[7] = prompt; details[8] = model
    details[13] = 'https://flow-content.google/original'; details[14] = aspect
    return [None, [[WORKFLOW, None, None, ['unique.png', [], None, None, media, None, []], PROJECT]],
            [[media, PROJECT, WORKFLOW, None, None, [], [details, None, [768, 1024]]]], [], None, None, None, []]


def upload_listing():
    payload = listing()
    payload[2][0][6] = [None, [None] * 7, [768, 1024], None, None, []]
    return payload


class ReadbackTests(unittest.IsolatedAsyncioTestCase):
    def context(self):
        return dict(version=1, baseline=[], project_id=PROJECT, model='BELUGA', aspect=4, count=1,
                    seed=123, prompt_hash=fingerprint('synthetic'))

    def test_exact_new_image_and_all_identity_refusals(self):
        parsed = decode_listing(listing(), PROJECT)
        self.assertEqual(select_image(parsed, self.context()).media_id, MEDIA)
        for update in [{'baseline': [MEDIA]}, {'seed': 124}, {'prompt_hash': fingerprint('other')},
                       {'project_id': WORKFLOW}, {'baseline': None}, {'version': 2}, {'count': 2}]:
            with self.subTest(update=update), self.assertRaises(ReadbackUnavailable):
                select_image(parsed, self.context() | update)
        for model, aspect in [(35, 4), (36, 1)]:
            with self.assertRaises(ReadbackUnavailable):
                select_image(decode_listing(listing(model=model, aspect=aspect), PROJECT), self.context())

    def test_ambiguous_results_do_not_choose_newest(self):
        payload = listing()
        second = listing(media='44444444-4444-4444-8444-444444444444')
        payload[2] += second[2]
        parsed = decode_listing(payload, PROJECT)
        with self.assertRaises(ReadbackUnavailable):
            select_image(parsed, self.context())

    def test_partial_unknown_and_cross_project_listings_are_rejected(self):
        for change in ('cursor', 'project', 'duplicate', 'shape'):
            payload = listing()
            if change == 'cursor': payload[0] = 'continuation'
            elif change == 'project': payload[2][0][1] = WORKFLOW
            elif change == 'duplicate': payload[2].append(payload[2][0])
            else: payload.pop()
            with self.subTest(change=change), self.assertRaises(ReadbackUnavailable):
                decode_listing(payload, PROJECT)

    def test_generated_artwork_cannot_be_mistaken_for_an_uploaded_frame(self):
        self.assertEqual(decode_listing(listing(), PROJECT)['uploads'], [])
        payload = upload_listing()
        payload[2][0][2] = MEDIA
        self.assertEqual(decode_listing(payload, PROJECT)['uploads'], [])

    def test_measured_empty_project_is_complete_but_missing_one_field_is_not(self):
        payload = [None, None, None, [], None, None, None, []]
        self.assertEqual(decode_listing(payload, PROJECT)['ids'], [])
        payload[1] = []
        with self.assertRaises(ReadbackUnavailable): decode_listing(payload, PROJECT)

    async def test_actual_request_is_persisted_privately_before_forwarding(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'flow_image_attempt_1.submit'
            (Path(tmp) / 'flow_inputs.json').write_text('{}')
            page = Mock(url='https://flow.google.com/project/' + PROJECT)
            with patch.dict(os.environ, {'GFLOW_SHADOW_SUBMIT_MARKER': str(marker),
                    'GFLOW_SHADOW_REQUEST_HASH': 'a' * 64, 'FLOW_PREFLIGHT_PROJECT_ID': PROJECT}, clear=True), \
                    patch('flow_readback.snapshot', new=AsyncMock(return_value={'ids': [WORKFLOW]})):
                context = await prepare_image(page)
                row = [None, None, None, 123, 4, 'BELUGA', None, None, [[['synthetic']]]]
                bind_request(context, marker, [None, [row], 1], 'synthetic')
            path = context_path(marker)
            saved = json.loads(path.read_text())
            self.assertEqual(saved['seed'], 123)
            self.assertEqual(saved['baseline'], [WORKFLOW])
            self.assertEqual(saved['prompt_hash'], fingerprint('synthetic'))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn('synthetic', path.read_text())
            with self.assertRaises(ReadbackUnavailable):
                bind_request(context, marker, [None, [row], 1], 'changed')

    async def test_unavailable_baseline_does_not_invent_identity(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                'GFLOW_SHADOW_SUBMIT_MARKER': str(Path(tmp) / 'attempt.submit'),
                'GFLOW_SHADOW_REQUEST_HASH': 'a' * 64, 'FLOW_PREFLIGHT_PROJECT_ID': PROJECT}, clear=True), \
                patch('flow_readback.snapshot', new=AsyncMock(side_effect=ReadbackUnavailable)):
            (Path(tmp) / 'flow_inputs.json').write_text('{}')
            data = await prepare_image(Mock(url='https://flow.google.com/project/' + PROJECT))
            self.assertIsNone(data['baseline'])
            self.assertEqual(data['readback'], 'baseline_unavailable')

    async def test_safe_pre_submit_retry_reuses_context_without_new_baseline_budget(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                'GFLOW_SHADOW_SUBMIT_MARKER': str(Path(tmp) / 'flow_image_attempt_1.submit'),
                'GFLOW_SHADOW_REQUEST_HASH': 'a' * 64, 'FLOW_PREFLIGHT_PROJECT_ID': PROJECT}, clear=True), \
                patch('flow_readback.snapshot', new=AsyncMock(return_value={'ids': [MEDIA]})) as read:
            (Path(tmp) / 'flow_inputs.json').write_text('{}')
            page = Mock(url='https://flow.google.com/project/' + PROJECT)
            first = await prepare_image(page)
            second = await prepare_image(page)
            self.assertEqual(first, second)
            read.assert_awaited_once()

    async def test_recovered_upload_survives_restart_without_another_read_or_upload(self):
        import hashlib
        from flow_readback import prepare_upload, recover_saved_upload, upload_path, save
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                'GFLOW_SHADOW_SUBMIT_MARKER': str(Path(tmp) / 'flow_video_attempt_1.submit'),
                'GFLOW_SHADOW_REQUEST_HASH': 'a' * 64}, clear=True):
            source = Path(tmp) / 'frame.png'; source.write_bytes(b'original')
            data = prepare_upload(PROJECT, source, 'unique.png')
            data.update(state='recovered', media_id=MEDIA, readback_deadline=1)
            save(upload_path(), data)
            restored = prepare_upload(PROJECT, source, 'different-name-must-not-be-used.png')
            with patch('flow_readback.snapshot', new=AsyncMock()) as read:
                self.assertEqual(await recover_saved_upload(Mock(), PROJECT, source, restored), MEDIA)
                read.assert_not_awaited()
            self.assertEqual(restored['readback_deadline'], 1)

    def saved_attempt(self, root):
        from flow_readback import save, identity_hash
        marker = root / 'flow_image_attempt_1.submit'
        (root / 'flow_inputs.json').write_text('{}')
        data = self.context() | {'attempt': 1, 'profile': 'shadow', 'request_hash': 'a' * 64,
                                'frozen_inputs_sha256': fingerprint('{}'), 'recovery_deadline': None}
        save(context_path(marker), data)
        marker.write_text(json.dumps({'state': 'forwarded', 'actual_model': 'BELUGA',
            'expected_model': 'NARWHAL', 'selected_model': 'Nano Banana 2.1', 'readback_hash': identity_hash(data)}))
        return marker

    async def test_corrupt_context_cannot_enable_readback(self):
        from flow_readback import recover_context, ReadbackIdentityError
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); marker = self.saved_attempt(root)
            data = json.loads(context_path(marker).read_text()); data['seed'] += 1
            context_path(marker).write_text(json.dumps(data))
            with patch('flow_readback.snapshot', new=AsyncMock()) as read, self.assertRaises(ReadbackIdentityError):
                await recover_context(Mock(), context_path(marker), PROJECT, 'a' * 64, 999, root / 'art.png')
            read.assert_not_awaited()

    async def test_missing_or_changed_frozen_evidence_requires_reconciliation(self):
        import time
        from flow_readback import recover_context, ReadbackIdentityError
        for changed in (False, True):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); marker = self.saved_attempt(root)
                frozen = root / 'flow_inputs.json'
                if changed: frozen.write_text('{"changed":true}')
                else: frozen.unlink()
                with patch('flow_readback.snapshot', new=AsyncMock()) as read, self.assertRaises(ReadbackIdentityError):
                    await recover_context(Mock(), context_path(marker), PROJECT, 'a' * 64, time.time() + 30, root / 'art.png')
                read.assert_not_awaited()

    async def test_expired_recovery_budget_survives_restart(self):
        import time
        from flow_readback import recover_context
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp);marker = self.saved_attempt(root)
            original_deadline = time.time() - 1
            with patch('flow_readback.snapshot', new=AsyncMock()) as read:
                for deadline in (original_deadline, time.time() + 300):
                    with self.assertRaises(ReadbackUnavailable):
                        await recover_context(Mock(), context_path(marker), PROJECT, 'a' * 64, deadline, root / 'art.png')
                read.assert_not_awaited()
            self.assertEqual(json.loads(context_path(marker).read_text())['recovery_deadline'], original_deadline)

    async def test_download_failure_preserves_exact_receipt_before_download(self):
        import time
        from flow_readback import recover_context, receipt_path
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp); marker = self.saved_attempt(root)
            client = Mock()
            client._context.request.get = AsyncMock(side_effect=OSError('download failed'))
            with patch('flow_readback.snapshot', new=AsyncMock(return_value=decode_listing(listing(), PROJECT))), \
                    self.assertRaises(OSError):
                await recover_context(client, context_path(marker), PROJECT, 'a' * 64, time.time() + 30, root / 'art.png')
            saved = json.loads(receipt_path(context_path(marker)).read_text())
            self.assertEqual(saved['images'][0]['media_name'], MEDIA)
            self.assertNotIn('https://', receipt_path(context_path(marker)).read_text())
            self.assertFalse((root / 'art.png').exists())

    async def test_corrupt_download_does_not_replace_valid_original(self):
        from flow_readback import download_image
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'original.png'; output.write_bytes(b'valid-original-evidence')
            client = Mock()
            client._context.request.get = AsyncMock(return_value=Mock(status=200, body=AsyncMock(return_value=b'broken')))
            with self.assertRaises(OSError):
                await download_image(client, decode_listing(listing(), PROJECT)['images'][0], output)
            self.assertEqual(output.read_bytes(), b'valid-original-evidence')
            self.assertFalse(output.with_name('original.download.png').exists())

    async def test_current_listing_cdn_download_follows_only_verified_google_hosts(self):
        import io
        from PIL import Image
        from dataclasses import replace
        from flow_readback import download_image
        content = io.BytesIO();Image.new('RGB', (768, 1024)).save(content, 'PNG')
        image = replace(decode_listing(listing(), PROJECT)['images'][0], image_url='https://lh3.googleusercontent.com/exact-original')
        response = Mock(status=200, body=AsyncMock(return_value=content.getvalue()))
        client = Mock(download_image=AsyncMock())
        client._context.request.get = AsyncMock(return_value=response)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'original.png'
            await download_image(client, image, output)
            self.assertTrue(output.is_file())
            self.assertEqual(client._context.request.get.call_args.args[0], image.image_url + '=s0')
            self.assertEqual(client._context.request.get.call_args.kwargs['max_redirects'], 0)
            redirect = Mock(status=302, headers={'location': 'https://lh3.google.com/exact-original'})
            client._context.request.get.side_effect = [redirect, response]
            await download_image(client, image, output)
            self.assertEqual(client._context.request.get.call_args.args[0], redirect.headers['location'])
            redirect.headers['location'] = 'https://untrusted.example/original'
            client._context.request.get.side_effect = [redirect]
            from flow_readback import ReadbackIdentityError
            with self.assertRaises(ReadbackIdentityError): await download_image(client, image, output)
            self.assertEqual(output.read_bytes(), content.getvalue())

    async def test_thumbnail_is_never_promoted_as_the_original(self):
        import io
        from PIL import Image
        from flow_readback import download_image
        data = io.BytesIO();Image.new('RGB', (384, 512)).save(data, 'PNG')
        client = Mock()
        client._context.request.get = AsyncMock(return_value=Mock(status=200, body=AsyncMock(return_value=data.getvalue())))
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'original.png'
            with self.assertRaises(ReadbackUnavailable): await download_image(client, decode_listing(listing(), PROJECT)['images'][0], output)
            self.assertFalse(output.exists())

    async def test_original_size_url_normalization_is_idempotent_and_preserves_queries(self):
        import io
        from PIL import Image
        from types import SimpleNamespace
        from flow_readback import download_image
        data = io.BytesIO(); Image.new('RGB', (768, 1024)).save(data, 'PNG')
        for host in ('lh3.googleusercontent.com', 'lh3.google.com'):
            for suffix in ('', '=s0', '=s512', '=w384-h512'):
                with self.subTest(host=host, suffix=suffix), tempfile.TemporaryDirectory() as tmp:
                    query = '?synthetic=a%2Fb&value=one%3Dtwo'
                    original = f'https://{host}/original{suffix}{query}'
                    expected = f'https://{host}/original=s0{query}'
                    client = Mock()
                    client._context.request.get = AsyncMock(return_value=Mock(
                        status=200, body=AsyncMock(return_value=data.getvalue())))
                    for url in (original, expected):
                        await download_image(client, SimpleNamespace(image_url=url, dimensions=(768, 1024)), Path(tmp) / 'art.png')
                    self.assertEqual([call.args[0] for call in client._context.request.get.call_args_list], [expected, expected])

    async def test_upload_matches_unique_name_and_unchanged_source(self):
        import hashlib, time
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'frame.png'; source.write_bytes(b'original')
            record = dict(project_id=PROJECT, profile='shadow', name='unique.png',
                          source_hash=hashlib.sha256(source.read_bytes()).hexdigest(), readback_deadline=time.time() + 30)
            parsed = decode_listing(upload_listing(), PROJECT)
            with patch('flow_readback.snapshot', new=AsyncMock(return_value=parsed)), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(await recover_upload(Mock(), PROJECT, source, record), MEDIA)
                parsed['uploads'] *= 2
                with self.assertRaises(ReadbackUnavailable): await recover_upload(Mock(), PROJECT, source, record)
                source.write_bytes(b'changed')
                with self.assertRaises(ReadbackUnavailable): await recover_upload(Mock(), PROJECT, source, record)

    async def test_project_read_closes_page_and_never_writes(self):
        import time
        handlers = {}
        page = Mock(close=AsyncMock())
        page.on.side_effect = lambda event, handler: handlers.update({event: handler})
        text = json.dumps([["wrb.fr", "Zzl0ze", json.dumps(listing()), None]])
        async def navigate(*args, **kwargs):
            handlers['response'](Mock(url='https://flow.google.com/data/batchexecute?rpcids=Zzl0ze',
                                      request=Mock(post_data=PROJECT), status=200, finished=AsyncMock(return_value=None), text=AsyncMock(return_value=text)))
        page.goto = AsyncMock(side_effect=navigate)
        context = Mock(new_page=AsyncMock(return_value=page))
        result = await snapshot(context, PROJECT, time.time() + 2)
        self.assertIn(MEDIA, result['ids'])
        page.close.assert_awaited_once()
        page.click.assert_not_called()
        page.route.assert_not_called()

    async def test_lost_read_response_retries_only_reads_under_same_deadline(self):
        import time
        pages = []
        text = json.dumps([["wrb.fr", "Zzl0ze", json.dumps(listing()), None]])
        for index in range(2):
            handlers = {}
            page = Mock(close=AsyncMock())
            page.on.side_effect = lambda event, handler, h=handlers: h.update({event: handler})
            response = Mock(url='https://flow.google.com/data/batchexecute?rpcids=Zzl0ze', status=200,
                request=Mock(post_data=PROJECT),
                finished=AsyncMock(return_value=None), text=AsyncMock(**({'side_effect': RuntimeError('body lost')}
                    if index == 0 else {'return_value': text})))
            async def navigate(*args, h=handlers, r=response, **kwargs): h['response'](r)
            page.goto = AsyncMock(side_effect=navigate)
            pages.append(page)
        result = await snapshot(Mock(new_page=AsyncMock(side_effect=pages)), PROJECT, time.time() + 2)
        self.assertIn(MEDIA, result['ids'])
        for page in pages:
            page.close.assert_awaited_once()
            page.click.assert_not_called()


class InstalledObserverTests(unittest.IsolatedAsyncioTestCase):
    async def test_lost_image_response_recovers_one_exact_result_without_second_click(self):
        import time
        from unittest.mock import MagicMock
        from types import SimpleNamespace
        from PIL import Image
        from flow_readback import recover_context, receipt_path
        from gflow_cli.api.image import GenerateImageRequest, Model
        from gflow_cli.api.transports.migrated_composer import MigratedComposer
        handlers = {}
        page = MagicMock(url='https://flow.google.com/project/' + PROJECT,
                         route=AsyncMock(), unroute=AsyncMock())
        page.on.side_effect = lambda event, callback: handlers.update({event: callback})
        submit = Mock(is_enabled=AsyncMock(return_value=True))
        route = Mock(abort=AsyncMock(), continue_=AsyncMock())
        row = [None, None, None, 123, 4, 'BELUGA', None, None, [[['synthetic']]]]
        body = 'f.req=' + json.dumps([[['ogiZ0b', json.dumps([None, [row], 1]), None, 'generic']]])
        guard = None
        async def register(predicate, callback):
            nonlocal guard
            guard = callback
        page.route.side_effect = register
        async def click(*args, **kwargs):
            await guard(route, Mock(post_data=body))
            await handlers['response'](Mock(url='https://flow.google.com/data/batchexecute?rpcids=ogiZ0b',
                status=200, finished=AsyncMock(return_value=None), text=AsyncMock(side_effect=RuntimeError('response body unavailable'))))
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                'GFLOW_SHADOW_SUBMIT_MARKER': str(Path(tmp) / 'flow_image_attempt_1.submit'),
                'GFLOW_SHADOW_REQUEST_HASH': 'a' * 64, 'FLOW_PREFLIGHT_PROJECT_ID': PROJECT}, clear=True):
            marker = Path(os.environ['GFLOW_SHADOW_SUBMIT_MARKER'])
            (Path(tmp) / 'flow_inputs.json').write_text('{}')
            composer = MigratedComposer(); composer._shadow_image_model_label = 'Nano Banana 2.1'
            with patch('flow_readback.snapshot', new=AsyncMock(return_value={'ids': []})), \
                    patch.object(composer, '_pre_submit_gate', new=AsyncMock(return_value=submit)), \
                    patch.object(composer, '_click', new=AsyncMock(side_effect=click)) as clicks:
                with self.assertRaisesRegex(RuntimeError, 'body unavailable'):
                    await composer.submit_images_and_observe(page, GenerateImageRequest(prompt='synthetic', model=Model.NARWHAL))
            clicks.assert_awaited_once(); route.continue_.assert_awaited_once()
            context = context_path(marker)
            async def download(url, **kwargs):
                import io
                self.assertEqual(url, 'https://flow-content.google/original')
                self.assertEqual(json.loads(receipt_path(context).read_text())['images'][0]['media_name'], MEDIA)
                content = io.BytesIO(); Image.new('RGB', (768, 1024), 'white').save(content, 'PNG')
                return Mock(status=200, body=AsyncMock(return_value=content.getvalue()))
            client = SimpleNamespace(_context=Mock())
            client._context.request.get = AsyncMock(side_effect=download)
            with patch('flow_readback.snapshot', new=AsyncMock(return_value=decode_listing(listing(), PROJECT))):
                result = await recover_context(client, context, PROJECT, 'a' * 64, time.time() + 5, Path(tmp) / 'art.png')
            self.assertEqual(result['images'][0]['media_name'], MEDIA)
            self.assertTrue(Path(result['images'][0]['local_path']).is_file())
            self.assertEqual(json.loads(context.read_text())['readback'], 'exact_image_recovered')
            self.assertNotIn('https://', context.read_text())

    async def test_lost_upload_response_recovers_without_uploading_twice(self):
        from unittest.mock import MagicMock
        from types import SimpleNamespace
        from gflow_cli.api.transports.migrated_composer import MigratedComposer
        handlers = {}
        page = MagicMock()
        page.on.side_effect = lambda event, callback: handlers.update({event: callback})
        locator = page.locator.return_value
        locator.count = AsyncMock(return_value=0)
        locator.first.count = AsyncMock(return_value=1)
        locator.first.click = AsyncMock(); locator.first.wait_for = AsyncMock()
        chooser = SimpleNamespace(set_files=AsyncMock())
        async def chosen(): return chooser
        page.expect_file_chooser.return_value.__aenter__ = AsyncMock(return_value=SimpleNamespace(value=chosen()))
        page.expect_file_chooser.return_value.__aexit__ = AsyncMock(return_value=False)
        async def upload(*args):
            handlers['request'](Mock(url='https://flow.google.com/data/batchexecute?rpcids=maseQ'))
            await handlers['response'](Mock(url='https://flow.google.com/data/batchexecute?rpcids=maseQ',
                status=200, finished=AsyncMock(return_value=None), text=AsyncMock(side_effect=RuntimeError('body unavailable'))))
        chooser.set_files.side_effect = upload
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                'GFLOW_SHADOW_SUBMIT_MARKER': str(Path(tmp) / 'flow_video_attempt_1.submit'),
                'GFLOW_SHADOW_REQUEST_HASH': 'a' * 64}, clear=True), \
                patch('gflow_cli.api.transports.migrated_composer._unique_display_name', return_value='unique.png'), \
                patch('flow_readback.snapshot', new=AsyncMock(return_value=decode_listing(upload_listing(), PROJECT))):
            source = Path(tmp) / 'frame.png';source.write_bytes(b'original')
            result = await MigratedComposer()._upload_via_toolbar(page, PROJECT, source)
            self.assertEqual(result, (MEDIA, 'unique.png'))
            chooser.set_files.assert_awaited_once()
            data = json.loads((Path(tmp) / 'flow_video_attempt_1.upload.json').read_text())
            self.assertEqual(data['state'], 'recovered')


class RecoveryCommandTests(unittest.TestCase):
    def test_timed_out_recovery_terminates_and_reaps_its_browser_group(self):
        import signal, subprocess
        from flow_media_client import FlowMediaClient
        process = Mock(pid=1234)
        process.communicate.side_effect = [subprocess.TimeoutExpired('recover', 1), ('', '')]
        process.__enter__ = Mock(return_value=process)
        process.__exit__ = Mock(return_value=False)
        with patch('flow_media_client.subprocess.Popen', return_value=process), \
                patch('flow_media_client.os.killpg') as kill:
            self.assertEqual(FlowMediaClient._recovery_command(['recover'], 1), (-1, {}))
        kill.assert_called_once_with(1234, signal.SIGTERM)
        self.assertEqual(process.communicate.call_count, 2)

    def test_sdk_logging_cannot_corrupt_machine_readable_result(self):
        from contextlib import redirect_stdout, redirect_stderr
        import io
        from ops import flow_media_recover
        async def download(*args, **kwargs): print('synthetic SDK diagnostic')
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['recover', PROJECT, MEDIA, '/tmp/original.png']), \
                patch.object(flow_media_recover, 'download_existing', new=AsyncMock(side_effect=download)), \
                redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(flow_media_recover.main(), 0)
        self.assertEqual(json.loads(output.getvalue())['status'], 'downloaded')
        self.assertIn('synthetic SDK diagnostic', errors.getvalue())


if __name__ == '__main__':
    unittest.main()

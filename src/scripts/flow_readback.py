"""Read existing Flow project media; this module never submits or uploads."""
import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote_plus, urljoin, urlsplit, urlunsplit


class ReadbackUnavailable(ValueError):
    pass


class ReadbackIdentityError(ReadbackUnavailable):
    pass


def fingerprint(text):
    return hashlib.sha256(text.encode()).hexdigest()


def save(path, data):
    try:
        temporary = path.with_suffix(path.suffix + '.tmp')
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(data, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        raise ReadbackIdentityError('Private recovery evidence could not be persisted') from None


def context_path(marker):
    return Path(marker).with_suffix('.context.json')


def identity_hash(data):
    keys = ('version', 'request_hash', 'profile', 'project_id', 'baseline', 'seed', 'prompt_hash', 'model', 'aspect', 'count', 'attempt', 'frozen_inputs_sha256')
    return fingerprint(json.dumps({key: data[key] for key in keys}, sort_keys=True))


def decode_listing(payload, project):
    from gflow_cli.api.transports.batchexecute import image_records
    from gflow_cli.errors import WireFormatError
    uuid = re.compile(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}')
    # The current host omits both repeated fields for a project with no media.
    if isinstance(payload, list) and len(payload) == 8 and payload[1] is None and payload[2] is None:
        payload = payload[:1] + [[], []] + payload[3:]
    if (not isinstance(payload, list) or len(payload) != 8 or payload[0] is not None
            or any(payload[i] is not None for i in (4, 5, 6))
            or not all(isinstance(payload[i], list) for i in (1, 2, 3, 7))):
        raise ReadbackUnavailable('Unrecognized or incomplete project listing')
    media = {}
    for row in payload[2]:
        if (not isinstance(row, list) or len(row) not in (7, 8) or not isinstance(row[0], str)
                or not uuid.fullmatch(row[0].removesuffix('_upsampled'))
                or (len(row) == 7 and not uuid.fullmatch(row[0])) or row[1] != project or row[0] in media):
            raise ReadbackUnavailable('Unverified project media')
        media[row[0]] = row
    uploads = []
    for row in payload[1]:
        if (not isinstance(row, list) or len(row) != 5 or row[4] != project
                or not isinstance(row[3], list) or len(row[3]) != 7):
            raise ReadbackUnavailable('Unverified workflow')
        name, media_id = row[3][0], row[3][4]
        asset = media.get(media_id)
        if (isinstance(name, str) and asset and len(asset) == 7 and asset[2] == row[0]
                and isinstance(asset[6], list) and len(asset[6]) == 6
                and asset[6][0] is None and isinstance(asset[6][1], list) and len(asset[6][1]) == 7):
            uploads.append({'name': name, 'media_id': media_id})
    try:
        images = image_records('Zzl0ze', payload)
    except WireFormatError:
        images = []
    if any(image.project_id != project or image.media_id not in media for image in images):
        raise ReadbackUnavailable('Image project differs')
    return {'ids': sorted(media), 'images': images, 'media': media, 'uploads': uploads}


async def snapshot(context, project, deadline):
    """Reload in a separate page; retry only reads, under one caller-owned deadline."""
    from gflow_cli.api.transports.batchexecute import parse_frames
    last = ReadbackUnavailable('Project read deadline exhausted')
    while time.time() < deadline:
        try:
            page = await asyncio.wait_for(context.new_page(), max(.001, deadline - time.time()))
        except Exception:
            raise ReadbackUnavailable('Project page unavailable') from None
        future = asyncio.get_running_loop().create_future()
        tasks = set()

        async def observe(response):
            if 'Zzl0ze' not in parse_qs(urlsplit(response.url).query).get('rpcids', []):
                return
            try:
                if response.status != 200:
                    raise ReadbackUnavailable('Project read HTTP failure')
                if project not in unquote_plus(response.request.post_data or ''):
                    raise ReadbackUnavailable('Project listing request differs')
                for rpc, payload in parse_frames(await response.text()):
                    if rpc == 'Zzl0ze' and not future.done():
                        future.set_result(decode_listing(payload, project))
            except Exception:
                if not future.done():
                    future.set_exception(ReadbackUnavailable('Project response unavailable'))

        def response_handler(response):
            task = asyncio.create_task(observe(response))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

        page.on('response', response_handler)
        try:
            await page.goto(f'https://flow.google.com/project/{project}', wait_until='domcontentloaded',
                            timeout=max(1, min(15000, (deadline - time.time()) * 1000)))
            return await asyncio.wait_for(asyncio.shield(future), max(.001, deadline - time.time()))
        except Exception as exc:
            last = ReadbackUnavailable(type(exc).__name__)
        finally:
            page.remove_listener('response', response_handler)
            for task in tasks:
                task.cancel()
            await page.close()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if future.done() and not future.cancelled():
                future.exception()
            else:
                future.cancel()
        await asyncio.sleep(min(.5, max(0, deadline - time.time())))
    raise last


async def prepare_image(page):
    marker = os.getenv('GFLOW_SHADOW_SUBMIT_MARKER')
    request_hash = os.getenv('GFLOW_SHADOW_REQUEST_HASH', '')
    if not marker or not re.fullmatch('[0-9a-f]{64}', request_hash):
        return None
    project = os.getenv('FLOW_PREFLIGHT_PROJECT_ID', '')
    match = re.fullmatch(r'(?:/u/\d+)?/project/([0-9a-f-]{36})/?', urlsplit(str(page.url)).path)
    if not match or match[1] != project:
        raise ReadbackUnavailable('Image project identity unavailable')
    path = context_path(marker)
    frozen_hash = hashlib.sha256(path.with_name('flow_inputs.json').read_bytes()).hexdigest()
    if path.exists():
        previous = json.loads(path.read_text())
        if (Path(marker).exists() or 'seed' in previous or previous.get('version') != 1
                or previous.get('request_hash') != request_hash or previous.get('project_id') != project
                or previous.get('frozen_inputs_sha256') != frozen_hash
                or previous.get('profile') != os.getenv('GFLOW_PROFILE', 'shadow')):
            raise ReadbackIdentityError('Image attempt context conflicts')
        return previous
    number = re.search(r'_attempt_(\d+)\.submit$', marker)
    data = {'version': 1, 'attempt': int(number[1]) if number else None,
            'frozen_inputs_sha256': frozen_hash,
            'request_hash': request_hash, 'profile': os.getenv('GFLOW_PROFILE', 'shadow'),
            'project_id': project, 'baseline_deadline': time.time() + 30, 'baseline': None}
    save(path, data)
    try:
        listing = await snapshot(page.context, project, data['baseline_deadline'])
        data['baseline'] = listing['ids']
    except ReadbackUnavailable:
        data['readback'] = 'baseline_unavailable'
    save(path, data)
    return data


def bind_request(data, marker, payload, prompt):
    if data is None:
        return
    row = payload[1][0]
    if type(row[3]) is not int or not 0 <= row[3] < 2**32 or row[8] != [[[prompt]]]:
        raise ReadbackUnavailable('Actual image request identity unavailable')
    data.update(seed=row[3], prompt_hash=fingerprint(prompt), model='BELUGA', aspect=4, count=1,
                recovery_deadline=None, response={'response_finished': False, 'body_readable': False, 'decoded': False})
    save(context_path(marker), data)


def select_image(listing, data):
    if data.get('baseline') is None:
        raise ReadbackUnavailable('Baseline unavailable')
    if (data.get('version') != 1 or not isinstance(data.get('baseline'), list)
            or any(not isinstance(item, str) for item in data['baseline'])
            or data.get('model') != 'BELUGA' or data.get('aspect') != 4 or data.get('count') != 1
            or type(data.get('seed')) is not int or not re.fullmatch('[0-9a-f]{64}', data.get('prompt_hash', ''))):
        raise ReadbackIdentityError('Incomplete image readback identity')
    matches = []
    for image in listing['images']:
        if (image.media_id not in data['baseline'] and image.project_id == data['project_id']
                and image.seed == data['seed'] and fingerprint(image.prompt) == data['prompt_hash']):
            row = listing['media'][image.media_id]
            details = row[6][0]
            if len(details) != 15 or details[8] != 36 or details[14] != 4:
                raise ReadbackIdentityError('Recovered image settings differ')
            if min(image.dimensions) < 512 or abs(image.dimensions[0] / image.dimensions[1] - .75) > .015:
                raise ReadbackUnavailable('Recovered image dimensions differ')
            matches.append(image)
    data['candidate_count'] = len(matches)
    if len(matches) != 1:
        raise ReadbackUnavailable('Exact image absent or ambiguous')
    return matches[0]


def note_response(data, **fields):
    data.setdefault('response', {}).update(fields)
    save(context_path(os.environ['GFLOW_SHADOW_SUBMIT_MARKER']), data)


def note_upload_response(record, **fields):
    record.setdefault('response', {}).update(fields)
    save(upload_path(), record)


def upload_path():
    return Path(os.environ['GFLOW_SHADOW_SUBMIT_MARKER']).with_suffix('.upload.json')


def prepare_upload(project, source, name):
    data = {'version': 1, 'project_id': project, 'profile': os.getenv('GFLOW_PROFILE', 'shadow'),
            'request_hash': os.environ['GFLOW_SHADOW_REQUEST_HASH'],
            'source_hash': hashlib.sha256(source.read_bytes()).hexdigest()}
    path = upload_path()
    if path.exists():
        saved = json.loads(path.read_text())
        if any(saved.get(key) != value for key, value in data.items()):
            raise ReadbackIdentityError('Saved upload identity changed')
        if saved.get('state') not in {'prepared', 'attempted', 'recovered'}:
            raise ReadbackIdentityError('Saved upload state invalid')
        return saved
    data.update(name=name, state='prepared', readback_deadline=None)
    save(path, data)
    return data


def mark_upload(record, state):
    record['state'] = state
    save(upload_path(), record)


async def recover_upload(page, project, source, record):
    if (record['project_id'] != project or record['profile'] != os.getenv('GFLOW_PROFILE', 'shadow')
            or hashlib.sha256(source.read_bytes()).hexdigest() != record['source_hash']):
        raise ReadbackIdentityError('Upload identity changed')
    while time.time() < record['readback_deadline']:
        listing = await snapshot(page.context, project, record['readback_deadline'])
        matches = [row['media_id'] for row in listing['uploads'] if row['name'] == record['name']]
        record['candidate_count'] = len(matches)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ReadbackUnavailable('Exact upload ambiguous')
        await asyncio.sleep(min(1, max(0, record['readback_deadline'] - time.time())))
    raise ReadbackUnavailable('Exact upload unavailable')


async def recover_saved_upload(page, project, source, record):
    from gflow_cli.errors import MediaUploadRejectedError, WireFormatError
    if record['readback_deadline'] is None:
        record['readback_deadline'] = time.time() + 30
        save(upload_path(), record)
    try:
        if (record['project_id'] != project or record['profile'] != os.getenv('GFLOW_PROFILE', 'shadow')
                or hashlib.sha256(source.read_bytes()).hexdigest() != record['source_hash']):
            raise ReadbackIdentityError('Saved upload identity changed')
        if record['state'] == 'recovered':
            if not re.fullmatch(r'[0-9a-f-]{36}', record.get('media_id', '')):
                raise ReadbackIdentityError('Saved upload media identity invalid')
            return record['media_id']
        media = await recover_upload(page, project, source, record)
        record.update(media_id=media, state='recovered', readback='exact_upload_recovered')
        save(upload_path(), record)
        return media
    except ReadbackIdentityError as exc:
        raise WireFormatError(detail=str(exc), route='batchexecute:maseQ') from None
    except ReadbackUnavailable:
        record['readback'] = 'unavailable'
        save(upload_path(), record)
        raise MediaUploadRejectedError(detail='upload_response_read_failed: exact upload readback unavailable',
                                       status=200, route='batchexecute:maseQ') from None


def receipt_path(context_file):
    return context_file.with_name(context_file.name.removesuffix('.context.json') + '.result.json')


async def recover_context(client, context_file, project, request_hash, deadline, output):
    started = time.time()
    marker = context_file.with_name(context_file.name.removesuffix('.context.json') + '.submit')
    try:
        data = json.loads(context_file.read_text())
        frozen_hash = hashlib.sha256(context_file.with_name('flow_inputs.json').read_bytes()).hexdigest()
        evidence = json.loads(marker.read_text())
    except (OSError, ValueError):
        raise ReadbackIdentityError('Private recovery evidence unreadable') from None
    if (data.get('version') != 1 or data.get('project_id') != project
            or data.get('profile') != os.getenv('GFLOW_PROFILE', 'shadow') or data.get('request_hash') != request_hash):
        raise ReadbackIdentityError('Saved readback identity changed')
    if frozen_hash != data.get('frozen_inputs_sha256'):
        raise ReadbackIdentityError('Frozen inputs changed')
    number = re.search(r'_attempt_(\d+)\.submit$', marker.name)
    if not number or data.get('attempt') != int(number[1]):
        raise ReadbackIdentityError('Saved attempt identity changed')
    if (evidence.get('state') != 'forwarded' or evidence.get('actual_model') != 'BELUGA'
            or evidence.get('selected_model') != 'Nano Banana 2.1' or evidence.get('expected_model') != 'NARWHAL'
            or evidence.get('readback_hash') != identity_hash(data)):
        raise ReadbackIdentityError('Submitted request not verified')
    deadline = min(deadline, data.get('recovery_deadline') or deadline)
    data['recovery_deadline'] = deadline
    save(context_file, data)
    if data.get('baseline') is None:
        raise ReadbackUnavailable('Baseline unavailable')
    while time.time() < deadline:
        try:
            listing = await snapshot(client._context, project, deadline)
            data['listed_media_count'] = len(listing['ids'])
            image = select_image(listing, data)
            receipt = {'request_hash': request_hash, 'profile': data['profile'], 'project_id': project,
                       'count': 1, 'model': 'NARWHAL', 'images': [{'media_name': image.media_id,
                       'workflow_id': image.workflow_id, 'model_name_type': 'BELUGA',
                       'aspect_ratio': 'IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR'}]}
            save(receipt_path(context_file), receipt)
            data['readback'] = 'exact_image_recovered'
            data['media_id'] = image.media_id
            data.update(candidate_count=1, elapsed_seconds=round(time.time() - started, 3))
            save(context_file, data)
            await download_image(client, image, output)
            receipt['images'][0]['local_path'] = str(output)
            save(receipt_path(context_file), receipt)
            return receipt
        except ReadbackIdentityError:
            raise
        except ReadbackUnavailable:
            await asyncio.sleep(min(3, max(0, deadline - time.time())))
    data['readback'] = 'unavailable'
    data['elapsed_seconds'] = round(time.time() - started, 3)
    save(context_file, data)
    raise ReadbackUnavailable('Image recovery deadline exhausted')


async def download_image(client, image, output):
    from PIL import Image
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.stem + '.download' + output.suffix)
    try:
        url, deadline = image.image_url, time.time() + 120
        address = urlsplit(url)
        if address.hostname in {'lh3.googleusercontent.com', 'lh3.google.com'}:
            path = re.sub(r'=[ws]\d+[a-z0-9-]*$', '', address.path) + '=s0'
            url = urlunsplit(address._replace(path=path))
        for _ in range(3):
            address = urlsplit(url)
            if (address.scheme != 'https' or address.hostname not in
                    {'flow-content.google', 'lh3.googleusercontent.com', 'lh3.google.com'}
                    or address.port not in (None, 443) or address.username is not None or address.password is not None):
                raise ReadbackIdentityError('Untrusted original image download host')
            response = await client._context.request.get(url, max_redirects=0,
                timeout=max(1, (deadline - time.time()) * 1000))
            if response.status in (301, 302, 303, 307, 308) and response.headers.get('location'):
                url = urljoin(url, response.headers['location'])
                continue
            if response.status != 200:
                raise ReadbackUnavailable('Original image download unavailable')
            temporary.write_bytes(await response.body())
            break
        else:
            raise ReadbackUnavailable('Original image redirect limit exhausted')
        with Image.open(temporary) as candidate:
            candidate.load()
            if candidate.size != image.dimensions or min(candidate.size) < 512 or abs(candidate.width / candidate.height - .75) > .015:
                raise ReadbackUnavailable('Downloaded original dimensions differ')
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)

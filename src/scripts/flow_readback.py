"""Bind private submission identity and recover exact, existing Flow media."""
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote_plus, urljoin, urlsplit, urlunsplit


class ReadbackUnavailable(ValueError):
    pass


class ReadbackIdentityError(ReadbackUnavailable):
    pass


class ReadbackUnsupported(ReadbackUnavailable):
    pass


def recovery_phase(phase):
    """Only structural events cross the worker boundary; telemetry cannot abort work."""
    if phase not in {'launch','lease','browser','listing','match','editor','download','hash'}:
        return
    try:
        print('STATE_ZERO_PHASE '+json.dumps({'phase':phase,'at':round(time.time(),3)}),
              file=sys.stderr,flush=True)
    except Exception:
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
    if data.get('kind') == 'video':
        keys += ('kind', 'source_hash', 'frame_id', 'duration', 'wire_verified')
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
        raise ReadbackUnsupported('Unrecognized or incomplete project listing')
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
    recovery_phase('listing')
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
            except Exception as exc:
                if not future.done():
                    future.set_exception(exc if isinstance(exc, ReadbackUnsupported) else
                                         ReadbackUnavailable('Project response unavailable'))

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
            if isinstance(exc, ReadbackUnsupported):
                raise
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
        raise ReadbackUnsupported('Baseline unavailable')
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
        if saved.get('state') not in {'prepared', 'attempted', 'uploaded', 'recovered'}:
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
    from gflow_cli.errors import WireFormatError
    from gflow_cli.api.transports.migrated_composer import UploadResponseUnavailableError
    if record['readback_deadline'] is None:
        record['readback_deadline'] = time.time() + 30
        save(upload_path(), record)
    try:
        if (record['project_id'] != project or record['profile'] != os.getenv('GFLOW_PROFILE', 'shadow')
                or hashlib.sha256(source.read_bytes()).hexdigest() != record['source_hash']):
            raise ReadbackIdentityError('Saved upload identity changed')
        if record['state'] in {'uploaded', 'recovered'}:
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
        raise UploadResponseUnavailableError(detail='Exact upload readback unavailable',
                                       status=200, route='batchexecute:maseQ') from None


def receipt_path(context_file):
    return context_file.with_name(context_file.name.removesuffix('.context.json') + '.result.json')


async def recover_context(client, context_file, project, request_hash, deadline, output, *, media_id=None, workflow_id=None,
                          mode, candidate=None):
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
    video = data.get('kind') == 'video'
    if video:
        try:
            source_hash=hashlib.sha256(context_file.with_name('flow_start_frame.png').read_bytes()).hexdigest()
        except OSError:
            raise ReadbackIdentityError('Saved video frame unavailable') from None
        if (source_hash != data.get('source_hash')
                or evidence.get('state') != 'forwarded' or evidence.get('actual_model') != data.get('model')
                or evidence.get('readback_hash') != identity_hash(data)):
            raise ReadbackIdentityError('Submitted video identity differs')
    elif (evidence.get('state') != 'forwarded' or evidence.get('actual_model') != 'BELUGA'
            or evidence.get('selected_model') != 'Nano Banana 2.1' or evidence.get('expected_model') != 'NARWHAL'
            or evidence.get('readback_hash') != identity_hash(data)):
        raise ReadbackIdentityError('Submitted request not verified')
    return await recover_candidate(client,context_file,data,deadline,output,mode,candidate,
                                   media_id=media_id,workflow_id=workflow_id)


async def recover_candidate(client, context_file, data, deadline, output, mode, candidate, *, media_id=None, workflow_id=None):
    """Search and retrieval report evidence to the parent; child records are read-only."""
    context_hash=hashlib.sha256(context_file.read_bytes()).hexdigest()
    video=data.get('kind')=='video'
    if mode not in {'search','retrieve'} or mode=='retrieve' and (
            not isinstance(candidate,dict) or candidate.get('context_sha256')!=context_hash
            or candidate.get('kind')!=('video' if video else 'image')):
        raise ReadbackIdentityError('Retrieval candidate identity changed')
    while time.time()<deadline:
        try:
            listing=await snapshot(client._context,data['project_id'],deadline)
            if video:
                known=candidate['result'] if candidate else {}
                clip=select_video(listing,data,media_id=known.get('media_id') or media_id,
                                  workflow_id=known.get('workflow_id') or workflow_id)
                result=dict(request_hash=data['request_hash'],profile=data['profile'],project_id=data['project_id'],
                    succeeded=True,media_id=clip['media_id'],workflow_id=clip['workflow_id'],
                    request={'count':1,'model':'veo_3_1_fast','mode':'i2v','aspect':'portrait'})
                selected={'bytes':clip['bytes']}
            else:
                image=select_image(listing,data)
                if media_id and image.media_id!=media_id:
                    raise ReadbackIdentityError('Known image result conflicts with exact match')
                result=dict(request_hash=data['request_hash'],profile=data['profile'],project_id=data['project_id'],
                    count=1,model='NARWHAL',images=[dict(media_name=image.media_id,workflow_id=image.workflow_id,
                    model_name_type='BELUGA',aspect_ratio='IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR')])
                selected={'dimensions':list(image.dimensions)}
            recovery_phase('match')
            found=dict(kind='video' if video else 'image',context_sha256=context_hash,result=result,**selected)
            if mode=='search':
                return found | {'observed_at':time.time()}
            if mode=='retrieve' and found!={k:v for k,v in candidate.items() if k!='observed_at'}:
                raise ReadbackIdentityError('Completed candidate changed before retrieval')
            if video:
                await asyncio.wait_for(download_video(client,clip,output,deadline=deadline),max(.001,deadline-time.time()))
                result['local_path']=str(output)
            else:
                recovery_phase('download')
                await asyncio.wait_for(download_image(client,image,output),max(.001,deadline-time.time()))
                result['images'][0]['local_path']=str(output)
            recovery_phase('hash')
            result['sha256']=hashlib.sha256(output.read_bytes()).hexdigest()
            return result
        except ReadbackIdentityError:
            raise
        except ReadbackUnavailable:
            if mode=='retrieve':
                raise
            await asyncio.sleep(min(3,max(0,deadline-time.time())))
    raise ReadbackUnavailable('Exact search deadline exhausted')


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


def select_video(listing, data, *, media_id=None, workflow_id=None):
    """Select one completed original using recorded identities, never list order."""
    from gflow_cli.api.transports.batchexecute import generation_record
    from gflow_cli.errors import WireFormatError
    model = 'veo_3_1_i2v_s_fast_portrait'
    if (data.get('model') != model or data.get('aspect') != 'portrait'
            or data.get('count') != 1 or data.get('duration') != 8):
        raise ReadbackIdentityError('Saved video settings differ')
    if not media_id and (data.get('baseline') is None or not data.get('frame_id')
                         or not data.get('prompt_hash') or not data.get('wire_verified')):
        raise ReadbackUnsupported('Video discovery identity unavailable')
    if data.get('baseline') is not None and (not isinstance(data['baseline'],list)
            or any(not isinstance(value,str) for value in data['baseline'])):
        raise ReadbackIdentityError('Video baseline is corrupt')
    matches = []
    for row in listing['media'].values():
        try:
            record = generation_record('Zzl0ze', row)
        except WireFormatError:
            continue
        if media_id and record.workflow_id == media_id and record.media_id != media_id:
            raise ReadbackIdentityError('Video media/workflow identities were interchanged')
        if media_id and record.media_id != media_id:
            continue
        if workflow_id and record.workflow_id != workflow_id:
            raise ReadbackIdentityError('Video workflow identity differs')
        if record.project_id != data.get('project_id'):
            raise ReadbackIdentityError('Video project identity differs')
        try:
            settings = row[5][6][1]
            frames = settings[1]
            seed = row[7][0][1]
            duration = row[7][1][2][0]
            valid_settings = settings[0][0] == model and settings[0][2] == [1] and duration == 8
            bound_frame = len(frames) == 1 and frames[0][1] == 1 and frames[0][2] == data.get('frame_id')
            same_prompt = fingerprint(row[5][1]) == data.get('prompt_hash')
        except (IndexError, TypeError, KeyError):
            if media_id:
                raise ReadbackIdentityError('Video listing structure differs') from None
            continue
        identity_matches = (not data.get('frame_id') or bound_frame) and (not data.get('prompt_hash') or same_prompt)
        if data.get('seed') is not None:
            identity_matches = identity_matches and seed == data['seed']
        if not valid_settings or not identity_matches:
            if media_id:
                raise ReadbackIdentityError('Known video inputs or settings differ')
            continue
        if not media_id and record.workflow_id in data['baseline']:
            continue
        matches.append((record, {'media_id':record.media_id, 'workflow_id':record.workflow_id,
                        'project_id':record.project_id, 'bytes':record.size_bytes,
                        'video_url':record.video_url}))
    if len(matches) != 1:
        raise ReadbackUnavailable('Exact video absent or ambiguous')
    record, clip = matches[0]
    if record.is_failed:
        from flow_media_client import FlowCommandError
        raise FlowCommandError('video', 'generation_failed', media_id=record.media_id,
            operation_id=record.workflow_id, retryable=True, submission_state='forwarded')
    if not record.is_done or not record.size_bytes:
        raise ReadbackUnavailable('Exact video not completed')
    return clip


async def download_video(client, clip, output, *, deadline):
    """Download a proven original; keep signed URLs in memory only."""
    from gflow_cli.api.transports.migrated_recover import _download_original_verified
    from flow_media_client import FlowMediaClient
    if time.time() >= deadline:
        raise ReadbackUnavailable('Video download deadline exhausted')
    expected = clip.get('bytes')
    if type(expected) is not int or not 0 < expected <= 100 * 1024 * 1024:
        raise ReadbackIdentityError('Original video size unavailable')
    address = urlsplit(clip.get('video_url') or '')
    if (address.scheme == 'https' and address.hostname == 'flow-content.google'
            and address.port in (None,443) and address.username is None and address.password is None
            and address.path == '/video/' + clip['workflow_id']):
        recovery_phase('download')
        response = await client._context.request.get(clip['video_url'], max_redirects=0,
            timeout=max(1,(deadline-time.time())*1000))
        if response.status != 200:
            raise ReadbackUnavailable('Original video download unavailable')
        body = await response.body()
    else:
        recovery_phase('editor')
        await client.page.goto(f'https://flow.google.com/project/{clip["project_id"]}/edit/{clip["media_id"]}',
                              wait_until='domcontentloaded',timeout=max(1,min(60000,(deadline-time.time())*1000)))
        current=urlsplit(str(client.page.url))
        if (current.scheme != 'https' or current.hostname != 'flow.google.com'
                or current.path != f'/project/{clip["project_id"]}/edit/{clip["media_id"]}'):
            raise ReadbackIdentityError('Original download editor identity differs')
        recovery_phase('download')
        body = await asyncio.wait_for(_download_original_verified(client.page,expected=expected,
            media_id=clip['media_id']),max(.001,deadline-time.time()))
    if len(body) != expected or body[4:8] != b'ftyp':
        raise ReadbackUnavailable('Original video bytes do not match its receipt')
    if clip.get('sha256') and hashlib.sha256(body).hexdigest() != clip['sha256']:
        raise ReadbackIdentityError('Original video checksum changed')
    output.parent.mkdir(parents=True,exist_ok=True)
    temporary = output.with_name(output.stem+'.download'+output.suffix)
    try:
        temporary.write_bytes(body)
        remaining=deadline-time.time()
        if remaining <= 0:
            raise ReadbackUnavailable('Video validation deadline exhausted')
        try:
            width,height,duration,audio=FlowMediaClient._probe_video(temporary,timeout=min(30,remaining))
        except Exception as exc:
            raise ReadbackUnavailable('Original video cannot be decoded') from exc
        if (width,height) not in ((720,1280),(1080,1920)) or not 7.5<=duration<=8.5 or not audio:
            raise ReadbackUnavailable('Original video properties differ')
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


async def prepare_video(page, project, prompt, source, frame_id):
    marker = os.getenv('GFLOW_SHADOW_SUBMIT_MARKER')
    request_hash = os.getenv('GFLOW_SHADOW_REQUEST_HASH', '')
    if not marker or not re.fullmatch('[0-9a-f]{64}', request_hash):
        return None
    path = context_path(marker)
    number = re.search(r'_attempt_(\d+)\.submit$', marker)
    if not number or not source or not frame_id:
        raise ReadbackIdentityError('Video attempt identity unavailable')
    fixed = dict(version=1, kind='video', attempt=int(number[1]), request_hash=request_hash,
                 project_id=project, profile=os.getenv('GFLOW_PROFILE', 'shadow'),
                 frozen_inputs_sha256=hashlib.sha256(path.with_name('flow_inputs.json').read_bytes()).hexdigest(),
                 source_hash=hashlib.sha256(source.read_bytes()).hexdigest(), frame_id=frame_id,
                 prompt_hash=fingerprint(prompt), model='veo_3_1_i2v_s_fast_portrait',
                 aspect='portrait', count=1, duration=8)
    if path.exists():
        previous = json.loads(path.read_text())
        if Path(marker).exists() or any(previous.get(k) != v for k,v in fixed.items()):
            raise ReadbackIdentityError('Video attempt context conflicts')
        return previous
    data = fixed | dict(baseline_deadline=time.time()+30, baseline=None, wire_verified=False,
                        seed=None, recovery_deadline=None)
    save(path,data)
    try:
        data['baseline'] = (await snapshot(page.context,project,data['baseline_deadline']))['ids']
    except ReadbackUnavailable:
        data['readback'] = 'baseline_unavailable'
    save(path,data)
    return data


def _video_request_rows(body):
    encoded = parse_qs(body).get('f.req',[None])[0] or body.split('f.req=',1)[1]
    outer = json.JSONDecoder().raw_decode(encoded)[0]
    return [row for group in outer for row in group]


def video_submit_rpc(request):
    from gflow_cli.api.transports.migrated_composer import SUBMIT_RPCS
    try:
        for row in _video_request_rows(request.post_data or ''):
            if row[0] in SUBMIT_RPCS:
                return row[0]
    except (ValueError,TypeError,IndexError,KeyError):
        pass
    for rpc in parse_qs(urlsplit(request.url).query).get('rpcids', [''])[0].split(','):
        if rpc in SUBMIT_RPCS:
            return rpc
    return None


async def guard_video(route, request, data, rpc):
    """One forwarding transition, with durable identity before any paid request."""
    marker = Path(os.environ['GFLOW_SHADOW_SUBMIT_MARKER'])
    problem = None
    try:
        if marker.read_text() != 'submit_attempted\n':
            raise ReadbackIdentityError('Existing video submission requires reconciliation')
        from gflow_cli.api.transports.migrated_composer import _i2v_body_problem
        body = request.post_data or ''
        problem = _i2v_body_problem(body,rpc,data['frame_id'])
        try:
            rows = _video_request_rows(body)
            payload = json.loads(rows[0][1]) if len(rows) == 1 and rows[0][0] == rpc else None
            row = payload[0][0]
            if len(payload) == 3 and isinstance(payload[0],list) and len(payload[0]) != 1:
                problem = 'Video actual output count differs'
            recognized = len(payload) == 3 and len(payload[0]) == 1 and len(row) == 6
            if not recognized:
                problem = 'Video actual request structure is unverified'
            if recognized:
                if (rpc != 'eb1hJf' or row[1] != data['model'] or row[2] != 1
                        or row[4][1] != data['frame_id'] or payload[1][5] != data['project_id']
                        or fingerprint(row[0][2][0][0][0]) != data['prompt_hash']
                        or row[3] is not None and (type(row[3]) is not int or not 0 <= row[3] < 2**32)):
                    problem = 'Video actual request inputs or settings differ'
                else:
                    data.update(wire_verified=True, seed=row[3])
        except (ValueError,TypeError,IndexError,KeyError):
            data['wire_verified'] = False
            problem = 'Video actual request structure is unverified'
        save(context_path(marker),data)
        evidence = {'version':1, 'state':'blocked_before_submission' if problem else 'forwarded',
                    'readback_hash':identity_hash(data), 'actual_model':data['model']}
        if problem:
            evidence['reason'] = 'video_request_settings_mismatch'
        save(marker,evidence)
    except (OSError,ValueError,KeyError) as exc:
        problem = 'Video evidence unavailable; reconciliation required: '+type(exc).__name__
    if problem:
        await route.abort()
    else:
        await route.continue_()
        note_video_lifecycle(data,"route_continued")
    return problem


def video_receipt(data, record, context_file):
    if record.project_id != data['project_id']:
        raise ReadbackIdentityError('Video result project differs')
    result = {'request_hash':data['request_hash'], 'profile':data['profile'],
              'project_id':data['project_id'], 'media_id':record.media_id, 'workflow_id':record.workflow_id,
              'succeeded':True, 'request':{'count':1,'model':'veo_3_1_fast','mode':'i2v','aspect':'portrait'}}
    path = receipt_path(context_file)
    if path.exists():
        previous=json.loads(path.read_text())
        if any(previous.get(k)!=v for k,v in result.items()):
            raise ReadbackIdentityError('Video result identity changed')
        result=previous
    save(path,result)
    return result


def note_video_response(data, *, phase, rpc, response, exc=None, decoded=False, read_started=None, event_received=None, upload=False):
    try:
        if data is None:
            return
        fields=dict(phase=phase,rpc=rpc,http_status=response.status,
                    body_readable=exc is None,decoded=decoded,
                    routing_active=data.get('observer',{}).get('routing_active',False))
        if read_started is not None:
            fields['read_ms']=max(0,round((time.monotonic()-read_started)*1000))
            if event_received is not None:
                fields['event_to_read_ms']=max(0,round((read_started-event_received)*1000))
        observation=data.setdefault('observer',{'version':1,'counters':{},'events':[]})
        key=f'{phase}:{rpc}:readable' if exc is None else f'{phase}:{rpc}:unreadable'
        if not decoded and len(observation['counters'])<32:
            observation['counters'][key]=min(100000,observation['counters'].get(key,0)+1)
        if exc is not None:
            detail=('Network.getResponseBody: body unavailable' if 'Network.getResponseBody' in str(exc)
                    else 'Response body unavailable')
            fields.update(error_class=type(exc).__name__,detail=detail)
            data.setdefault('original_response_failure',fields.copy())
        data['response']=fields
        (note_upload_response if upload else note_response)(data,**fields)
    except Exception:
        pass


def note_video_lifecycle(data, event, *, request=None, upload=False):
    try:
        if data is None or event not in {'routing_active','routing_removed','request_finished','request_failed',
                'page_closed','context_closed','browser_disconnected','page_navigated','route_continued'}:
            return
        observation=data.setdefault('observer',{'version':1,'counters':{},'events':[]})
        observation['counters'][event]=min(100000,observation['counters'].get(event,0)+1)
        if event in {'routing_active','routing_removed'}:
            observation['routing_active']=event=='routing_active'
        item={'event':event,'at':round(time.time(),3),
              'routing_active':observation.get('routing_active',False)}
        if event=='request_failed':
            value=getattr(request,'failure',None)
            allowed={'net::ERR_ABORTED','net::ERR_FAILED','net::ERR_CONNECTION_RESET','net::ERR_TIMED_OUT',
                'net::ERR_NETWORK_CHANGED','net::ERR_INTERNET_DISCONNECTED','net::ERR_CONNECTION_CLOSED',
                'net::ERR_NAME_NOT_RESOLVED','net::ERR_BLOCKED_BY_CLIENT'}
            item['failure']=value if isinstance(value,str) and value in allowed else 'OTHER'
        observation['events']=(observation['events']+[item])[-24:]
        (note_upload_response if upload else note_response)(data,observer=observation)
    except Exception:
        pass


def observe_rpc(page, data, rpc, *, upload=False):
    """Observe lifecycle only; the transport remains the sole reader of each body."""
    try:
        if data is None:
            return lambda: None
        listeners=[]
        def record(event,request=None):
            try:
                if request is not None and rpc not in parse_qs(urlsplit(request.url).query).get('rpcids',[''])[0].split(','):
                    return
                note_video_lifecycle(data,event,request=request,upload=upload)
            except Exception:
                pass
        for event in ('requestfailed','requestfinished'):
            callback=lambda request,event=event:record('request_failed' if event=='requestfailed' else 'request_finished',request)
            page.on(event,callback);listeners.append((page,event,callback))
        for emitter,event,name in ((page,'close','page_closed'),(page.context,'close','context_closed'),
                                   (page,'framenavigated','page_navigated'),
                                   (page.context.browser,'disconnected','browser_disconnected')):
            if emitter is None:
                continue
            callback=lambda *args,name=name:record(name)
            emitter.on(event,callback);listeners.append((emitter,event,callback))
        def stop():
            for emitter,event,callback in listeners:
                try:emitter.remove_listener(event,callback)
                except Exception:pass
        return stop
    except Exception:
        return lambda: None

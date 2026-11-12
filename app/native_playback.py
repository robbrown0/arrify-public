"""Small native DTOs over the existing authoritative listening state machine."""
from .native_auth import NativeError
from .library import PlaybackConflict


def snapshot(result: dict) -> dict:
    state = result['listening']
    partial = result.get('_partial', False)
    return {'partial': partial, 'current_track_id': result['current'],
            'position_seconds': result['position'], 'server_time': result['server_time'],
            'queue_revision': state['revision'], 'owner_device_id': state['owner'],
            'owner_epoch': state['epoch'], 'intent': state['intent'],
            'context': None if partial else result.get('playback_context'),
            'shuffle': None if partial else result.get('shuffle', False),
            'repeat_mode': None if partial else result.get('repeat', 'off'),
            'queue_controls_available': None if partial else not (result.get('channel') or result.get('party_id') or (result.get('station') and result['station'].get('mode') != 'mix')),
            'playing': state['playing'], 'observed': state['observed'],
            'duration_seconds': state.get('duration', 0), 'output': state.get('output', ''),
            'seek_target_seconds': state['seek_target'], 'seek_intent': state['seek_intent'],
            'play_id': state['play_id'],
            'upcoming_track_ids': None if partial else [t['id'] for t in result.get('queue', [])[:3]]}


def playback(listening, uid: int, data: dict | None) -> dict:
    if data is None:
        return snapshot(listening.state(uid))
    if data.get('action') not in ('play', 'pause', 'resume', 'seek', 'next', 'previous', 'heartbeat', 'takeover', 'reattach', 'shuffle', 'repeat', 'queue_jump', 'queue_edit', 'queue', 'remove', 'reorder'):
        raise NativeError(400, 'invalid_request', 'Unsupported native playback action.')
    if data.get('action') == 'play':
        ids = data.get('ids')
        if not data.get('channel') and (not isinstance(ids, list) or not 1 <= len(ids) <= 1000 or any(type(i) is not int or i < 1 for i in ids)):
            raise NativeError(400, 'invalid_request', 'Choose 1–1000 track IDs or a channel.')
    if data.get('action') == 'next' and (not isinstance(data.get('transition_id'), str) or not 1 <= len(data['transition_id']) <= 128):
        raise NativeError(400, 'invalid_request', 'Provide a unique transition ID.')
    try:
        return snapshot(listening.command(uid, data))
    except PlaybackConflict as exc:
        raise NativeError(409, 'playback_changed', str(exc)) from None
    except (ValueError, TypeError, KeyError) as exc:
        raise NativeError(400, 'invalid_request', 'Invalid playback command.') from None


def queue_page(listening, uid, request_path):
    from urllib.parse import urlparse, parse_qs
    q = parse_qs(urlparse(request_path).query, keep_blank_values=True)
    if any(len(v) != 1 for v in q.values()):
        raise NativeError(400, 'invalid_request', 'Duplicate parameters.')
    try:
        offset = int(q.get('offset', ['0'])[0]); limit = int(q.get('limit', ['100'])[0])
        revision = q.get('revision', [None])[0]
        if not 0 <= offset <= 50000 or not 1 <= limit <= 100: raise ValueError()
    except ValueError:
        raise NativeError(400, 'invalid_request', 'Invalid queue page.') from None
    with listening.a.lock:
        state = listening.state(uid)
        current = state['listening']['revision']
        if (offset and revision is None) or (revision is not None and str(current) != revision):
            raise NativeError(409, 'playback_changed', 'Queue changed. Refresh it.')
        ids = state['upcoming']
        items = []
        for index in range(offset, min(len(ids), offset + limit)):
            try: track = listening.p.lib.track(ids[index])
            except ValueError: continue
            items.append({'index': index, 'track': {k: track.get(k) for k in ('id','title','artist','album','artwork')}})
        return {'revision': current, 'total': len(ids), 'items': items,
                'next_offset': offset + limit if offset + limit < len(ids) else None}

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
            'repeat_mode': None if partial else result.get('repeat', 'off'),
            'queue_controls_available': None if partial else not (result.get('channel') or result.get('party_id') or (result.get('station') and result['station'].get('mode') != 'mix')),
            'playing': state['playing'], 'observed': state['observed'],
            'seek_target_seconds': state['seek_target'], 'seek_intent': state['seek_intent'],
            'play_id': state['play_id'],
            'upcoming_track_ids': None if partial else [t['id'] for t in result.get('queue', [])[:2]]}


def playback(listening, uid: int, data: dict | None) -> dict:
    if data is None:
        return snapshot(listening.state(uid))
    if data.get('action') not in ('play', 'pause', 'resume', 'seek', 'next', 'previous', 'heartbeat', 'takeover', 'reattach', 'shuffle', 'repeat'):
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

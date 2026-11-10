"""Additive native auth transport; legacy web routes retain their contract."""
import secrets
from .native_auth import NativeError, NativeSessions
from .native_catalog import page as catalog_page
from .native_playback import playback, snapshot, queue_page
from . import native_library
from .library import PlaybackConflict

PREFIX = '/api/v1/native/'


def dispatch(handler, path, data=None):
    if not path.startswith(PREFIX):
        return False
    request_id = secrets.token_hex(12)
    headers = {'Cache-Control': 'no-store', 'Pragma': 'no-cache', 'X-Request-ID': request_id}
    a = handler.server.accounts
    service = NativeSessions(a)
    try:
        if path == PREFIX + 'capabilities' and data is None:
            result = service.capabilities()
            if getattr(handler.server, 'native_media', None):
                result['features'] += ['native_media_manifest_v1', 'native_catalog_v1', 'native_playback_v1', 'native_library_v1', 'native_queue_v1', 'native_favorites_v1', 'native_live_activity_v1']
                result['media_profiles'] = ['original', 'aac', 'mp3']
                result['media_leveling_modes'] = ['off']
                result['native_prefetch_tracks'] = 3
        elif path == PREFIX + 'server-proof' and data is not None:
            try:a.rate_limit(('server-proof', handler.client_address[0]), 120, 60)
            except ValueError:raise NativeError(429, 'rate_limited', 'Retry shortly.') from None
            if not isinstance(data,dict) or set(data) != {'nonce'}:raise NativeError(400, 'invalid_request', 'Invalid server verification request.')
            from .server_identity import identity
            result = identity(a).proof(service.capabilities()['server_id'], data['nonce'])
        elif path in (PREFIX + 'sessions', PREFIX + 'refresh') and data is not None:
            try:
                a.rate_limit(('native-auth', handler.client_address[0], path), 60 if path.endswith('refresh') else 12, 900)
            except ValueError:
                headers['Retry-After'] = '900'
                raise NativeError(429, 'rate_limited', 'Too many attempts. Try again later.') from None
            if path.endswith('refresh'):
                result = service.refresh(data.get('refresh_token'))
            else:
                try:
                    result = service.login(data)
                except ValueError as exc:
                    if str(exc).startswith('Sign-in is busy'):
                        headers['Retry-After'] = '1'
                        raise NativeError(503, 'sign_in_busy', 'Sign-in is busy. Try again.') from None
                    raise NativeError(401, 'invalid_credentials', 'Invalid credentials.') from None
        elif path in (PREFIX + 'sessions', PREFIX + 'sessions/revoke', PREFIX + 'playback/manifest', PREFIX + 'catalog', PREFIX + 'playback', PREFIX + 'playback/queue', PREFIX + 'library', PREFIX + 'library/detail', PREFIX + 'favorites', PREFIX + 'live-activity', PREFIX + 'local-presence', PREFIX + 'playlists'):
            token, kind = handler.token()
            if kind != 'native':
                raise NativeError(401, 'unauthenticated', 'A bearer token is required.')
            try:
                user = a.identity(token, kind)
            except PermissionError:
                raise NativeError(401, 'unauthenticated', 'Sign in again.') from None
            if path == PREFIX + 'local-presence':
                try:a.rate_limit(('native-presence', user['id']), 120, 60)
                except ValueError:raise NativeError(429, 'rate_limited', 'Retry shortly.') from None
                with a.lock:
                    result = handler.server.live_activities.local_presence.dispatch(user['id'], token, data, handler.server.listening.state(user['id'])['listening'])
            elif path == PREFIX + 'live-activity':
                try:a.rate_limit(('native-live-activity', user['id']), 30, 60)
                except ValueError:raise NativeError(429, 'rate_limited', 'Retry shortly.') from None
                result = handler.server.live_activities.dispatch(user['id'], token, data, handler)
            elif path == PREFIX + 'playlists' and data is not None:
                try:a.rate_limit(('native-playlists', user['id']), 30, 60)
                except ValueError:raise NativeError(429, 'rate_limited', 'Retry shortly.') from None
                result = native_library.edit_playlist(handler.server.library, a, user['id'], data)
            elif path == PREFIX + 'favorites':
                try:a.rate_limit(('native-favorites', user['id']), 120, 60)
                except ValueError:raise NativeError(429, 'rate_limited', 'Retry shortly.') from None
                result = native_library.favorites(handler.server.library, a, user['id'], data)
            elif path == PREFIX + 'catalog' and data is None:
                try:a.rate_limit(('native-catalog', user['id']), 180, 60)
                except ValueError:
                    headers['Retry-After'] = '60'
                    raise NativeError(429, 'rate_limited', 'Retry shortly.') from None
                result = catalog_page(handler.server.library, handler.path)
            elif path in (PREFIX + 'library', PREFIX + 'library/detail') and data is None:
                try:a.rate_limit(('native-library', user['id']), 120, 60)
                except ValueError:raise NativeError(429, 'rate_limited', 'Retry shortly.') from None
                if path.endswith('/detail'):
                    result = native_library.detail(handler.server.library, a, user['id'], handler.path)
                else:
                    result = native_library.overview(handler.server.library, a, user['id'])
            elif path == PREFIX + 'playback/queue' and data is None:
                try:a.rate_limit(('native-queue', user['id']), 180, 60)
                except ValueError:raise NativeError(429, 'rate_limited', 'Retry shortly.') from None
                result = queue_page(handler.server.listening, user['id'], handler.path)
            elif path == PREFIX + 'playback':
                expanded = data is not None and data.get('action') == 'play' and 'source' in data
                if expanded:
                    data = native_library.expand_playback(handler.server.library, a, user['id'], data)
                with a.lock:
                    before = snapshot(handler.server.listening.state(user['id'])) if data is not None else {}
                    result = snapshot(handler.server.listening.command(user['id'], data)) if expanded else playback(handler.server.listening, user['id'], data)
                    live = getattr(handler.server, 'live_activities', None)
                    if live and data is not None:
                        live.local_presence.acquired(user['id'], token, before, result, data)
            elif path.endswith('/manifest') and data is not None:
                try:a.rate_limit(('native-manifest', user['id']), 120, 60)
                except ValueError:
                    headers['Retry-After'] = '60'
                    raise NativeError(429, 'rate_limited', 'Retry shortly.') from None
                result = handler.server.native_media.manifest(user['id'], token, data)
            elif path.endswith('/revoke') and data is not None:
                result = service.revoke(user['id'], data.get('session_id'))
            elif path.endswith('/sessions') and data is None:
                result = service.sessions(user['id'])
            else:
                raise NativeError(405, 'method_not_allowed', 'Method not allowed.')
        else:
            raise NativeError(404, 'not_found', 'Endpoint not found.')
        handler.json(result, extra=headers)
    except PlaybackConflict as exc:
        handler.json({'error': {'code': 'playback_changed', 'message': str(exc), 'request_id': request_id}}, 409, headers)
    except (ValueError, LookupError, TypeError):
        handler.json({'error': {'code': 'invalid_request', 'message': 'Invalid library request.', 'request_id': request_id}}, 400, headers)
    except PermissionError:
        handler.json({'error': {'code': 'account_unavailable', 'message': 'Account access is unavailable.', 'request_id': request_id}}, 403, headers)
    except NativeError as exc:
        if exc.status == 401:
            headers['WWW-Authenticate'] = 'Bearer realm="Arrify"'
        handler.json({'error': {'code': exc.code, 'message': exc.message, 'request_id': request_id}}, exc.status, headers)
    return True


def error(handler, status, code, message):
    request_id = secrets.token_hex(12)
    handler.json({'error': {'code': code, 'message': message, 'request_id': request_id}}, status,
                 {'Cache-Control': 'no-store', 'X-Request-ID': request_id})

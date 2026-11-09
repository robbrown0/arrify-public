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
                result['features'] += ['native_media_manifest_v1', 'native_catalog_v1', 'native_playback_v1', 'native_library_v1', 'native_queue_v1']
                result['media_profiles'] = ['original', 'aac', 'mp3']
                result['media_leveling_modes'] = ['off']
                result['native_prefetch_tracks'] = 3
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
        elif path in (PREFIX + 'sessions', PREFIX + 'sessions/revoke', PREFIX + 'playback/manifest', PREFIX + 'catalog', PREFIX + 'playback', PREFIX + 'playback/queue', PREFIX + 'library', PREFIX + 'library/detail'):
            token, kind = handler.token()
            if kind != 'native':
                raise NativeError(401, 'unauthenticated', 'A bearer token is required.')
            try:
                user = a.identity(token, kind)
            except PermissionError:
                raise NativeError(401, 'unauthenticated', 'Sign in again.') from None
            if path == PREFIX + 'catalog' and data is None:
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
                if data is not None and data.get('action') == 'play' and 'source' in data:
                    data = native_library.expand_playback(handler.server.library, a, user['id'], data)
                    result = snapshot(handler.server.listening.command(user['id'], data))
                    handler.json(result, extra=headers)
                    return True
                result = playback(handler.server.listening, user['id'], data)
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

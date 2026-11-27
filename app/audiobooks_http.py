"""Same-origin audiobook routes; administrative provider setup stays private."""
import re
import time
from urllib.parse import parse_qs, urlsplit

from .audiobook_provider import BookError, identifier


def dispatch(handler, path, data=None):
    if not (path.startswith('/api/v1/audiobooks') or path == '/api/v1/admin/audiobooks'): return False
    books = getattr(handler.server, 'audiobooks', None)
    if books is None:
        handler.json({'error': 'Audiobooks are not configured.'}, 404); return True
    try:
        stream = re.fullmatch(r'/api/v1/audiobooks/stream/([A-Za-z0-9_-]{43})/([0-9]{1,4})', path)
        if stream and data is None:
            uid, _, _ = books.stream(stream[1], int(stream[2]))
            key = handler.server.active_streams.open(uid, None, 'audiobook')
            try: serve_stream(handler, books, stream[1], int(stream[2]), key)
            finally: handler.server.active_streams.close(key)
            return True
        try: user = handler.identity(write=data is not None)
        except PermissionError:
            if handler.token()[1] != 'native': raise
            handler.json({'error': {'code':'unauthenticated','message':'Sign in again.'}}, 401); return True
        uid = user['id']
        if path == '/api/v1/admin/audiobooks':
            if not user['admin']: raise BookError('Administrator access required.', 403)
            if data is None: result = books.provider.public_config()
            else:
                handler.server.accounts.rate_limit(('audiobook-settings', uid), 12, 900)
                result = books.provider.configure(data)
        elif path == '/api/v1/audiobooks/status':
            result = books.status(uid)
        elif path == '/api/v1/audiobooks/preferences' and data is not None:
            result = books.set_preferences(uid, data)
        elif path == '/api/v1/audiobooks/library' and data is None:
            query = parse_qs(urlsplit(handler.path).query)
            try: page = int(query.get('page', ['0'])[0])
            except ValueError: raise BookError('Invalid library page.', 400) from None
            result = books.library(uid, query.get('library', [None])[0], page, query.get('q', [''])[0], query.get('filter', ['all'])[0], query.get('sort', ['title'])[0], query.get('author', [None])[0], query.get('series', [None])[0])
        elif path in ('/api/v1/audiobooks/authors', '/api/v1/audiobooks/series') and data is None:
            query = parse_qs(urlsplit(handler.path).query)
            try: page = int(query.get('page', ['0'])[0])
            except ValueError: raise BookError('Invalid library page.', 400) from None
            result = books.groups(uid, path.rsplit('/', 1)[1], query.get('library', [None])[0], page, query.get('q', [''])[0])
        elif path == '/api/v1/audiobooks/continue' and data is None:
            result = books.continue_listening(uid)
        elif path == '/api/v1/audiobooks/state' and data is None:
            result = books.public_state(uid)
        elif path == '/api/v1/audiobooks/play' and data is not None:
            handler.server.accounts.rate_limit(('audiobook-play', uid), 60, 60)
            token, kind = handler.token(); result = books.begin(uid, data, token, kind)
        elif path == '/api/v1/audiobooks/progress' and data is not None:
            handler.server.accounts.rate_limit(('audiobook-progress', uid), 90, 60)
            result = books.progress(uid, data)
        elif path == '/api/v1/audiobooks/download' and data is not None:
            handler.server.accounts.rate_limit(('audiobook-download', uid), 30, 60)
            token, kind = handler.token(); result = books.download(uid, data, token, kind)
        elif path == '/api/v1/audiobooks/offline-progress' and data is not None:
            handler.server.accounts.rate_limit(('audiobook-offline-progress', uid), 90, 60)
            result = books.offline_progress(uid, data)
        elif path == '/api/v1/audiobooks/queue' and data is not None:
            result = books.queue(uid, data)
        elif path == '/api/v1/audiobooks/bookmark' and data is not None:
            result = books.bookmark(uid, data)
        elif path.startswith('/api/v1/audiobooks/book/') and data is None:
            result = books.book(uid, identifier(path.rsplit('/', 1)[1]))
            # Server-private original file paths are used only by a fenced manifest.
            result['parts'] = [{k: v for k, v in part.items() if k != 'path'} for part in result['parts']]
        elif path.startswith('/api/v1/audiobooks/cover/') and data is None:
            book_id = identifier(path.rsplit('/', 1)[1]); books.provider.require()
            with books.provider.user(uid, 'GET', '/api/items/' + book_id + '/cover', raw=True) as response:
                content = response.read(8 * 1024 * 1024 + 1)
                mime = response.headers.get('Content-Type', '').split(';')[0]
                if len(content) > 8 * 1024 * 1024 or mime not in ('image/jpeg', 'image/png', 'image/webp'):
                    raise BookError('Book artwork is unavailable.', 404)
                handler.send(200, content, mime, {'Cache-Control': 'private, no-store'})
            return True
        else: raise BookError('Audiobook request not found.', 404)
        handler.json(result)
    except BookError as exc:
        # Provider credentials are managed privately. Their failure is never an
        # instruction to revoke an otherwise valid Arrify session.
        status = 503 if exc.status == 401 else exc.status
        error = {'error': str(exc)}
        if handler.token()[1] == 'native':
            error = {'error': {'code':'audiobook_conflict' if status==409 else 'audiobooks_unavailable', 'message':str(exc)}}
        handler.json(error, status)
    return True


def serve_stream(handler, books, lease, index, key):
    uid, state, part = books.stream(lease, index)
    headers = {}
    value = handler.headers.get('Range')
    if value:
        if not re.fullmatch(r'bytes=\d*-\d*', value) or value == 'bytes=-': raise BookError('Invalid byte range.', 416)
        headers['Range'] = value
    if handler.headers.get('If-Range'):
        value = handler.headers['If-Range']
        if not re.fullmatch(r'[\x20-\x7e]{1,300}', value): raise BookError('Invalid resume validator.', 400)
        headers['If-Range'] = value
    with books.provider.user(uid, handler.command, part['path'], headers=headers, raw=True) as response:
        if response.status == 416:
            handler.send(416, extra={'Content-Range':response.headers.get('Content-Range', 'bytes */0')}); return
        mime = response.headers.get('Content-Type', part['mime']).split(';')[0]
        if mime not in ('audio/mp4', 'audio/mpeg', 'audio/flac', 'audio/x-flac', 'audio/aac', 'audio/ogg', 'audio/wav', 'audio/x-wav', 'application/octet-stream'):
            raise BookError('This audiobook returned an unsupported audio response.', 502)
        handler.send_response(response.status)
        handler.send_header('Content-Type', mime)
        for name in ('Content-Length', 'Content-Range', 'Accept-Ranges', 'ETag'):
            if response.headers.get(name): handler.send_header(name, response.headers[name])
        handler.send_header('Cache-Control', 'private, no-store'); handler.send_header('X-Content-Type-Options', 'nosniff')
        handler.end_headers()
        if handler.command == 'HEAD': return
        handler.server.active_streams.sending(key)
        checked = time.monotonic()
        while True:
            if time.monotonic() - checked >= 1:
                try: books.stream(lease, index)
                except BookError:
                    handler.close_connection = True; return
                checked = time.monotonic()
            chunk = response.read(131072)
            if not chunk: return
            handler.wfile.write(chunk)

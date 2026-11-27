"""Book catalog, durable checkpoints and fenced long-form playback for Arrify."""
import base64
import hashlib
import json
import math
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import urllib.parse

from .audiobook_provider import AudiobookProvider, BookError, identifier
from .library import PlaybackConflict


def number(value, maximum, default=0):
    if value is None: return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= maximum:
        raise BookError('Invalid audiobook position.', 400)
    return float(value)


class Audiobooks:
    def __init__(self, accounts, listening, directory, fetch=None, now=time.time):
        self.a, self.listening, self.now = accounts, listening, now
        root = Path(directory) / 'audiobooks'; root.mkdir(mode=0o700, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(root / 'audiobooks.db', check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL'); self.db.execute('PRAGMA synchronous=FULL'); self.db.execute('PRAGMA secure_delete=ON')
        for name in ('preferences', 'state'):
            self.db.execute(f'CREATE TABLE IF NOT EXISTS {name}(user_id INTEGER PRIMARY KEY,payload TEXT NOT NULL)')
        self.db.execute('''CREATE TABLE IF NOT EXISTS checkpoints(user_id INTEGER,book_id TEXT,payload TEXT NOT NULL,
            PRIMARY KEY(user_id,book_id))''')
        self.db.execute('CREATE TABLE IF NOT EXISTS downloads(grant TEXT PRIMARY KEY,user_id INTEGER,payload TEXT NOT NULL)')
        self.db.execute('''CREATE TABLE IF NOT EXISTS offline_receipts(user_id INTEGER,session_id TEXT,payload TEXT NOT NULL,
            PRIMARY KEY(user_id,session_id))''')
        self.provider = AudiobookProvider(accounts, self.db, self.lock, root / 'secrets', fetch, now)
        self.stop = threading.Event(); self.worker = None
        self.locks = [threading.RLock() for _ in range(64)]

    def start(self):
        def work():
            failures = 0
            while not self.stop.wait(min(300, 15 * 2**min(failures, 4))):
                try:
                    if self.provider.config().get('origin'):
                        self.cleanup()
                        if self.provider.available(): self.flush_pending()
                    failures = 0
                except (BookError, OSError, ValueError): failures += 1
        self.worker = threading.Thread(target=work, daemon=True, name='audiobook-cleanup'); self.worker.start()

    def close(self):
        self.stop.set()
        if self.worker: self.worker.join(timeout=10)
        self.db.close()

    def read(self, table, uid, default=None):
        with self.lock: row = self.db.execute(f'SELECT payload FROM {table} WHERE user_id=?', (uid,)).fetchone()
        return json.loads(row[0]) if row else ({} if default is None else default)

    def write(self, table, uid, value):
        self.provider.require_account(uid)
        with self.lock:
            self.db.execute(f'INSERT INTO {table} VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET payload=excluded.payload',
                            (uid, json.dumps(value, separators=(',', ':'))))

    def preferences(self, uid):
        return {'visible': True, 'continuous': False, 'speed': 1.0, 'back_seconds': 30, 'forward_seconds': 30,
                **self.read('preferences', uid)}

    def set_preferences(self, uid, data):
        with self.locks[uid % 64]:
            value = self.preferences(uid)
            for key in ('visible', 'continuous'):
                if key in data:
                    if type(data[key]) is not bool: raise BookError('Invalid audiobook setting.', 400)
                    value[key] = data[key]
            if 'speed' in data:
                speed = number(data['speed'], 3)
                if speed < .5: raise BookError('Choose a speed between 0.5× and 3×.', 400)
                value['speed'] = speed
            for key in ('back_seconds', 'forward_seconds'):
                if key in data:
                    if data[key] not in (10, 15, 20, 30, 60, 90): raise BookError('Choose a supported skip interval.', 400)
                    value[key] = data[key]
            self.write('preferences', uid, value)
            return value

    def status(self, uid):
        return {'enabled': self.provider.available(), 'preferences': self.preferences(uid)}

    def metadata(self, item, detail=False):
        if item.get('mediaType') != 'book' or item.get('libraryId') not in self.provider.config().get('libraries', []):
            raise BookError('This audiobook is not available in your library.', 404)
        media = item.get('media', {}); meta = media.get('metadata', {})
        duration = number(media.get('duration'), 86400 * 31)
        book = {'id': identifier(item['id']), 'library_id': identifier(item['libraryId']),
                'title': str(meta.get('title') or 'Untitled audiobook')[:500],
                'author': str(meta.get('authorName') or ', '.join(x.get('name', '') for x in meta.get('authors', [])))[:500],
                'narrator': str(meta.get('narratorName') or ', '.join(meta.get('narrators', [])))[:500],
                'series': str(meta.get('seriesName') or ', '.join(x.get('name', '') for x in meta.get('series', [])))[:500],
                'authors': [{'id': identifier(x['id']), 'name': str(x.get('name', ''))[:500]} for x in meta.get('authors', [])[:50] if x.get('id')],
                'series_entries': [{'id': identifier(x['id']), 'name': str(x.get('name', ''))[:500], 'sequence': str(x['sequence'])[:80] if x.get('sequence') is not None else None} for x in meta.get('series', [])[:50] if x.get('id')],
                'published_date': str(meta.get('publishedDate') or '')[:40] or None,
                'published_year': str(meta.get('publishedYear') or '')[:8] or None,
                'added_at': item.get('addedAt') if isinstance(item.get('addedAt'), (int, float)) else None,
                'duration': duration, 'cover': '/api/v1/audiobooks/cover/' + identifier(item['id']),
                'description': str(meta.get('description') or '')[:12000], 'missing': bool(item.get('isMissing') or item.get('isInvalid'))}
        if detail:
            tracks = []
            for track in media.get('tracks', [])[:1000]:
                start = number(track.get('startOffset'), duration)
                length = number(track.get('duration'), duration)
                url = track.get('contentUrl', '')
                parsed = urllib.parse.urlsplit(url)
                # Only the authenticated item's file endpoint is a valid proxy target.
                path = urllib.parse.unquote(parsed.path)
                if (parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or '\\' in path
                        or not path.startswith('/api/items/' + book['id'] + '/file/') or '..' in path.split('/')):
                    raise BookError('This audiobook returned an unsupported media resource.')
                size = track.get('metadata', {}).get('size', track.get('size', 0))
                tracks.append({'index': len(tracks), 'start': start, 'duration': length, 'path': url,
                               'size_bytes': int(number(size, 1024**4)),
                               'mime': str(track.get('mimeType') or 'audio/mp4')[:100]})
            if not tracks or duration <= 0: raise BookError('This audiobook is not ready to play.', 422)
            book['parts'] = tracks
            book['source_revision'] = hashlib.sha256(json.dumps([item.get('updatedAt'),
                [(x.get('updatedAt'), x.get('contentUrl'), x.get('metadata', {}).get('size'), x.get('metadata', {}).get('mtimeMs'))
                 for x in media.get('tracks', [])]], sort_keys=True).encode()).hexdigest()
            chapters = []
            for chapter in media.get('chapters', [])[:5000]:
                # Embedded chapter clocks can extend past the decoded audio
                # duration (for example MP3 encoder padding). They are optional
                # navigation metadata, not a reason to reject playable audio.
                if not isinstance(chapter, dict): continue
                start, end = chapter.get('start'), chapter.get('end')
                if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
                       for x in (start, end)): continue
                start, end = max(0, min(duration, start)), max(0, min(duration, end))
                if end > start:
                    chapters.append({'title': str(chapter.get('title') or f'Chapter {len(chapters)+1}')[:500], 'start': start, 'end': end})
            book['chapters'] = sorted(chapters, key=lambda x: x['start']) or [
                {'title': f'Part {x["index"]+1}', 'start': x['start'], 'end': min(duration, x['start'] + x['duration'])} for x in tracks]
        return book

    def remote_progress(self, uid, book_id):
        try: return self.provider.user(uid, 'GET', '/api/me/progress/' + identifier(book_id))
        except BookError as exc:
            if exc.status == 404: return {'currentTime': 0, 'lastUpdate': 0, 'isFinished': False}
            raise

    def checkpoint(self, uid, book_id):
        with self.lock: row = self.db.execute('SELECT payload FROM checkpoints WHERE user_id=? AND book_id=?', (uid, book_id)).fetchone()
        return json.loads(row[0]) if row else {}

    def save_checkpoint(self, uid, book_id, value):
        self.provider.require_account(uid)
        with self.lock:
            self.db.execute('INSERT INTO checkpoints VALUES(?,?,?) ON CONFLICT(user_id,book_id) DO UPDATE SET payload=excluded.payload',
                            (uid, book_id, json.dumps(value)))

    def book(self, uid, book_id):
        item = self.provider.user(uid, 'GET', '/api/items/' + identifier(book_id) + '?expanded=1')
        value = self.metadata(item, True)
        value['progress'] = self.remote_progress(uid, book_id)
        value['pending'] = bool(self.checkpoint(uid, book_id).get('dirty'))
        value['bookmarks'] = self.provider.user(uid, 'GET', '/api/me/bookmarks/' + book_id).get('bookmarks', [])
        return value

    def library(self, uid, library_id=None, page=0, query='', progress_filter='all', sort='title', author=None, series=None):
        self.provider.require()
        libraries = self.provider.user(uid, 'GET', '/api/libraries').get('libraries', [])
        allowed = [{'id': identifier(x['id']), 'name': str(x.get('name') or 'Audiobooks')[:120]} for x in libraries
                   if x.get('mediaType') == 'book' and x['id'] in self.provider.config()['libraries']]
        if not allowed: return {'libraries': [], 'items': [], 'total': 0, 'next_page': None}
        selected = library_id or allowed[0]['id']
        if selected not in {x['id'] for x in allowed}: raise BookError('This library is not available to your account.', 403)
        if type(page) is not int or not 0 <= page <= 10000: raise BookError('Invalid library page.', 400)
        if progress_filter not in ('all', 'started', 'finished', 'unstarted'): raise BookError('Invalid audiobook filter.', 400)
        sorts = {'title': ('media.metadata.title', 0), 'author': ('media.metadata.authorName', 0),
                 'release': ('media.metadata.publishedYear', 1), 'added': ('addedAt', 1),
                 'duration': ('media.duration', 0), 'sequence': ('sequence', 0)}
        if sort not in sorts: raise BookError('Invalid audiobook sort.', 400)
        if author and series: raise BookError('Choose an author or series scope.', 400)
        if author: author = identifier(author)
        if series: series = identifier(series)
        if (author or series) and progress_filter != 'all': raise BookError('Progress filters are unavailable in this scope.', 400)
        if sort == 'sequence' and not series: raise BookError('Choose a series before sorting by book number.', 400)
        query = str(query).strip()[:200]
        if query and (author or series): raise BookError('Search within a library before choosing an author or series.', 400)
        if query and sort != 'title': raise BookError('Search results use relevance order.', 400)
        if query:
            result = self.provider.user(uid, 'GET', '/api/libraries/' + selected + '/search?' + urllib.parse.urlencode({'q': query, 'limit': 100}))
            rows = [x.get('libraryItem', x) for x in result.get('book', [])]
            total = len(rows); next_page = None
        else:
            params = {'limit': 48, 'page': page, 'sort': sorts[sort][0], 'desc': sorts[sort][1], 'minified': 0 if author or series else 1}
            if author or series:
                params['filter'] = ('authors.' if author else 'series.') + base64.b64encode((author or series).encode()).decode()
            elif progress_filter != 'all':
                params['filter'] = 'progress.' + base64.b64encode({'started':'in-progress', 'finished':'finished', 'unstarted':'not-started'}[progress_filter].encode()).decode()
            result = self.provider.user(uid, 'GET', '/api/libraries/' + selected + '/items?' + urllib.parse.urlencode(
                params))
            rows = result.get('results', []); total = int(result.get('total', 0)); next_page = page + 1 if (page + 1) * 48 < total else None
        progress = self.provider.user(uid, 'GET', '/api/me/progress').get('mediaProgress', [])
        progress = {x.get('libraryItemId'): x for x in progress}
        books = []
        for row in rows:
            book = self.metadata(row); book['progress'] = progress.get(book['id'], {'currentTime': 0, 'isFinished': False})
            if query and not self.matches_progress(book['progress'], progress_filter): continue
            books.append(book)
        return {'libraries': allowed, 'library_id': selected, 'items': books, 'total': total, 'next_page': next_page,
                'preferences': self.preferences(uid)}

    def groups(self, uid, kind, library_id=None, page=0, query='', author=None):
        if kind not in ('authors', 'series'): raise BookError('Invalid audiobook grouping.', 400)
        self.provider.require()
        libraries = self.provider.user(uid, 'GET', '/api/libraries').get('libraries', [])
        allowed = [x['id'] for x in libraries if x.get('mediaType') == 'book' and x['id'] in self.provider.config()['libraries']]
        selected = library_id or (allowed[0] if allowed else None)
        if selected not in allowed: raise BookError('This library is not available to your account.', 403)
        if type(page) is not int or not 0 <= page <= 10000: raise BookError('Invalid library page.', 400)
        # Filter data contains names and IDs, not audio tracks or full book objects.
        # The provider transport bounds the response at 8 MiB.
        if author:
            if kind != 'series': raise BookError('Choose an author to browse series.', 400)
            author = identifier(author)
            result = self.provider.user(uid, 'GET', '/api/authors/' + author + '?' + urllib.parse.urlencode({'include':'items,series', 'library':selected}))
            rows = [x for x in result.get('series', []) if any(item.get('libraryId') == selected for item in x.get('items', []))]
        else:
            result = self.provider.user(uid, 'GET', '/api/libraries/' + selected + '/filterdata')
            rows = result.get(kind, [])
        query = str(query).strip().casefold()[:200]
        values = [{'id': identifier(x['id']), 'name': str(x.get('name', ''))[:500]} for x in rows
                  if x.get('id') and query in str(x.get('name', '')).casefold()]
        values.sort(key=lambda x: (x['name'].casefold(), x['id']))
        return {'items': values[page*48:(page+1)*48], 'total': len(values), 'library_id': selected,
                'next_page': page+1 if (page+1)*48 < len(values) else None}

    @staticmethod
    def matches_progress(progress, value):
        if value == 'unstarted': return not progress.get('isFinished') and not progress.get('currentTime', 0)
        return value == 'all' or (bool(progress.get('isFinished')) if value == 'finished' else
                                  progress.get('currentTime', 0) > 0 and not progress.get('isFinished'))

    def continue_listening(self, uid):
        self.provider.require()
        result = self.provider.user(uid, 'GET', '/api/me/items-in-progress?limit=12')
        rows = result.get('libraryItems', [])
        progress = self.provider.user(uid, 'GET', '/api/me/progress').get('mediaProgress', [])
        positions = {x.get('libraryItemId'): x for x in progress}
        books = []
        for row in rows[:12]:
            if row.get('mediaType') != 'book' or row.get('libraryId') not in self.provider.config()['libraries']: continue
            value = self.metadata(row); value['progress'] = positions.get(value['id'], {})
            if self.matches_progress(value['progress'], 'started'): books.append(value)
        return {'items': books}

    def anchor(self, token, kind):
        digest = self.a.digest(token)
        if kind == 'native':
            with self.a.lock:
                row = self.a.db.execute('SELECT f.anchor FROM native_access n JOIN native_families f ON f.id=n.family WHERE n.digest=?', (digest,)).fetchone()
            if not row: raise BookError('Sign in again.', 401)
            return row[0]
        return digest

    def valid(self, uid, state):
        if not state or state.get('expires', 0) <= self.now() or not self.provider.available(): return False
        if state.get('library_id') not in self.provider.config()['libraries']: return False
        with self.a.lock:
            account = self.a.db.execute('''SELECT 1 FROM sessions s JOIN users u ON u.id=s.user_id
                WHERE s.digest=? AND s.user_id=? AND s.expires>? AND u.approved=1 AND u.frozen=0 AND u.deleted_at=0''',
                (state.get('anchor'), uid, self.now())).fetchone()
            music = self.listening.session(self.listening.p.load(uid))
            return bool(account and (state.get('kind') == 'download' or
                        (music['owner'] == state['device_id'] and music['epoch'] == state['music_epoch']
                         and music['intent'] == state['music_intent'])))

    def public_state(self, uid):
        state = self.read('state', uid)
        return {key: state.get(key) for key in ('book_id', 'title', 'device_id', 'lease', 'revision', 'position', 'playing', 'queue')} | {
            'active': self.valid(uid, state), 'preferences': self.preferences(uid),
            'author': state.get('book', {}).get('author', ''), 'duration': state.get('book', {}).get('duration', 0),
            'cover': '/api/v1/audiobooks/cover/' + state['book_id'] if state.get('book_id') else None}

    def begin(self, uid, data, token, kind):
        with self.locks[uid % 64]:
            book_id = identifier(data.get('book_id'))
            device = self.listening.device(data)
            previous = self.read('state', uid)
            request_id = identifier(data.get('request_id'))
            if previous.get('request_id') == request_id and self.valid(uid, previous): return self.manifest(previous, uid)
            if data.get('automatic') and (data.get('previous_lease') != previous.get('lease') or not self.valid(uid, previous)):
                raise BookError('Playback moved to another device. Choose Listen here to continue.', 409)
            # Flush an older checkpoint before choosing the authoritative resume cursor.
            checkpoint = self.checkpoint(uid, book_id)
            if checkpoint.get('dirty'):
                if data.get('use_remote') is True:
                    checkpoint['dirty'] = False; self.save_checkpoint(uid, book_id, checkpoint)
                else: self.flush(uid, book_id)
            book = self.book(uid, book_id)
            progress = book['progress']; position = number(progress.get('currentTime'), book['duration'])
            if data.get('restart') is True or progress.get('isFinished'): position = 0
            revision = data.get('music_revision')
            if type(revision) is not int: raise BookError('Refresh playback before starting this book.', 409)
            try: music = self.listening.claim_external(uid, device, str(data.get('device_name', 'Arrify'))[:60], revision)
            except PlaybackConflict as exc: raise BookError(str(exc), 409) from None
            state = {'book_id': book_id, 'library_id': book['library_id'], 'book': book, 'title': book['title'], 'device_id': device,
                     'lease': secrets.token_urlsafe(32), 'revision': previous.get('revision', 0) + 1,
                     'music_epoch': music['epoch'], 'music_intent': music['intent'], 'anchor': self.anchor(token, kind),
                     'expires': self.now() + 3600, 'request_id': request_id, 'position': position, 'playing': True,
                     'queue': [x for x in previous.get('queue', []) if x['id'] != book_id], 'seq': 0, 'reported_at': self.now()}
            self.write('state', uid, state)
            # Completed checkpoints are an idempotency receipt for the current
            # session only. Audiobookshelf owns the listening history.
            with self.lock:
                self.db.execute("DELETE FROM checkpoints WHERE user_id=? AND book_id!=? AND json_extract(payload,'$.dirty')=0", (uid, book_id))
            self.save_checkpoint(uid, book_id, {'lease': state['lease'], 'seq': 0, 'position': position,
                'duration': book['duration'], 'remote_stamp': progress.get('lastUpdate', 0), 'dirty': False})
            return self.manifest(state, uid)

    def manifest(self, state, uid):
        book = {k: v for k, v in state['book'].items() if k != 'parts'}
        book['parts'] = [{'index': x['index'], 'start': x['start'], 'duration': x['duration'],
                         'size_bytes': x.get('size_bytes', 0), 'mime': x.get('mime', 'audio/mp4'),
                         'url': f'/api/v1/audiobooks/stream/{state["lease"]}/{x["index"]}'} for x in state['book']['parts']]
        return {'book': book, 'position': state['position'], 'lease': state['lease'], 'revision': state['revision'],
                'queue': state.get('queue', []), 'preferences': self.preferences(uid),
                'music_epoch': state.get('music_epoch'), 'music_intent': state.get('music_intent')}

    def download(self, uid, data, token, kind):
        """Explicit offline preparation never claims or interrupts music output."""
        with self.locks[uid % 64]:
            book = self.book(uid, identifier(data.get('book_id')))
            anchor = self.anchor(token, kind)
            # Keep an unchanged URL stable for URLSession's opaque resume data.
            # Explicit authenticated resume renews the grant; possession alone does not.
            with self.lock:
                row = self.db.execute("SELECT payload FROM downloads WHERE user_id=? AND json_extract(payload,'$.book_id')=? AND json_extract(payload,'$.anchor')=?", (uid, book['id'], anchor)).fetchone()
            if row:
                previous = json.loads(row[0])
                if previous['book']['source_revision'] == book['source_revision']:
                    previous.update(expires=self.now()+3600, book=book,
                                    position=number(book['progress'].get('currentTime'), book['duration']))
                    with self.lock: self.db.execute('UPDATE downloads SET payload=? WHERE grant=?', (json.dumps(previous), previous['lease']))
                    return self.manifest(previous, uid)
            state = {'kind':'download', 'book_id':book['id'], 'library_id':book['library_id'], 'book':book,
                     'lease':secrets.token_urlsafe(32), 'anchor':anchor, 'expires':self.now()+3600,
                     'position':number(book['progress'].get('currentTime'), book['duration']), 'revision':0}
            with self.lock:
                self.db.execute("DELETE FROM downloads WHERE user_id=? AND (json_extract(payload,'$.expires')<=? OR json_extract(payload,'$.book_id')=?)", (uid, self.now(), book['id']))
                count = self.db.execute('SELECT count(*) FROM downloads WHERE user_id=?', (uid,)).fetchone()[0]
                if count >= 4: raise BookError('Finish or cancel another book download first.', 409)
                self.db.execute('INSERT INTO downloads VALUES(?,?,?)', (state['lease'], uid, json.dumps(state)))
            return self.manifest(state, uid)

    def progress(self, uid, data):
        with self.locks[uid % 64]:
            state = self.read('state', uid)
            if not state or data.get('lease') != state.get('lease') or data.get('device_id') != state.get('device_id'):
                raise BookError('Listening moved to another session. Your earlier place is still saved on this device.', 409)
            if not self.valid(uid, state): raise BookError('Playback moved or this listening session ended. Reopen the book to continue.', 409)
            seq = data.get('seq')
            if type(seq) is not int or not 1 <= seq <= 2**53: raise BookError('Invalid progress sequence.', 400)
            checkpoint = self.checkpoint(uid, state['book_id'])
            if seq <= checkpoint.get('seq', 0): return {'saved': True, 'pending': checkpoint.get('dirty', False), 'remote_stamp':checkpoint.get('remote_stamp', 0)}
            position = number(data.get('position'), state['book']['duration'])
            state.update(position=position, playing=bool(data.get('playing')), seq=seq, expires=self.now()+3600, reported_at=self.now())
            checkpoint.update(seq=seq, position=position, dirty=True,
                              finished=bool(data.get('finished')) and position >= state['book']['duration'] - 2)
            # FULL-synchronous SQLite publication precedes any provider mutation.
            self.save_checkpoint(uid, state['book_id'], checkpoint); self.write('state', uid, state)
            try: self.flush(uid, state['book_id'])
            except BookError as exc:
                if exc.status in (401, 403, 409): raise
            active = self.valid(uid, state)
            saved = self.checkpoint(uid, state['book_id'])
            return {'saved': True, 'pending': saved.get('dirty', False), 'active': active, 'remote_stamp': saved.get('remote_stamp', 0)}

    def flush(self, uid, book_id):
        checkpoint = self.checkpoint(uid, book_id)
        if not checkpoint.get('dirty'): return
        remote = self.remote_progress(uid, book_id)
        same = abs(float(remote.get('currentTime', 0)) - checkpoint['position']) < .5 and bool(remote.get('isFinished')) == bool(checkpoint.get('finished'))
        if remote.get('lastUpdate', 0) != checkpoint.get('remote_stamp', 0) and not same:
            checkpoint['conflict'] = True; self.save_checkpoint(uid, book_id, checkpoint)
            raise BookError('Your listening position changed on another device. Reopen the book to use its saved position.', 409)
        if not same:
            self.provider.user(uid, 'PATCH', '/api/me/progress/' + book_id,
                               {'currentTime': checkpoint['position'], 'duration': checkpoint['duration'],
                                'progress': checkpoint['position']/checkpoint['duration'], 'isFinished': bool(checkpoint.get('finished'))})
            remote = self.remote_progress(uid, book_id)
        checkpoint.update(dirty=False, conflict=False, remote_stamp=remote.get('lastUpdate', 0))
        self.save_checkpoint(uid, book_id, checkpoint)

    def offline_progress(self, uid, data):
        """Reconcile downloaded listening without claiming another device's output.

        A client keeps the original canonical timestamp with its durable journal.
        Rewinds are intentional; timestamps, rather than maximum position, fence
        conflicts. The receipt precedes the provider write and survives a lost reply.
        """
        with self.locks[uid % 64]:
            self.provider.require_account(uid)
            session = identifier(data.get('session_id')); book_id = identifier(data.get('book_id'))
            device = self.listening.device(data); seq = data.get('seq')
            if type(seq) is not int or not 1 <= seq <= 2**53: raise BookError('Invalid progress sequence.', 400)
            with self.lock:
                row = self.db.execute('SELECT payload FROM offline_receipts WHERE user_id=? AND session_id=?', (uid, session)).fetchone()
            receipt = json.loads(row[0]) if row else None
            if receipt and (receipt['book_id'] != book_id or receipt['device_id'] != device):
                raise BookError('This offline listening record belongs to another book or device.', 409)
            if receipt and receipt.get('dirty'): self.flush_offline(uid, session, receipt)
            if not receipt or seq > receipt['seq']:
                book = self.metadata(self.provider.user(uid, 'GET', '/api/items/' + book_id + '?expanded=1'), True)
                if data.get('source_revision') != book['source_revision']:
                    raise BookError('This audiobook changed. Your offline place is saved; download the current edition before syncing it.', 409)
                position = number(data.get('position'), book['duration'])
                stamp = receipt['remote_stamp'] if receipt else number(data.get('base_remote_stamp'), 2**53)
                receipt = {'book_id':book_id, 'device_id':device, 'seq':seq, 'position':position,
                           'duration':book['duration'], 'remote_stamp':stamp, 'dirty':True,
                           'finished':data.get('finished') is True and position >= book['duration']-2,
                           'updated':self.now()}
                self.save_offline(uid, session, receipt)
                self.flush_offline(uid, session, receipt)
            return {'saved':True, 'pending':receipt['dirty'], 'remote_stamp':receipt['remote_stamp']}

    def save_offline(self, uid, session, receipt):
        self.provider.require_account(uid)
        with self.lock:
            if not self.db.execute('SELECT 1 FROM offline_receipts WHERE user_id=? AND session_id=?', (uid, session)).fetchone():
                # Bound receipts without discarding unresolved listening records.
                self.db.execute("DELETE FROM offline_receipts WHERE user_id=? AND session_id IN (SELECT session_id FROM offline_receipts WHERE user_id=? AND json_extract(payload,'$.dirty')=0 ORDER BY json_extract(payload,'$.updated') DESC LIMIT -1 OFFSET 63)", (uid, uid))
                count = self.db.execute('SELECT count(*) FROM offline_receipts WHERE user_id=?', (uid,)).fetchone()[0]
                if count >= 128: raise BookError('Reconnect to save your earlier offline listening first.', 409)
            self.db.execute('INSERT INTO offline_receipts VALUES(?,?,?) ON CONFLICT(user_id,session_id) DO UPDATE SET payload=excluded.payload', (uid, session, json.dumps(receipt)))

    def flush_offline(self, uid, session, receipt):
        remote = self.remote_progress(uid, receipt['book_id'])
        same = abs(float(remote.get('currentTime', 0))-receipt['position']) < .5 and bool(remote.get('isFinished')) == receipt['finished']
        if remote.get('lastUpdate', 0) != receipt['remote_stamp'] and not same:
            raise BookError('Your place changed on another device. Choose which listening position to keep.', 409)
        if not same:
            self.provider.user(uid, 'PATCH', '/api/me/progress/' + receipt['book_id'],
                               {'currentTime':receipt['position'], 'duration':receipt['duration'],
                                'progress':receipt['position']/receipt['duration'], 'isFinished':receipt['finished']})
            remote = self.remote_progress(uid, receipt['book_id'])
        receipt.update(dirty=False, remote_stamp=remote.get('lastUpdate', 0))
        self.save_offline(uid, session, receipt)

    def queue(self, uid, data):
        with self.locks[uid % 64]:
            state = self.read('state', uid)
            if not state: state = {'queue': [], 'revision': 0}
            if data.get('revision') != state.get('revision'): raise BookError('Your book queue changed. Refresh it and try again.', 409)
            action = data.get('action')
            if action in ('next', 'last'):
                book = self.book(uid, identifier(data.get('book_id')))
                queue = [x for x in state['queue'] if x['id'] != book['id']]
                entry = {k: book[k] for k in ('id', 'title', 'author', 'cover')}
                if action == 'next': queue.insert(0, entry)
                else: queue.append(entry)
                if len(queue) > 100: raise BookError('Your audiobook queue is full.', 400)
                state['queue'] = queue
            elif action == 'order':
                ids = data.get('ids')
                if not isinstance(ids, list) or len(ids) != len(set(ids)) or not set(ids) <= {x['id'] for x in state['queue']}:
                    raise BookError('Invalid audiobook queue.', 400)
                old = {x['id']: x for x in state['queue']}; state['queue'] = [old[x] for x in ids]
            else: raise BookError('Invalid audiobook queue action.', 400)
            state['revision'] += 1; self.write('state', uid, state)
            return {'queue': state['queue'], 'revision': state['revision']}

    def bookmark(self, uid, data):
        book = self.book(uid, identifier(data.get('book_id')))
        position = int(number(data.get('position'), book['duration']))
        title = str(data.get('title') or 'Bookmark')[:200]
        if data.get('action') == 'remove':
            self.provider.user(uid, 'DELETE', '/api/me/item/' + book['id'] + '/bookmark/' + str(position))
        else:
            method = 'PATCH' if any(int(x.get('time', -1)) == position for x in book['bookmarks']) else 'POST'
            self.provider.user(uid, method, '/api/me/item/' + book['id'] + '/bookmark', {'time': position, 'title': title})
        return {'saved': True}

    def stream(self, lease, index):
        if not isinstance(lease, str) or len(lease) != 43: raise BookError('This audiobook link has expired.', 403)
        with self.lock:
            row = self.db.execute('SELECT user_id,payload FROM state WHERE json_extract(payload,\'$.lease\')=?', (lease,)).fetchone()
            if not row: row = self.db.execute('SELECT user_id,payload FROM downloads WHERE grant=?', (lease,)).fetchone()
        if not row: raise BookError('This audiobook link has expired.', 403)
        uid, state = row['user_id'], json.loads(row['payload'])
        if not self.valid(uid, state): raise BookError('Listening moved or this session expired.', 403)
        if not 0 <= index < len(state['book']['parts']): raise BookError('Invalid audiobook part.', 404)
        return uid, state, state['book']['parts'][index]

    def cleanup(self):
        self.provider.cleanup_deleted()
        with self.lock:
            users = self.db.execute('SELECT user_id FROM preferences UNION SELECT user_id FROM state UNION SELECT user_id FROM checkpoints UNION SELECT user_id FROM downloads UNION SELECT user_id FROM offline_receipts').fetchall()
        for row in users:
            with self.a.lock: account = self.a.db.execute('SELECT deleted_at FROM users WHERE id=?', (row[0],)).fetchone()
            if account and not account['deleted_at']: continue
            with self.lock:
                for table in ('preferences', 'state', 'checkpoints', 'downloads', 'offline_receipts'): self.db.execute(f'DELETE FROM {table} WHERE user_id=?', (row[0],))

    def flush_pending(self):
        with self.lock:
            rows = self.db.execute("SELECT user_id,book_id FROM checkpoints WHERE json_extract(payload,'$.dirty')=1 AND coalesce(json_extract(payload,'$.conflict'),0)=0 LIMIT 20").fetchall()
        for row in rows:
            if self.stop.is_set(): return
            uid = row['user_id']
            with self.a.lock:
                user = self.a.db.execute('SELECT approved,frozen,deleted_at FROM users WHERE id=?', (uid,)).fetchone()
            if not user or not user['approved'] or user['frozen'] or user['deleted_at']: continue
            with self.locks[uid % 64]:
                try: self.flush(uid, row['book_id'])
                except BookError as exc:
                    if exc.status == 409: continue  # A user must resolve a conflicting position.
                    raise

    def active_count(self):
        with self.lock:
            rows = self.db.execute("SELECT user_id,payload FROM state WHERE json_extract(payload,'$.playing')=1 AND json_extract(payload,'$.reported_at')>?", (self.now()-60,)).fetchall()
        return sum(self.valid(row['user_id'], json.loads(row['payload'])) for row in rows)

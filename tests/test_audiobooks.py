import base64
from concurrent.futures import ThreadPoolExecutor
import http.client
from http.server import ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import urlsplit, parse_qs
from unittest.mock import patch

from app.accounts import Accounts
from app.audiobooks import Audiobooks
from app.audiobook_provider import BookError
from app.connections import ActiveStreams
from app.library import Player
from app.listening import Listening
from app.native_playback import snapshot
from app.server import Handler
from test_parties import Library


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data); self.status = status; self.headers = headers or {}


class ShelfFixture:
    def __init__(self):
        self.users = {'admin': {'id': 'admin', 'username': 'admin', 'password': 'private-admin-password', 'type': 'root', 'isActive': True}}
        self.access = {}; self.refresh = {}; self.progress = {}; self.bookmarks = {}; self.counter = 0
        self.created = 0; self.offline = False; self.lose_create = False; self.lose_progress = False; self.lose_refresh = False
        self.duration = 120; self.paths = []; self.refresh_uses = []
        self.audio = base64.b64decode((Path(__file__).resolve().parents[1] / 'tools/media-probe/silence.mp3.base64').read_text())

    def item(self, book_id='book-one'):
        return {'id': book_id, 'libraryId': 'books', 'mediaType': 'book', 'media': {'duration': self.duration,
            'metadata': {'title': 'A Good Story' if book_id == 'book-one' else 'Another Story', 'authorName': 'A Writer',
                         'narrators': ['A Reader'], 'description': '<p>A story worth listening to.</p>'},
            'tracks': [{'startOffset': 0, 'duration': self.duration, 'mimeType': 'audio/mpeg',
                        'contentUrl': '/api/items/' + book_id + '/file/123'}],
            'chapters': [{'title': 'Beginning', 'start': 0, 'end': self.duration/2},
                         {'title': 'Next chapter', 'start': self.duration/2, 'end': self.duration}]}}

    def fetch(self, method, path, data, headers, raw):
        self.paths.append((method, path))
        if self.offline: raise BookError('Fixture connection unavailable.')
        if path == '/login':
            user = self.users.get(data['username'])
            if not user or user['password'] != data['password'] or not user['isActive']: raise BookError('Sign-in failed.', 401)
            self.counter += 1; access = 'access-' + str(self.counter); refresh = 'refresh-' + str(self.counter)
            self.access[access] = user; self.refresh[refresh] = user
            return {'user': {'id': user['id'], 'type': user['type'], 'accessToken': access, 'refreshToken': refresh}}
        if path == '/auth/refresh':
            old = headers['x-refresh-token']; self.refresh_uses.append(old)
            user = self.refresh.pop(old, None)
            if not user: raise BookError('Invalid refresh.', 401)
            self.counter += 1; access = 'access-' + str(self.counter); refresh = 'refresh-' + str(self.counter)
            self.access[access] = user; self.refresh[refresh] = user
            if self.lose_refresh: self.lose_refresh = False; raise BookError('Lost refresh response.')
            return {'user': {'accessToken': access, 'refreshToken': refresh}}
        user = self.access.get(headers.get('Authorization', '').removeprefix('Bearer '))
        if not user: raise BookError('Expired access.', 401)
        if path == '/api/users' and method == 'GET':
            if user['type'] != 'root': raise BookError('Not allowed.', 403)
            return {'users': [{'id': x['id'], 'username': x['username']} for x in self.users.values()]}
        if path == '/api/users' and method == 'POST':
            if user['type'] != 'root': raise BookError('Not allowed.', 403)
            if data['username'] in self.users: raise BookError('Already exists.', 400)
            self.created += 1; created = {**data, 'id': 'managed-' + str(self.created)}; self.users[created['username']] = created
            if self.lose_create: self.lose_create = False; raise BookError('Lost creation response.')
            return {'user': {'id': created['id']}}
        if path.startswith('/api/users/'):
            if user['type'] != 'root': raise BookError('Not allowed.', 403)
            target = next((x for x in self.users.values() if x['id'] == path.rsplit('/', 1)[1]), None)
            if not target: raise BookError('Missing user.', 404)
            if method == 'DELETE': del self.users[target['username']]
            elif method == 'PATCH': target.update(data)
            return {}
        if path == '/api/libraries': return {'libraries': [{'id': 'books', 'name': 'Our Books', 'mediaType': 'book'}]}
        if path.startswith('/api/libraries/books/items'):
            rows = [self.item(), self.item('book-two')]
            encoded = parse_qs(urlsplit(path).query).get('filter', [''])[0]
            if encoded:
                value = base64.b64decode(encoded.split('.', 1)[1]).decode()
                rows = [x for x in rows if Audiobooks.matches_progress(self.progress.get((user['id'], x['id']), {}), 'started' if value == 'in-progress' else 'finished')]
            return {'results': rows, 'total': len(rows)}
        if path.startswith('/api/libraries/books/search'): return {'book': [{'libraryItem': self.item()}]}
        if path == '/api/me/progress': return {'mediaProgress': [v for (u, _), v in self.progress.items() if u == user['id']]}
        if path.startswith('/api/me/items-in-progress'):
            return {'libraryItems': [self.item(b) for (u, b), v in self.progress.items() if u == user['id'] and Audiobooks.matches_progress(v, 'started')]}
        if path.startswith('/api/me/progress/'):
            key = user['id'], path.rsplit('/', 1)[1]
            if method == 'GET':
                if key not in self.progress: raise BookError('Not started.', 404)
                return dict(self.progress[key])
            self.counter += 1; self.progress[key] = {**data, 'libraryItemId': key[1], 'lastUpdate': self.counter}
            if self.lose_progress: self.lose_progress = False; raise BookError('Lost progress acknowledgement.')
            return {}
        if path.startswith('/api/me/bookmarks/'):
            return {'bookmarks': self.bookmarks.get((user['id'], path.rsplit('/', 1)[1]), [])}
        if '/bookmark' in path:
            key = user['id'], path.split('/')[4]
            if method == 'POST': self.bookmarks.setdefault(key, []).append(data)
            else: self.bookmarks[key] = [x for x in self.bookmarks.get(key, []) if x['time'] != int(path.rsplit('/', 1)[1])]
            return {}
        if path.startswith('/api/items/'):
            book_id = urlsplit(path).path.split('/')[3]
            if book_id not in ('book-one', 'book-two'): raise BookError('No access.', 403)
            if '/file/' in path:
                body = self.audio; status = 200; response_headers = {'Content-Type': 'audio/mpeg', 'Accept-Ranges': 'bytes'}
                if headers.get('Range'):
                    left, right = headers['Range'][6:].split('-'); start = int(left or 0); end = min(len(body)-1, int(right) if right else len(body)-1)
                    response_headers['Content-Range'] = f'bytes {start}-{end}/{len(body)}'; body = body[start:end+1]; status = 206
                response_headers['Content-Length'] = str(len(body))
                return Response(b'' if method == 'HEAD' else body, status, response_headers)
            return self.item(book_id)
        raise AssertionError((method, path))


class AudiobookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.a = Accounts(self.tmp.name)
        for name in ('readerone', 'readertwo'):
            self.a.register(name, 'a-strong-password', email=name+'@example.invalid'); self.a.approve(name)
        self.player = Player(self.a, Library()); self.listening = Listening(self.player)
        self.fixture = ShelfFixture(); self.books = Audiobooks(self.a, self.listening, self.tmp.name, self.fixture.fetch)
        self.books.provider.configure({'origin': 'http://books.internal:13378', 'username': 'admin', 'password': 'private-admin-password', 'enabled': True})
        self.token = self.a.login('readerone', 'a-strong-password'); self.device = 'a'*32

    def test_library_order_and_scope_are_forwarded_before_pagination(self):
        self.books.library(1, page=2, sort='release')
        path = next(p for _, p in reversed(self.fixture.paths) if '/items?' in p)
        query = parse_qs(urlsplit(path).query)
        self.assertEqual(query['page'], ['2']); self.assertEqual(query['limit'], ['48'])
        self.assertEqual(query['sort'], ['media.metadata.publishedYear']); self.assertEqual(query['desc'], ['1'])
        with patch.object(self.books.provider, 'user', return_value={'results': [], 'total': 0}) as user:
            # Allowed library lookup is required even for scoped book reads.
            user.side_effect = [{'libraries':[{'id':'books','mediaType':'book'}]}, {'results':[], 'total':0}, {'mediaProgress':[]}]
            self.books.library(1, series='series-one', sort='sequence')
            path = user.call_args_list[1].args[2]
            query = parse_qs(urlsplit(path).query)
            self.assertEqual(query['sort'], ['sequence'])
            self.assertEqual(base64.b64decode(query['filter'][0].split('.',1)[1]).decode(), 'series-one')

    def test_invalid_library_scope_and_sort_do_not_read_items(self):
        for values in ({'sort':'private.path'}, {'sort':'release_date'}, {'sort':'sequence'}, {'library_id':'other'}, {'author':'../other'}, {'author':'one','series':'two'}):
            before = len(self.fixture.paths)
            with self.assertRaises(BookError): self.books.library(1, **values)
            self.assertFalse(any('/items?' in p for _,p in self.fixture.paths[before:]))

    def test_group_navigation_pages_metadata_without_full_book_scans(self):
        authors = [{'id':'author-'+str(i), 'name':f'Writer {i:03}'} for i in range(110)]
        with patch.object(self.books.provider, 'user') as user:
            user.side_effect = [{'libraries':[{'id':'books','mediaType':'book'}]}, {'authors':authors}]
            result = self.books.groups(1, 'authors', page=1)
            self.assertEqual(len(result['items']),48); self.assertEqual(result['items'][0]['id'],'author-48')
            self.assertEqual(result['next_page'],2)
            self.assertTrue(user.call_args_list[1].args[2].endswith('/filterdata'))

    def test_author_series_include_only_the_selected_library(self):
        with patch.object(self.books.provider, 'user') as user:
            user.side_effect = [{'libraries':[{'id':'books','mediaType':'book'}]}, {'series':[
                {'id':'series-one','name':'Stories','items':[{'libraryId':'books'}]},
                {'id':'private-series','name':'Other books','items':[{'libraryId':'private'}]}]}]
            result = self.books.groups(1, 'series', author='writer')
            self.assertEqual([x['id'] for x in result['items']], ['series-one'])
            self.assertIn('library=books',user.call_args_list[1].args[2])

    def test_metadata_preserves_author_series_numbers_and_date_precision(self):
        item = self.fixture.item(); item['addedAt'] = 123
        item['media']['metadata'].update(authors=[{'id':'writer','name':'Writer'}],
            series=[{'id':'series','name':'Stories','sequence':'2.5'}], publishedYear='2024')
        book = self.books.metadata(item)
        self.assertEqual(book['series_entries'][0]['sequence'],'2.5')
        self.assertEqual(book['authors'][0]['id'],'writer')
        self.assertIsNone(book['published_date']); self.assertEqual(book['published_year'],'2024')
        item['media']['metadata'].update(authors=None, series=None, seriesName='Stories')
        self.assertEqual(self.books.metadata(item)['series_entries'], [])

    def test_accepted_start_retry_reuses_session_and_does_not_reclaim_output(self):
        first = self.play(request='same-request')
        with patch.object(self.listening, 'claim_external', side_effect=AssertionError('Duplicate ownership claim')):
            second = self.play(request='same-request')
        self.assertEqual(first['lease'],second['lease']); self.assertEqual(first['revision'],second['revision'])

    def tearDown(self):
        self.books.close(); self.a.db.close(); self.tmp.cleanup()

    def play(self, uid=1, device=None, book='book-one', request='request-one', **extra):
        state = self.listening.state(uid)['listening']
        return self.books.begin(uid, {'book_id': book, 'device_id': device or self.device, 'request_id': request,
            'music_revision': state['revision'], **extra}, self.token, 'web')

    def test_accounts_are_created_transparently_once_with_restricted_permissions(self):
        with ThreadPoolExecutor(4) as pool: values = list(pool.map(lambda _: self.books.library(1), range(4)))
        self.assertEqual(self.fixture.created, 1); self.assertEqual(len(values[0]['items']), 2)
        self.books.library(2); self.assertEqual(self.fixture.created, 2)
        user = next(x for x in self.fixture.users.values() if x['id'] == 'managed-1')
        self.assertTrue(user['isActive']); self.assertEqual(user['type'], 'user')
        self.assertFalse(user['permissions']['accessAllLibraries']); self.assertEqual(user['librariesAccessible'], ['books'])
        self.assertFalse(user['permissions']['delete']); self.assertFalse(user['permissions']['upload'])
        payloads = '\n'.join(x[0] for x in self.books.db.execute('SELECT payload FROM provider_accounts'))
        self.assertNotIn(user['password'], payloads); self.assertNotIn('private-admin-password', json.dumps(values))

    def test_lost_provision_response_survives_restart_without_duplicate_account(self):
        self.fixture.lose_create = True
        with self.assertRaises(BookError): self.books.library(1)
        self.books.close(); self.books = Audiobooks(self.a, self.listening, self.tmp.name, self.fixture.fetch)
        self.books.library(1); self.assertEqual(self.fixture.created, 1)

    def test_erasure_recovers_and_removes_a_lost_provision_response(self):
        self.fixture.lose_create = True
        with self.assertRaises(BookError): self.books.library(1)
        self.a.delete_account('a-strong-password', user_id=1); self.books.cleanup()
        self.assertEqual(list(self.fixture.users), ['admin'])
        with self.assertRaises(BookError): self.books.library(1)

    def test_progress_is_canonical_per_user_and_rewinds_are_preserved(self):
        result = self.play()
        for seq, position in [(1, 50), (2, 20)]:
            self.books.progress(1, {'lease': result['lease'], 'device_id': self.device, 'seq': seq, 'position': position, 'playing': False})
        self.assertEqual(self.books.book(1, 'book-one')['progress']['currentTime'], 20)
        self.assertEqual(self.books.book(2, 'book-one')['progress']['currentTime'], 0)
        self.books.progress(1, {'lease': result['lease'], 'device_id': self.device, 'seq': 1, 'position': 70})
        self.assertEqual(self.books.book(1, 'book-one')['progress']['currentTime'], 20)

    def test_lost_progress_response_reconciles_without_replaying_old_position(self):
        result = self.play(); self.fixture.lose_progress = True
        value = self.books.progress(1, {'lease': result['lease'], 'device_id': self.device, 'seq': 1, 'position': 33})
        self.assertTrue(value['pending']); self.assertTrue(self.books.checkpoint(1, 'book-one')['dirty'])
        self.books.flush(1, 'book-one')
        self.assertFalse(self.books.checkpoint(1, 'book-one')['dirty'])
        writes = [x for x in self.fixture.paths if x == ('PATCH', '/api/me/progress/book-one')]
        self.assertEqual(len(writes), 1)

    def test_offline_progress_survives_restart_and_stale_device_cannot_overwrite(self):
        first = self.play(); self.fixture.offline = True
        value = self.books.progress(1, {'lease': first['lease'], 'device_id': self.device, 'seq': 1, 'position': 40})
        self.assertTrue(value['pending']); self.books.close()
        self.books = Audiobooks(self.a, self.listening, self.tmp.name, self.fixture.fetch); self.fixture.offline = False
        second = self.play(device='b'*32, request='second')
        self.assertEqual(second['position'], 40)
        with self.assertRaises(BookError) as error:
            self.books.progress(1, {'lease': first['lease'], 'device_id': self.device, 'seq': 2, 'position': 5})
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.books.book(1, 'book-one')['progress']['currentTime'], 40)

    def test_music_and_book_handoff_fences_streams_without_changing_music_dto(self):
        before = self.listening.command(1, {'action': 'play', 'ids': [1, 2], 'device_id': self.device, 'expected_revision': 0})
        result = self.play(); stream = result['book']['parts'][0]['url'].split('/')
        self.books.stream(stream[-2], 0)
        state = self.listening.state(1); native = snapshot(state)
        self.assertEqual(native['current_track_id'], before['current']); self.assertFalse(native['playing'])
        self.listening.command(1, {'action': 'resume', 'device_id': self.device, 'owner_epoch': state['listening']['epoch'], 'expected_revision': state['listening']['revision']})
        with self.assertRaises(BookError): self.books.stream(stream[-2], 0)

    def test_revocation_blocks_existing_book_stream_and_account_erasure_cleans_provider(self):
        result = self.play(); self.a.db.execute('DELETE FROM sessions WHERE digest=?', (self.a.digest(self.token),))
        with self.assertRaises(BookError): self.books.stream(result['lease'], 0)
        self.a.delete_account('a-strong-password', user_id=1); self.books.cleanup()
        self.assertEqual(list(self.fixture.users), ['admin'])
        self.assertIsNone(self.books.provider.saved_user(1))

    def test_long_books_and_nonfinite_positions(self):
        self.fixture.duration = 100000; result = self.play()
        self.books.progress(1, {'lease': result['lease'], 'device_id': self.device, 'seq': 1, 'position': 90000})
        self.assertEqual(self.books.book(1, 'book-one')['progress']['currentTime'], 90000)
        with self.assertRaises(BookError): self.books.progress(1, {'lease': result['lease'], 'device_id': self.device, 'seq': 2, 'position': float('nan')})

    def test_download_does_not_claim_music_and_revocation_still_applies(self):
        music = self.listening.command(1, {'action':'play','ids':[1,2],'device_id':self.device,'expected_revision':0})
        result = self.books.download(1, {'book_id':'book-one'}, self.token, 'web')
        self.assertEqual(self.listening.state(1)['listening']['intent'], music['listening']['intent'])
        self.assertTrue(self.listening.state(1)['listening']['playing'])
        self.books.stream(result['lease'], 0)
        self.a.db.execute('DELETE FROM sessions WHERE digest=?', (self.a.digest(self.token),))
        with self.assertRaises(BookError): self.books.stream(result['lease'], 0)

    def test_expired_provider_access_and_lost_refresh_do_not_sign_out_arrify(self):
        self.books.library(1); saved = self.books.provider.saved_user(1); old = saved['refresh']; self.fixture.access.pop(saved['access'])
        self.fixture.lose_refresh = True
        with self.assertRaises(BookError): self.books.library(1)
        self.books.library(1)
        self.assertEqual(self.fixture.refresh_uses.count(old), 1)
        self.assertEqual(self.a.identity(self.token, 'web')['id'], 1)

    def test_external_progress_change_requires_explicit_resume_choice(self):
        result = self.play(); self.books.library(1)
        provider = self.books.provider.saved_user(1)['provider_id']
        self.fixture.progress[(provider, 'book-one')] = {'currentTime': 80, 'lastUpdate': 999, 'isFinished': False}
        with self.assertRaises(BookError) as error:
            self.books.progress(1, {'lease': result['lease'], 'device_id': self.device, 'seq': 1, 'position': 20})
        self.assertEqual(error.exception.status, 409)
        recovered = self.play(request='choose-remote', use_remote=True)
        self.assertEqual(recovered['position'], 80)

    def test_book_queue_is_separate_from_chapters_and_never_auto_enables_continuous_play(self):
        result = self.play(); self.assertEqual(len(result['book']['chapters']), 2)
        value = self.books.queue(1, {'action': 'next', 'book_id': 'book-two', 'revision': result['revision']})
        self.assertEqual([x['id'] for x in value['queue']], ['book-two'])
        self.assertFalse(self.books.preferences(1)['continuous'])
        with self.assertRaises(BookError): self.books.queue(1, {'action': 'order', 'ids': [], 'revision': result['revision']})

    def test_multiple_audio_files_remain_one_book_with_one_canonical_cursor(self):
        original = self.fixture.item
        def multipart(book_id='book-one'):
            item = original(book_id)
            if book_id == 'book-one':
                item['media']['tracks'] = [
                    {'startOffset':0,'duration':20,'mimeType':'audio/mpeg','contentUrl':'/api/items/book-one/file/123'},
                    {'startOffset':20,'duration':100,'mimeType':'audio/mpeg','contentUrl':'/api/items/book-one/file/124'}]
                item['media']['chapters'] = [{'title':'Prologue','start':0,'end':20},{'title':'Chapter One','start':20,'end':120}]
            return item
        self.fixture.item = multipart
        library = self.books.library(1)
        self.assertEqual([x['id'] for x in library['items']].count('book-one'),1)
        result = self.play()
        self.assertEqual([x['start'] for x in result['book']['parts']],[0,20])
        self.assertEqual([x['title'] for x in result['book']['chapters']],['Prologue','Chapter One'])
        self.assertEqual(self.books.stream(result['lease'],1)[2]['path'],'/api/items/book-one/file/124')
        self.books.progress(1,{'lease':result['lease'],'device_id':self.device,'seq':1,'position':25,'playing':False})
        self.assertEqual(self.books.remote_progress(1,'book-one')['currentTime'],25)
        self.assertEqual(self.books.public_state(1)['queue'],[])

    def test_embedded_chapter_overrun_does_not_block_playback(self):
        self.fixture.duration = 1765.219456
        original = self.fixture.item
        def overrun(book_id='book-one'):
            item = original(book_id)
            item['media']['chapters'] = [{'title':'ch2','start':0,'end':1769.259}]
            return item
        self.fixture.item = overrun
        result = self.play()
        self.assertEqual(result['book']['chapters'], [{'title':'ch2','start':0,'end':1765.219456}])
        self.assertEqual(self.books.stream(result['lease'], 0)[2]['path'], '/api/items/book-one/file/123')
        with self.assertRaises(BookError):
            self.books.progress(1, {'lease':result['lease'], 'device_id':self.device,
                                  'seq':1, 'position':1769.259, 'playing':False})

    def test_invalid_optional_chapters_fall_back_to_audio_parts(self):
        original = self.fixture.item
        for markers in ([None, {'start':True,'end':20}, {'start':0,'end':float('nan')},
                         {'start':0,'end':'120'}, {'start':130,'end':150}, {'start':50,'end':20}],
                        [{'start':0,'end':float('inf')} ]):
            with self.subTest(markers=markers):
                item = original(); item['media']['chapters'] = markers
                result = self.books.metadata(item, True)
                self.assertEqual(result['chapters'], [{'title':'Part 1','start':0,'end':120}])

    def test_unsafe_provider_media_url_is_rejected(self):
        old = self.fixture.item
        def malicious(book_id='book-one'):
            item = old(book_id); item['media']['tracks'][0]['contentUrl'] = 'https://external.invalid/secret'; return item
        self.fixture.item = malicious
        with self.assertRaises(BookError): self.play()

    def test_failed_configuration_never_publishes_candidate(self):
        before = self.books.provider.config()
        self.fixture.offline = True
        with self.assertRaises(BookError): self.books.provider.configure({'password':'replacement', 'enabled':False})
        self.assertEqual(self.books.provider.config(), before)

    def test_series_scoped_provider_response_accepts_single_series_object(self):
        item = self.fixture.item()
        item['media']['metadata']['series'] = {'id':'series-one','name':'Stories','sequence':'2.5'}
        value = self.books.metadata(item)
        self.assertEqual(value['series_entries'], [{'id':'series-one','name':'Stories','sequence':'2.5'}])
        self.assertEqual(value['series'], 'Stories')

    def test_downloading_on_another_device_preserves_the_first_devices_grant(self):
        first = self.books.download(1, {'book_id':'book-one'}, self.token, 'web')
        second_token = self.a.login('readerone', 'a-strong-password')
        second = self.books.download(1, {'book_id':'book-one'}, second_token, 'web')
        self.assertNotEqual(first['lease'], second['lease'])
        self.books.stream(first['lease'],0); self.books.stream(second['lease'],0)
        self.a.db.execute('DELETE FROM sessions WHERE digest=?', (self.a.digest(second_token),))
        self.books.stream(first['lease'],0)
        with self.assertRaises(BookError): self.books.stream(second['lease'],0)

    def test_replacing_an_edition_revokes_old_download_grants_on_other_devices(self):
        first = self.books.download(1, {'book_id':'book-one'}, self.token, 'web')
        old = self.fixture.item
        def changed(book_id='book-one'):
            item = old(book_id); item['media']['tracks'][0]['metadata'] = {'mtimeMs':100}; return item
        self.fixture.item = changed
        second_token = self.a.login('readerone', 'a-strong-password')
        second = self.books.download(1, {'book_id':'book-one'}, second_token, 'web')
        self.books.stream(second['lease'],0)
        with self.assertRaises(BookError): self.books.stream(first['lease'],0)

    def test_resume_download_reuses_url_but_changed_edition_does_not(self):
        first = self.books.download(1, {'book_id':'book-one'}, self.token, 'web')
        self.assertEqual(self.books.download(1, {'book_id':'book-one'}, self.token, 'web')['lease'], first['lease'])
        old = self.fixture.item
        def changed(book_id='book-one'):
            item = old(book_id); item['media']['tracks'][0]['metadata'] = {'mtimeMs':100}; return item
        self.fixture.item = changed
        self.assertNotEqual(self.books.download(1, {'book_id':'book-one'}, self.token, 'web')['lease'], first['lease'])
        with self.assertRaises(BookError): self.books.stream(first['lease'], 0)

    def test_library_rechecks_progress_when_provider_filter_contains_a_reset_book(self):
        self.books.provider.user(1, 'PATCH', '/api/me/progress/book-one', {'currentTime':30, 'isFinished':False})
        download = self.books.download(1, {'book_id':'book-one'}, self.token, 'web')
        stamp = self.books.remote_progress(1, 'book-one')['lastUpdate']
        self.books.offline_progress(1, {'book_id':'book-one', 'source_revision':download['book']['source_revision'],
            'session_id':'reset-book', 'device_id':self.device, 'base_remote_stamp':stamp, 'seq':1, 'position':0})
        original = self.books.provider.user
        def stale_filter(uid, method, path, *args, **kwargs):
            if '/items?' in path: return {'results':[self.fixture.item()], 'total':1}
            return original(uid, method, path, *args, **kwargs)
        with patch.object(self.books.provider, 'user', side_effect=stale_filter):
            self.assertEqual(self.books.library(1, progress_filter='started')['items'], [])
            self.assertEqual(self.books.library(1, progress_filter='finished')['items'], [])
            self.assertEqual([x['id'] for x in self.books.library(1, progress_filter='unstarted')['items']], ['book-one'])
        self.assertEqual(self.books.continue_listening(1)['items'], [])

    def test_audio_identity_ignores_metadata_edits_but_detects_media_changes(self):
        item = self.fixture.item()
        before = self.books.metadata(item, True)
        item['updatedAt'] = 200
        item['media']['metadata']['title'] = 'Corrected title'
        item['media']['chapters'][0]['title'] = 'Corrected chapter'
        corrected = self.books.metadata(item, True)
        self.assertNotEqual(before['source_revision'], corrected['source_revision'], 'Keep the legacy client identity compatible')
        self.assertEqual(before['audio_revision'], corrected['audio_revision'])
        for field, value in [('contentUrl','/api/items/book-one/file/456'), ('duration',119), ('startOffset',1)]:
            changed = self.fixture.item(); changed['media']['tracks'][0][field] = value
            self.assertNotEqual(before['audio_revision'], self.books.metadata(changed, True)['audio_revision'], field)
        changed = self.fixture.item(); changed['media']['tracks'][0]['metadata'] = {'mtimeMs':123,'size':800}
        self.assertNotEqual(before['audio_revision'], self.books.metadata(changed, True)['audio_revision'])

    def test_metadata_only_download_refresh_keeps_grants_on_both_devices(self):
        first = self.books.download(1, {'book_id':'book-one'}, self.token, 'web')
        other_token = self.a.login('readerone', 'a-strong-password')
        other = self.books.download(1, {'book_id':'book-one'}, other_token, 'web')
        original = self.fixture.item
        def corrected(book_id='book-one'):
            item = original(book_id); item['updatedAt'] = 123; return item
        self.fixture.item = corrected
        refreshed = self.books.download(1, {'book_id':'book-one'}, self.token, 'web')
        self.assertEqual(first['lease'], refreshed['lease'])
        self.books.stream(first['lease'],0); self.books.stream(other['lease'],0)

    def test_audio_identity_allows_metadata_only_sync_and_rejects_real_replacement(self):
        download = self.books.download(1, {'book_id':'book-one'}, self.token, 'web')
        data = {'book_id':'book-one', 'source_revision':download['book']['source_revision'],
                'audio_revision':download['book']['audio_revision'], 'session_id':'metadata-sync',
                'device_id':self.device, 'base_remote_stamp':0, 'seq':1, 'position':30}
        original = self.fixture.item
        def corrected(book_id='book-one'):
            item = original(book_id); item['updatedAt'] = 321; return item
        self.fixture.item = corrected
        self.assertTrue(self.books.offline_progress(1, data)['saved'])
        def replaced(book_id='book-one'):
            item = corrected(book_id); item['media']['tracks'][0]['duration'] = 119; return item
        self.fixture.item = replaced
        with self.assertRaises(BookError) as failure:
            self.books.offline_progress(1, {**data, 'seq':2, 'position':40})
        self.assertEqual(failure.exception.reason, 'edition_changed')
        self.assertEqual(self.books.remote_progress(1, 'book-one')['currentTime'],30)

    def test_offline_download_progress_restarts_rewinds_and_lost_ack_without_claiming_output(self):
        download = self.books.download(1, {'book_id':'book-one'}, self.token, 'web')
        other = self.listening.command(1, {'action':'play','ids':[1,2],'device_id':'b'*32,'expected_revision':0})
        data = {'book_id':'book-one', 'source_revision':download['book']['source_revision'],
                'session_id':'offline-session', 'device_id':self.device, 'base_remote_stamp':0, 'seq':1, 'position':50}
        self.fixture.lose_progress = True
        with self.assertRaises(BookError): self.books.offline_progress(1, data)
        self.books.close(); self.books = Audiobooks(self.a, self.listening, self.tmp.name, self.fixture.fetch)
        self.books.offline_progress(1, data)
        patches = [x for x in self.fixture.paths if x[0] == 'PATCH' and '/progress/' in x[1]]
        self.assertEqual(len(patches), 1)
        self.books.offline_progress(1, {**data, 'seq':2, 'position':20})
        self.books.offline_progress(1, data)  # Delayed acknowledgement/retry cannot undo a rewind.
        self.assertEqual(self.books.remote_progress(1, 'book-one')['currentTime'], 20)
        self.assertEqual(self.listening.state(1)['listening'], other['listening'])

    def test_offline_progress_conflict_preserves_other_device_position(self):
        download = self.books.download(1, {'book_id':'book-one'}, self.token, 'web')
        self.books.provider.user(1, 'PATCH', '/api/me/progress/book-one', {'currentTime':90,'isFinished':False})
        with self.assertRaises(BookError) as failure:
            self.books.offline_progress(1, {'book_id':'book-one', 'source_revision':download['book']['source_revision'],
                'session_id':'offline-session','device_id':self.device,'base_remote_stamp':0,'seq':1,'position':30})
        self.assertEqual(failure.exception.status, 409)
        self.assertEqual(self.books.remote_progress(1, 'book-one')['currentTime'], 90)

    def test_canonical_continue_listening_and_filters_include_only_own_unfinished_books(self):
        self.books.provider.user(1, 'PATCH', '/api/me/progress/book-two', {'currentTime':30, 'isFinished':False})
        self.books.provider.user(2, 'PATCH', '/api/me/progress/book-one', {'currentTime':50, 'isFinished':False})
        self.assertEqual([x['id'] for x in self.books.continue_listening(1)['items']], ['book-two'])
        self.assertEqual([x['id'] for x in self.books.library(1, progress_filter='started')['items']], ['book-two'])
        self.assertFalse(self.books.library(1, progress_filter='finished')['items'])

    def test_automatic_advance_cannot_steal_output_after_music_takeover(self):
        book = self.play(); state = self.listening.state(1)
        self.listening.command(1, {'action':'play','ids':[1,2],'device_id':self.device,
            'owner_epoch':state['listening']['epoch'],'expected_revision':state['listening']['revision']})
        with self.assertRaises(BookError): self.play(book='book-two', request='automatic', automatic=True, previous_lease=book['lease'])
        with self.assertRaises(BookError): self.books.progress(1, {'lease':book['lease'], 'device_id':self.device, 'seq':1, 'position':25})


class AudiobookHTTPTests(unittest.TestCase):
    play = AudiobookTests.play
    def setUp(self):
        AudiobookTests.setUp(self); self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.accounts = self.a; self.server.player = self.player; self.server.library = self.player.lib
        self.server.audiobooks = self.books; self.server.active_streams = ActiveStreams()
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.patch = patch('app.server.ORIGINS', frozenset([self.origin])); self.patch.start()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.patch.stop(); AudiobookTests.tearDown(self)

    def call(self, path, method='GET', data=None, authenticated=True, csrf=True, headers=None):
        request_headers = {'Origin': self.origin, **(headers or {})}
        if authenticated: request_headers['Cookie'] = 'arrify_session=' + self.token
        if data is not None:
            request_headers['Content-Type'] = 'application/json'
            if csrf: request_headers['X-CSRF-Token'] = self.a.csrf(self.token)
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        connection.request(method, path, json.dumps(data) if data is not None else None, request_headers)
        response = connection.getresponse(); result = response.status, response.read(), dict(response.getheaders()); connection.close(); return result

    def test_http_auth_csrf_ranges_and_session_scoped_media(self):
        self.assertEqual(self.call('/api/v1/audiobooks/library', authenticated=False)[0], 403)
        self.assertEqual(self.call('/api/v1/audiobooks/preferences', 'POST', {'speed': 2}, csrf=False)[0], 403)
        result = self.play(); url = result['book']['parts'][0]['url']
        code, body, headers = self.call(url, headers={'Range': 'bytes=0-1'}, authenticated=False)
        self.assertEqual((code, body), (206, self.fixture.audio[:2])); self.assertEqual(headers['Content-Type'], 'audio/mpeg')
        self.assertEqual(self.call(url, method='HEAD', authenticated=False)[0], 200)
        self.assertEqual(self.call(url.rsplit('/', 1)[0]+'/9', authenticated=False)[0], 404)
        self.a.db.execute('DELETE FROM sessions WHERE digest=?', (self.a.digest(self.token),))
        self.assertEqual(self.call(url, authenticated=False)[0], 403)

    def test_http_never_exposes_provider_secrets_and_non_admin_cannot_configure(self):
        self.assertEqual(self.call('/api/v1/admin/audiobooks')[0], 403)
        code, body, _ = self.call('/api/v1/audiobooks/library')
        self.assertEqual(code, 200)
        for private in ('private-admin-password', 'access-', 'refresh-', 'contentUrl', 'password'):
            self.assertNotIn(private.encode(), body)

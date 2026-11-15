import contextlib
import json
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from app.native_auth import NativeError
from app.native_catalog import page
from app.native_library import detail, expand_playback
from app.native_moods import MOOD_GROUPS
from app.search import Search

class NativeLibraryTests(unittest.TestCase):
    def setUp(self):
        tracks = [{'id': i, 'title': 'Song %03d' % i, 'artist': 'P!nk', 'album': 'Album', 'track_number': str(121-i), 'artwork': '/api/v1/artwork/'+str(i)} for i in range(1,121)]
        album = {'id':'a'*24,'name':'Album','artist':'P!nk','track_ids':list(range(1,121)), 'artwork':'/api/v1/artwork/1'}
        self.lib=SimpleNamespace(index_lock=contextlib.nullcontext(), tracks={t['id']:t for t in tracks}, albums={album['id']:album}, artists={}, search=Search(tracks,[album],[]))
        self.lib.collections=lambda:[{'slug':'station','name':'Station','section':'channels','tracks':120},{'slug':'mix','name':'Mix','section':'playlists','tracks':120}]
        self.lib.collection_ids=lambda slug:list(range(1,121))
    def test_browse_playback_expands_entire_filtered_selection(self):
        from unittest.mock import Mock
        self.lib.discovery = Mock()
        self.lib.discovery.select.return_value = list(range(1, 121))
        filters = {'genre':'pop', 'decade':'2000', 'kind':'songs'}
        result = expand_playback(self.lib, None, 1, {'action':'play','source':{'kind':'browse','id':json.dumps(filters)},'track_id':75})
        self.assertEqual(len(result['ids']), 120)
        self.assertEqual(result['track_id'], 75)
        self.lib.discovery.select.assert_called_once_with(filters)
        from urllib.parse import urlencode
        page = detail(self.lib, None, 1, '/api/v1/native/library/detail?' + urlencode({'kind':'browse','id':json.dumps(filters)}))
        self.assertEqual(page['item']['kind'], 'browse')
        self.assertEqual(page['item']['subtitle'], '')
        self.assertEqual(page['item']['track_count'], 120)
        for value in ['[]', '{"genre":1}', 'not-json']:
            with self.assertRaises(NativeError): expand_playback(self.lib, None, 1, {'source':{'kind':'browse','id':value}})

    def test_search_popularity_preserves_navigation_and_page_order(self):
        from unittest.mock import Mock
        self.lib.popularity = Mock(spec=["get"])
        self.lib.popularity.get.side_effect = lambda artist, rows: ([dict(row, popularity={'doubloons': 2, 'evidence': []}) for row in rows], True)
        result = page(self.lib, '/api/v1/native/catalog?q=Pink&limit=3')
        self.assertEqual(len(result['items']), 3)
        self.assertTrue(all(row['popularity']['doubloons'] == 2 for row in result['items']))
        self.lib.popularity.get.assert_called_once()
        self.assertEqual(result['next_offset'], 3)
        self.lib.popularity.get.reset_mock()
        page(self.lib, '/api/v1/native/catalog')
        self.lib.popularity.get.assert_not_called()

    def test_track_navigation_ids_and_detail_popularity(self):
        from app.native_library import track
        row = dict(self.lib.tracks[1], album_id='album', artist_id='artist')
        self.assertEqual(track(row)['album_id'], 'album')
        self.assertEqual(track(row)['artist_id'], 'artist')
        from unittest.mock import Mock
        self.lib.popularity = Mock(spec=["get"])
        self.lib.popularity.get.side_effect = lambda artist, rows: ([dict(row, popularity={'doubloons': 1}) for row in rows], False)
        result = detail(self.lib, None, 1, '/api/v1/native/library/detail?kind=albums&id='+'a'*24)
        self.assertEqual(result['items'][0]['popularity']['doubloons'], 1)

    def test_radio_source_is_validated_and_keeps_radio_semantics(self):
        from unittest.mock import Mock
        self.lib.radio = Mock()
        self.lib.radio.describe.return_value = {'kind':'track', 'seed':'1'}
        result = expand_playback(self.lib, None, 1, {'action':'play', 'source':{'kind':'radio-track','id':'1'}})
        self.assertEqual(result['radio'], {'kind':'track','seed':'1'})
        self.assertNotIn('ids', result)
        self.lib.radio.describe.assert_called_once_with('track', '1')

    def test_empty_catalog_browses_and_search_still_uses_aliases(self):
        result=page(self.lib,'/api/v1/native/catalog')
        self.assertEqual((len(result['items']),result['total'],result['next_offset']),(50,120,50))
        self.assertEqual(page(self.lib,'/api/v1/native/catalog?q=Pink')['total'],120)
        self.assertEqual(page(self.lib,'/api/v1/native/catalog?kind=albums')['items'][0]['artwork'],'/api/v1/artwork/1')
    def test_detail_pagination_album_order_and_revision(self):
        path='/api/v1/native/library/detail?kind=albums&id='+'a'*24
        first=detail(self.lib,None,1,path)
        self.assertEqual(first['items'][0]['id'],120)
        second=detail(self.lib,None,1,path+'&offset=50&revision='+first['revision'])
        self.assertEqual(second['items'][0]['id'],70)
        self.lib.albums['a'*24]['track_ids'].pop()
        with self.assertRaises(NativeError) as error: detail(self.lib,None,1,path+'&offset=50&revision='+first['revision'])
        self.assertEqual(error.exception.status,409)
        for query in ('&limit=101','&offset=-1','&offset=2&offset=3'):
            with self.assertRaises(NativeError):detail(self.lib,None,1,path+query)
    def test_playback_uses_full_context_and_channels_keep_rotation(self):
        body={'action':'play','source':{'kind':'albums','id':'a'*24},'track_id':90}
        command=expand_playback(self.lib,None,1,body)
        self.assertEqual(len(command['ids']),120)
        self.assertEqual(command['track_id'],90)
        self.assertNotIn('source',command)
        channel=expand_playback(self.lib,None,1,{'action':'play','source':{'kind':'channels','id':'station'}})
        self.assertEqual(channel['channel'],'station');self.assertNotIn('ids',channel)
        for bad in ({**body,'track_id':999},{**body,'ids':[1]},{**body,'shuffle':'true'}):
            with self.assertRaises(NativeError):expand_playback(self.lib,None,1,bad)
    def test_private_playlist_checks_account_access(self):
        with patch('app.native_library.Playlists') as playlists:
            playlists.return_value.get.side_effect=LookupError('private')
            with self.assertRaises(NativeError) as error:detail(self.lib,object(),7,'/detail?kind=playlists&id=42')
            self.assertEqual(error.exception.status,404)
            playlists.return_value.get.assert_called_once_with(7,42)
    def test_mood_taxonomy_matches_website(self):
        source=(Path(__file__).resolve().parents[1]/'web/app.js').read_text().split('const moodGroups=')[1].split(';')[0]
        source=re.sub(r'(\w+):',r'"\1":',source).replace("'",'"')
        self.assertEqual(MOOD_GROUPS,json.loads(source))

class FavoriteLibraryTests(NativeLibraryTests):
    def test_favorites_share_web_storage_and_are_account_scoped(self):
        import sqlite3
        from app.native_library import favorites
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE favorites(user_id INTEGER,track_id INTEGER,PRIMARY KEY(user_id,track_id))')
        a = SimpleNamespace(db=db, lock=contextlib.nullcontext())
        self.assertEqual(favorites(self.lib,a,1,{'track_id':2,'liked':True})['track_ids'],[2])
        self.assertEqual(favorites(self.lib,a,2)['track_ids'],[])
        db.execute('INSERT INTO favorites VALUES(1,3)') # web write appears natively
        result = detail(self.lib,a,1,'/detail?kind=favorites&id=liked')
        self.assertEqual([t['id'] for t in result['items']],[3,2])
        command = expand_playback(self.lib,a,1,{'action':'play','source':{'kind':'favorites','id':'liked'}})
        self.assertEqual(command['ids'],[3,2])
        self.assertEqual(command['playback_context']['name'],'Liked songs')
        favorites(self.lib,a,1,{'track_id':2,'liked':False})
        self.assertEqual(db.execute('SELECT track_id FROM favorites WHERE user_id=1').fetchall(),[(3,)])
        for data in ({'track_id':True,'liked':True},{'track_id':1,'liked':'true'},{'track_id':999,'liked':True},{'track_id':1,'liked':True,'user_id':2}):
            with self.assertRaises(NativeError):favorites(self.lib,a,1,data)

class HomeLibraryTests(NativeLibraryTests):
    def test_home_albums_follow_account_history_and_skip_deleted_tracks(self):
        import sqlite3
        from app.native_library import overview
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.execute('CREATE TABLE history(user_id INTEGER,track_id INTEGER,played_at INTEGER)')
        db.executemany('INSERT INTO history VALUES(?,?,?)',[(1,1,10),(1,2,20),(1,999,30),(2,3,40)])
        a=SimpleNamespace(db=db,lock=contextlib.nullcontext())
        self.lib.tracks[1]['album_id']='a'*24;self.lib.tracks[2]['album_id']='a'*24
        self.lib.suggestions=lambda:[]
        with patch('app.native_library.Playlists') as playlists, patch('app.native_library.PersonalStations') as stations:
            playlists.return_value.rows.return_value=[];stations.return_value.collections.return_value=[]
            result=overview(self.lib,a,1)
            self.assertEqual([x['id'] for x in result['home_albums']],['a'*24])
            self.assertEqual(result['home_albums'][0]['track_count'],120)
            self.assertEqual([x['id'] for x in result['recent_tracks']],[2,1])
            self.assertEqual([x['id'] for x in overview(self.lib,a,2)['recent_tracks']],[3])
            result = detail(self.lib,a,1,'/api/v1/native/library/detail?kind=recent&id=played')
            self.assertEqual([x['id'] for x in result['items']],[2,1])
            self.assertEqual(expand_playback(self.lib,a,1,{'action':'play','source':{'kind':'recent','id':'played'}})['ids'],[2,1])
            self.assertEqual(overview(self.lib,a,2)['home_albums'],[])

class NativeNavigationTests(NativeLibraryTests):
    def test_direct_track_window_is_bounded_and_can_page_both_directions(self):
        result = page(self.lib, '/catalog?around=90')
        self.assertEqual(result['offset'], 64)
        self.assertEqual(result['previous_offset'], 14)
        self.assertEqual(len(result['items']), 50)
        self.assertEqual(result['items'][25]['id'], 90)
        self.assertEqual(result['next_offset'], 114)
        earlier = page(self.lib, '/catalog?offset=14&revision=' + result['revision'])
        self.assertEqual(earlier['items'][-1]['id'], 64)
        for suffix in ('?around=bad', '?letter=ZZ', '?sort=bad', '?liked=1'):
            with self.assertRaises(NativeError): page(self.lib, '/catalog' + suffix)

    def test_detail_window_does_not_reorder_playlist_or_lose_duplicates(self):
        self.lib.collection_ids = lambda slug: [1, 2, 1] + list(range(3, 121))
        first = detail(self.lib, None, 1, '/detail?kind=collections&id=mix')
        self.assertEqual([x['id'] for x in first['items'][:3]], [1, 2, 1])
        window = detail(self.lib, None, 1, '/detail?kind=collections&id=mix&around=100')
        self.assertEqual(window['items'][25]['id'], 100)
        self.assertGreater(window['offset'], 50)
        self.assertEqual(window['total'], 121)

    def test_catalog_playback_expands_beyond_loaded_page_and_respects_sort(self):
        result = expand_playback(self.lib, None, 1, {'action': 'play', 'track_id': 90,
            'source': {'kind': 'catalog', 'id': json.dumps({'q': '', 'sort': 'recent', 'liked': False})}})
        self.assertEqual(result['ids'], list(range(120, 0, -1)))
        self.assertEqual(result['track_id'], 90)
        self.assertEqual(page(self.lib, '/catalog?sort=recent')['items'][0]['id'], 120)

    def test_liked_filter_is_account_scoped_and_revision_detects_changes(self):
        import sqlite3
        db = sqlite3.connect(':memory:'); self.addCleanup(db.close)
        db.execute('CREATE TABLE favorites(user_id INTEGER,track_id INTEGER)')
        db.executemany('INSERT INTO favorites VALUES(?,?)', [(1, 1), (1, 3), (2, 2)])
        accounts = SimpleNamespace(db=db, lock=contextlib.nullcontext())
        result = page(self.lib, '/catalog?liked=true&limit=1', accounts, 1)
        self.assertEqual(result['items'][0]['id'], 1)
        self.assertEqual(result['total'], 2)
        db.execute('DELETE FROM favorites WHERE user_id=1 AND track_id=3')
        with self.assertRaises(NativeError):
            page(self.lib, '/catalog?liked=true&offset=1&revision=' + result['revision'], accounts, 1)
        self.assertEqual(page(self.lib, '/catalog?liked=true', accounts, 2)['items'][0]['id'], 2)

    def test_artist_album_filter_does_not_include_other_artist(self):
        self.lib.search = Search(list(self.lib.tracks.values()), list(self.lib.albums.values()),
            [{'id': 'pink', 'name': 'P!nk', 'track_ids': list(range(1, 121))}])
        self.assertEqual(page(self.lib, '/catalog?kind=albums&artist=pink')['total'], 1)
        self.assertEqual(page(self.lib, '/catalog?kind=albums&artist=missing')['total'], 0)

class PopularityReadCostTests(unittest.TestCase):
    def test_repeated_cached_reads_do_not_commit_per_artist(self):
        import threading
        from unittest.mock import Mock
        from app.popularity import Popularity
        service = Popularity.__new__(Popularity)
        service.lock = threading.RLock(); service.cache = {}; service.refresh = {}; service.pending = set()
        service.db = Mock(); service.enqueue = Mock(); service.signals = Mock()
        service.signals.catalog_pending = set()
        service.signals.score.return_value = {'doubloons': 0}
        with patch('app.popularity.time.time', return_value=1000):
            service.get('Artist', [{'id': 1, 'title': 'Song'}])
            service.get('Artist', [{'id': 1, 'title': 'Song'}])
        self.assertEqual(service.db.commit.call_count, 1)
        self.assertEqual(service.enqueue.call_count, 2, 'Provider scheduling must still run')
        with patch('app.popularity.time.time', return_value=1061):
            service.get('Artist', [{'id': 1, 'title': 'Song'}])
        self.assertEqual(service.db.commit.call_count, 2)

    def test_new_playlist_batches_view_records_in_one_commit(self):
        import threading
        from unittest.mock import Mock
        from app.popularity import Popularity
        service = Popularity.__new__(Popularity)
        service.lock = threading.RLock(); service.cache = {}; service.refresh = {}; service.pending = set()
        service.db = Mock(); service.enqueue = Mock(); service.signals = Mock()
        service.signals.catalog_pending = set(); service.signals.score.return_value = {'doubloons': 0}
        rows = [{'id': i, 'title': 'Song', 'artist': f'Artist {i}'} for i in range(50)]
        with patch('app.popularity.time.time', return_value=1000):
            enriched, pending = service.get_many(rows)
            self.assertEqual(service.db.commit.call_count, 1)
            self.assertEqual(len(service.db.executemany.call_args.args[1]), 50)
            self.assertEqual([x['id'] for x in enriched], list(range(50)))
            self.assertEqual(service.enqueue.call_count, 50)
            service.get_many(rows)
            self.assertEqual(service.db.commit.call_count, 1)

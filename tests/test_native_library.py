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
            self.assertEqual(overview(self.lib,a,2)['home_albums'],[])

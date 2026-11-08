import sqlite3
import threading
import unittest
from types import SimpleNamespace
from app.library import Library

class CollectionArtTests(unittest.TestCase):
    def test_collection_art_uses_only_available_local_members(self):
        db=sqlite3.connect(':memory:');db.executescript('''
            CREATE TABLE tracks(id INTEGER,artist TEXT,album TEXT,available INTEGER);
            CREATE TABLE playlist_tracks(playlist_slug TEXT,track_id INTEGER,position INTEGER);
            INSERT INTO tracks VALUES(1,'A','One',1),(2,'A','One',1),(3,'B','Two',1),(4,'C','Missing',0);
            INSERT INTO playlist_tracks VALUES('mix',1,0),('mix',2,1),('mix',3,2),('mix',4,3);
        ''')
        library=Library.__new__(Library)
        library.music=SimpleNamespace(db=db,lock=threading.RLock(),playlists=lambda:[
            {'slug':'mix','tracks':3},{'slug':'jazz-for-sleep','tracks':0}])
        try:
            collections=library.collections()
            self.assertEqual(collections[0]['artworks'],['/api/v1/artwork/1','/api/v1/artwork/3'])
            self.assertEqual(collections[1]['artworks'],[])
            self.assertIsNone(collections[1]['artwork'])
        finally:db.close()
    def test_daily_album_suggestions_are_stable_and_public(self):
        library=Library.__new__(Library);library.index_lock=threading.RLock()
        library.albums={str(i):{'id':str(i),'name':f'Album {i}','artist':'Artist','artwork':f'/api/v1/artwork/{i}','track_ids':[1,2]} for i in range(12)}
        first=library.suggestions();self.assertEqual(first,library.suggestions());self.assertEqual(len(first),6)
        self.assertTrue(all('track_ids' not in album for album in first))

if __name__=='__main__':unittest.main()

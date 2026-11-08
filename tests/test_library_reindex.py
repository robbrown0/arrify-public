import sqlite3
import tempfile
import threading
import unittest
from types import SimpleNamespace

from app.browse import Browse
from app.library import Library
from app.catalog_engine import SCHEMA


class ReindexTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.execute("INSERT INTO tracks(id,source,relative_path,title,artist,album,extension,mime_type,size,modified_ns) VALUES(1,'nas01','one.mp3','One','Artist','Album','mp3','audio/mpeg',100,1)")
        self.lib=Library.__new__(Library);self.lib.index_lock=threading.RLock()
        self.lib.music=SimpleNamespace(db=self.db,lock=threading.RLock(),dynamic_playlists={})
        self.lib.radio=SimpleNamespace(lock=threading.RLock(),pools={})
        self.lib.reindex()
        self.lib.discovery=Browse(self.tmp.name,self.lib,start=False)

    def tearDown(self):
        self.db.close();self.tmp.cleanup()

    def test_unchanged_refresh_preserves_derived_indexes(self):
        catalog=self.lib.radio_index;search=self.lib.search
        self.lib.discovery.page({'kind':'songs'});browse=self.lib.discovery.view()
        self.lib.radio.pools['sentinel']=catalog
        self.lib.reindex()
        self.assertIs(self.lib.radio_index,catalog);self.assertIs(self.lib.search,search)
        self.assertIs(self.lib.discovery.view(),browse)
        self.assertIs(self.lib.radio.pools['sentinel'],catalog)

    def test_changed_track_and_removed_track_replace_indexes(self):
        old=self.lib.discovery.view()
        self.db.execute("UPDATE tracks SET title='New title' WHERE id=1")
        self.lib.reindex()
        self.assertIsNot(self.lib.discovery.view(),old)
        self.assertEqual(self.lib.discovery.page({})['tracks'][0]['title'],'New title')
        self.db.execute('UPDATE tracks SET available=0 WHERE id=1');self.lib.reindex()
        self.assertEqual(self.lib.discovery.page({})['total'],0)

    def test_membership_only_change_rebuilds_hits(self):
        old=self.lib.radio_index
        self.db.execute("INSERT INTO playlists VALUES('hits','Hits','',0)")
        self.db.execute("INSERT INTO playlist_tracks VALUES('hits',1,0)")
        self.lib.reindex()
        self.assertIsNot(self.lib.radio_index,old)
        self.assertEqual(self.lib.radio_index.hits[1],1)

    def test_metadata_and_quality_changes_are_not_hidden_by_cache(self):
        self.lib.discovery.page({})
        self.lib.metadata=SimpleNamespace(snapshot=lambda:{1:{'modified':1,'source':'test','values':{'genres':['jazz'],'year':1970}}})
        self.lib.reindex()
        self.assertEqual(self.lib.discovery.page({'genre':'jazz','decade':'1970'})['total'],1)
        self.db.execute("INSERT INTO track_technical VALUES(1,1,0,1,'flac',0)")
        self.lib.reindex()
        self.assertEqual(self.lib.discovery.page({})['tracks'][0]['quality'],'Lossless')


if __name__=='__main__':unittest.main()

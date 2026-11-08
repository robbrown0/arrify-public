import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from app.locations import Locations

class Music:
    def __init__(self):
        self.lock=threading.RLock()
        self.base_sources={'nas01':'/music/nas01','unr01':'/music/unr01'}
        self.db=sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE tracks(source TEXT,available INTEGER,size INTEGER DEFAULT 0)')
        self.db.execute("INSERT INTO tracks(source,available) VALUES('nas01',1)")
    def _all_sources(self):return self.base_sources
class Library:
    def __init__(self):self.music=Music()
    def reindex(self):pass

class LocationTests(unittest.TestCase):
    def test_validation(self):
        for path in ['/data','/etc','/mnt/nas-01-music/../secrets','relative',None]:
            with self.assertRaises(ValueError):Locations.resolve(path,False)
        with patch('app.locations.Path.is_dir',return_value=False):
            with self.assertRaises(ValueError):Locations.resolve('/mnt/nas-01-music')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'root';root.mkdir();(root/'escape').symlink_to('/etc')
            with patch('app.locations.MOUNTS',{'/approved':str(root)}):
                with self.assertRaises(ValueError):Locations.resolve('/approved/escape',False)
    def test_edit_remove_persist_and_conflicts(self):
        with tempfile.TemporaryDirectory() as d,patch.object(Locations,'refresh'),patch('app.locations.Path.is_dir',return_value=True),patch('app.locations.os.access',return_value=True):
            lib=Library();s=Locations(lib,d)
            self.assertEqual(s.public()['revision'],0)
            with self.assertRaises(ValueError):s.edit({'action':'save','revision':1,'name':'stale','path':'/mnt/nas-01-music'})
            with self.assertRaises(ValueError):s.edit({'action':'save','revision':0,'name':'overlap','path':'/mnt/nas-01-music/Album'})
            s.edit({'action':'save','revision':0,'name':'Extra','path':'/srv/arrify-example/music-extra'})
            self.assertEqual(len(s.public()['items']),3)
            s.edit({'action':'save','revision':1,'id':'nas01','name':'Renamed','path':'/mnt/nas-01-music'})
            self.assertEqual(s.public()['items'][0]['name'],'Renamed')
            s.edit({'action':'remove','revision':2,'id':'nas01'})
            self.assertNotIn('nas01',lib.music.base_sources)
            self.assertEqual(lib.music.db.execute('SELECT available FROM tracks').fetchone()[0],0)
            self.assertEqual(len(Locations(Library(),d).public()['items']),2)
            self.assertEqual(json.loads((Path(d)/'locations.json').read_text())['revision'],3)
            self.assertEqual((Path(d)/'locations.json').stat().st_mode&0o777,0o600)
            self.assertFalse((Path(d)/'locations.tmp').exists())

    def test_folder_browser_stays_inside_mappings_and_hides_symlinks(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'Albums').mkdir();(root/'Artists').mkdir();(root/'.private').mkdir();(root/'escape').symlink_to('/etc')
            with patch('app.locations.MOUNTS',{'/approved':str(root)}):
                locations=Locations.__new__(Locations)
                self.assertEqual(locations.folders()['items'][0]['path'],'/approved')
                rows=locations.folders('/approved')
                self.assertEqual([x['name'] for x in rows['items']],['Albums','Artists']);self.assertEqual(rows['parent'],'')
                self.assertEqual(locations.folders('/approved','album')['items'][0]['name'],'Albums')
                with self.assertRaises(ValueError):locations.folders('/approved/escape')
                with self.assertRaises(ValueError):locations.folders('/etc')

import math
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from app.sound import Sound,safe_gain

class SoundTests(unittest.TestCase):
    def test_gain_and_peak_ceiling(self):
        self.assertEqual(safe_gain(-20,-10),4)
        self.assertEqual(safe_gain(-8,-.3),-8)
        self.assertEqual(safe_gain(-30,-2),1)
        self.assertEqual(safe_gain(-40,-30),12)
        self.assertEqual(safe_gain(float('-inf'),float('-inf')),0)
    def fixture(self,d):
        class Accounts:
            lock=threading.RLock()
            db=sqlite3.connect(':memory:')
        a=Accounts();a.db.row_factory=sqlite3.Row;a.db.execute('CREATE TABLE users(id INTEGER PRIMARY KEY)');a.db.execute('INSERT INTO users VALUES(1)');a.db.execute('INSERT INTO users VALUES(2)')
        class Music:
            def _track_file(self,tid):return {'modified_ns':1,'size':10},'/never-opened'
        class Library:
            music=Music();index_lock=threading.RLock();albums={'album':{'track_ids':[1,2]}}
            def track(self,tid):return {'album_id':'album'}
        with patch('app.sound.threading.Thread.start'):return Sound(d,Library(),a)
    def test_private_preferences_and_validation(self):
        with tempfile.TemporaryDirectory() as d:
            s=self.fixture(d)
            self.assertEqual(s.preferences(1)['leveling'],'off')
            s.preferences(1,{'leveling':'album','gapless':True})
            self.assertEqual(s.preferences(1)['leveling'],'album')
            self.assertEqual(s.preferences(2)['leveling'],'off')
            for data in [{'leveling':'bogus','gapless':True},{'leveling':'track','gapless':'yes'}]:
                with self.assertRaises(ValueError):s.preferences(1,data)
    def test_cache_pending_and_album_gain(self):
        with tempfile.TemporaryDirectory() as d:
            s=self.fixture(d)
            self.assertEqual(s.status(1,'track')['status'],'analyzing')
            s.status(1,'track');self.assertEqual(s.jobs.qsize(),1)
            for tid,lufs,peak in [(1,-20,-8),(2,-30,-15)]:s.db.execute('INSERT INTO measurements VALUES(?,1,10,?,?,100,0,1)',(tid,lufs,peak))
            self.assertEqual(s.status(1,'track')['gain_db'],4)
            self.assertEqual(s.status(2,'track')['gain_db'],12)
            first=s.status(1,'album');second=s.status(2,'album')
            self.assertEqual(first['gain_db'],second['gain_db']);self.assertTrue(first['album_estimate'])
            self.assertLessEqual(first['gain_db']-8,-1)
            # Replacing a file invalidates the size/mtime-keyed measurement.
            s.lib.music._track_file=lambda tid:({'modified_ns':2,'size':11},'/never-opened')
            self.assertEqual(s.status(1,'track')['status'],'analyzing')
            s.db.execute('INSERT INTO measurements VALUES(1,2,11,NULL,NULL,100,0,1)')
            self.assertEqual(s.status(1,'track')['status'],'silent')

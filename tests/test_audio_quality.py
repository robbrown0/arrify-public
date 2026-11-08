import json
import sqlite3
import threading
import unittest
from types import SimpleNamespace
from app.library import Library

class AudioQualityTests(unittest.TestCase):
    def test_cached_details_still_refresh_current_track_quality(self):
        db=sqlite3.connect(':memory:');db.execute('CREATE TABLE arrify_audio_details(track_id,modified_ns,payload)')
        db.execute('INSERT INTO arrify_audio_details VALUES(1,2,?)',(json.dumps({'duration':12,'sample_rate':44100,'bit_depth':16}),))
        measured={'label':'LOSSLESS'}
        music=SimpleNamespace(lock=threading.RLock(),db=db,_track_file=lambda tid:({'modified_ns':2},'/unused'),track_quality=lambda tid:measured)
        lib=Library.__new__(Library);lib.music=music;lib.index_lock=threading.RLock();lib.tracks={1:{'quality':''}}
        self.assertEqual(lib.technical(1)['quality'],'Lossless');self.assertEqual(lib.track(1)['quality'],'Lossless')
        measured['label']='256 kbps';self.assertEqual(lib.technical(1)['quality'],'256 kbps');self.assertEqual(lib.track(1)['quality'],'256 kbps')
        db.close()

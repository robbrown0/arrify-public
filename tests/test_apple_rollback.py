import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('rollback_apple',Path(__file__).resolve().parents[1]/'tools/close_apple_for_rollback.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class AppleRollbackTests(unittest.TestCase):
    def test_rollback_closes_secure_local_sessions_without_restoring_guest_access(self):
        from app.accounts import Accounts
        from app.library import Player
        from app.parties import Parties
        import test_parties as fixtures
        with tempfile.TemporaryDirectory() as root:
            a=Accounts(root)
            try:
                a.db.execute("INSERT INTO users(id,username,password,approved,created) VALUES(1,'host','!fixture',1,1)")
                player=Player(a,fixtures.Library());service=Parties(a,player,origin='https://arrify.example')
                party=service.host(1,data={'name':'Secure party'});token=party['guest_url'].split('#')[1];_,guest,secret=service.join(token,'Alex')
                service.add(token,secret,guest['csrf'],1)
                state=player.load(1);state.update(party_id=party['id'],upcoming=[1],listening={'intent':1,'revision':1,'playing':True});player.save(1,state)
                self.assertEqual(module.prepare(Path(root)/'accounts.db',Path(root)/'before.db'),1)
                self.assertTrue(service.host(1,party['id'])['ended']);self.assertEqual(a.db.execute('SELECT count(*) FROM party_guests').fetchone()[0],0)
                self.assertNotIn('party_id',player.load(1));self.assertFalse(player.load(1)['listening']['playing'])
            finally:a.db.close()
    def test_rollback_snapshots_and_closes_only_apple_preserving_data(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'accounts.db';backup=Path(root)/'before.db'
            with sqlite3.connect(path) as db:
                db.executescript('CREATE TABLE parties(id TEXT,source TEXT,ended INTEGER,open INTEGER,revision INTEGER);CREATE TABLE apple_party_playback(user_id INTEGER,payload TEXT);CREATE TABLE queue(title TEXT);')
                db.executemany('INSERT INTO parties VALUES(?,?,0,1,1)',[('local','arrify'),('external','apple')]);db.execute('INSERT INTO queue VALUES(?)',('Keep this request',));db.execute('INSERT INTO apple_party_playback VALUES(1,?)',(json.dumps({'party_id':'external','entry_id':3,'epoch':2,'revision':4}),))
            self.assertEqual(module.prepare(path,backup),1)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT source,ended,open FROM parties ORDER BY source').fetchall(),[('apple',1,0),('arrify',0,1)])
                self.assertEqual(db.execute('SELECT title FROM queue').fetchone()[0],'Keep this request')
                self.assertIsNone(json.loads(db.execute('SELECT payload FROM apple_party_playback').fetchone()[0])['party_id'])
            with sqlite3.connect(backup) as db:self.assertEqual(db.execute('SELECT sum(ended) FROM parties').fetchone()[0],0)
            with self.assertRaises(FileExistsError):module.prepare(path,backup)

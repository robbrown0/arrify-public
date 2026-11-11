import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from app.accounts import Accounts
from app.native_auth import NativeSessions, NativeError
from app.local_presence import LocalPresence


class AccountDeletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.a = Accounts(self.tmp.name)
        for name in ('owner', 'other'):
            self.a.register(name, 'long-test-password', name+'@example.invalid')
            self.a.approve(name, admin=name == 'owner')
        self.uid = self.a.db.execute("SELECT id FROM users WHERE username='owner'").fetchone()[0]
    def tearDown(self):
        self.a.db.close(); self.tmp.cleanup()

    def test_erases_all_personal_data_and_revokes_every_session_without_harming_other_users(self):
        a = self.a; uid = self.uid
        token = a.login('owner', 'long-test-password')
        other = a.login('other', 'long-test-password')
        service = NativeSessions(a)
        pairs = [service.login(dict(username='owner', password='long-test-password', device_name=n)) for n in ('phone', 'tablet')]
        LocalPresence(a)
        a.db.execute('INSERT INTO native_output_sessions VALUES(?,?,?,?)', (uid, 'device', 1, pairs[0]['session_id']))
        a.db.execute('CREATE TABLE live_activities(family TEXT REFERENCES native_families(id) ON DELETE CASCADE, token TEXT)')
        a.db.execute('INSERT INTO live_activities VALUES(?,?)', (pairs[0]['session_id'], 'push-secret'))
        # Exercise modules that do not use foreign-key cascades, including future
        # user_id tables: erasure must not silently leave integration credentials.
        for table in ('spotify_links', 'apple_music_links', 'request_progress', 'request_notifications', 'request_events', 'sound_preferences'):
            a.db.execute('CREATE TABLE '+table+'(user_id INTEGER, payload TEXT)')
            a.db.execute('INSERT INTO '+table+' VALUES(?,?)', (uid, 'private'))
        a.db.execute('INSERT INTO favorites VALUES(?,1)', (uid,))
        a.db.execute("INSERT INTO station_preferences(user_id,items) VALUES(?,'[]')", (uid,))
        a.db.execute("INSERT INTO music_requests VALUES(?,'request',1,'{}')", (uid,))
        a.db.execute("INSERT INTO personal_playlists(user_id,name,visibility) VALUES(?,'Shared playlist','shared')", (uid,))
        a.db.execute("INSERT INTO history(user_id,track_id,played_at) VALUES(?,1,1)", (uid,))
        a.db.execute("INSERT INTO admin_audit(actor_id,target_id,action,created) VALUES(?,2,'approve',1)", (uid,))
        a.edit_search_history(uid, {'action':'remember','query':'private query'})
        a.enable_recovery('owner'); recovery = a.claim_recovery()
        folder = Path(self.tmp.name)/'playback-diagnostics';folder.mkdir();(folder/f'{uid}.json').write_text('private')
        a.delete_account('long-test-password', user_id=uid)
        self.assertIsNone(a.db.execute('SELECT 1 FROM users WHERE id=?', (uid,)).fetchone())
        for row in a.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            table = row[0]
            if 'user_id' in {r[1] for r in a.db.execute('PRAGMA table_info('+table+')')}:
                self.assertEqual(a.db.execute('SELECT count(*) FROM '+table+' WHERE user_id=?', (uid,)).fetchone()[0], 0, table)
        self.assertEqual(a.db.execute('SELECT count(*) FROM live_activities').fetchone()[0], 0)
        for pair in pairs:
            with self.assertRaises(PermissionError):a.identity(pair['access_token'], 'native')
            with self.assertRaises(NativeError):service.refresh(pair['refresh_token'])
        with self.assertRaises(PermissionError):a.identity(token, 'web')
        with self.assertRaises(PermissionError):a.check_recovery(recovery)
        self.assertEqual(a.identity(other, 'web')['username'], 'other')
        self.assertFalse((folder/f'{uid}.json').exists())
        with self.assertRaises(sqlite3.IntegrityError):a.db.execute('INSERT INTO favorites VALUES(?,2)', (uid,))
        with self.assertRaises(sqlite3.IntegrityError):a.db.execute("INSERT INTO spotify_links VALUES(?,'late credentials')", (uid,))
        a.register('owner', 'long-test-password', 'owner@example.invalid')
        self.assertGreater(a.db.execute("SELECT id FROM users WHERE username='owner'").fetchone()[0], uid)
        self.assertFalse(a.needs_setup())

    def test_wrong_password_does_not_modify_account_or_create_ledger(self):
        with self.assertRaises(PermissionError):self.a.delete_account('wrong', user_id=self.uid)
        self.assertFalse((Path(self.tmp.name)/'account-deletions.jsonl').exists())
        self.assertTrue(self.a.login('owner', 'long-test-password'))

    def test_pending_and_frozen_accounts_can_delete_without_approval(self):
        self.a.register('pending', 'long-test-password', 'pending@example.invalid')
        self.a.delete_account('long-test-password', username='pending')
        self.a.db.execute("UPDATE users SET frozen=1 WHERE username='other'")
        self.a.delete_account('long-test-password', username='other')
        self.assertEqual(self.a.db.execute('SELECT count(*) FROM users').fetchone()[0], 1)

    def test_old_database_restore_replays_erasure_and_never_reopens_setup(self):
        backup = Path(self.tmp.name)/'old.db'
        db = sqlite3.connect(backup); self.a.db.backup(db); db.close()
        self.a.delete_account('long-test-password', user_id=self.uid)
        self.a.db.close()
        db = sqlite3.connect(backup); target = sqlite3.connect(Path(self.tmp.name)/'accounts.db'); db.backup(target); target.close(); db.close()
        self.a = Accounts(self.tmp.name)
        self.assertIsNone(self.a.db.execute('SELECT 1 FROM users WHERE id=?', (self.uid,)).fetchone())
        self.assertFalse(self.a.needs_setup())
        ledger = (Path(self.tmp.name)/'account-deletions.jsonl').read_text()
        self.assertNotIn('owner', ledger); self.assertNotIn('password', ledger)

    def test_pending_deletion_does_not_disable_initial_setup(self):
        with tempfile.TemporaryDirectory() as d:
            a = Accounts(d); a.register('pending','long-test-password','pending@example.invalid')
            a.delete_account('long-test-password',username='pending'); a.db.close()
            a = Accounts(d)
            self.assertTrue(a.needs_setup()); a.db.close()

    def test_backup_scrubber_removes_only_deleted_accounts(self):
        from tools.erase_account_backups import scrub
        root = Path(self.tmp.name)/'backups'; root.mkdir()
        db = sqlite3.connect(root/'accounts.db'); self.a.db.backup(db); db.close()
        self.a.delete_account('long-test-password', user_id=self.uid)
        requests = Path(self.tmp.name)/'requests'; (requests/'inbox').mkdir(parents=True)
        item = requests/'inbox'/'one.json';item.write_text(json.dumps({'requested_by':self.uid,'selection':{'title':'Song'}}))
        self.assertEqual(scrub(self.tmp.name, root, requests), 1)
        self.assertNotIn('requested_by',json.loads(item.read_text()))
        with sqlite3.connect(root/'accounts.db') as db:
            self.assertEqual(db.execute('SELECT username FROM users').fetchall(), [('other',)])
            self.assertEqual(db.execute('SELECT email FROM profiles').fetchall(), [('other@example.invalid',)])
            self.assertEqual(db.execute('SELECT setup_complete FROM account_lifecycle').fetchone()[0], 1)
        self.assertEqual(scrub(self.tmp.name, root), 1)

    def test_owned_parties_and_guests_are_erased(self):
        a = self.a
        a.db.executescript("""
          CREATE TABLE parties(id TEXT PRIMARY KEY,owner INTEGER,name TEXT);
          CREATE TABLE party_guests(party_id TEXT,name TEXT);
          CREATE TABLE party_entries(party_id TEXT,title TEXT);
          CREATE TABLE party_acquisitions(party_id TEXT,metadata TEXT);
          CREATE TABLE party_wifi(party_id TEXT,mode TEXT);
          CREATE TABLE party_session_ids(id TEXT,owner INTEGER,token TEXT);
          CREATE TABLE guest_wifi_profiles(scope TEXT,profile TEXT);
        """)
        for pid,uid in [('mine',self.uid),('theirs',2)]:
            a.db.execute('INSERT INTO parties VALUES(?,?,?)',(pid,uid,'Party'))
            for table in ('party_guests','party_entries','party_acquisitions','party_wifi'):
                a.db.execute('INSERT INTO '+table+' VALUES(?,?)',(pid,'private'))
            a.db.execute('INSERT INTO party_session_ids VALUES(?,?,?)',(pid,uid,'secret'))
            a.db.execute('INSERT INTO guest_wifi_profiles VALUES(?,?)',('party:'+pid,'secret'))
            a.db.execute('INSERT INTO guest_wifi_profiles VALUES(?,?)',('user:'+str(uid),'secret'))
        a.delete_account('long-test-password',user_id=self.uid)
        for table in ('parties','party_guests','party_entries','party_acquisitions','party_wifi','party_session_ids'):
            self.assertEqual(a.db.execute('SELECT count(*) FROM '+table).fetchone()[0],1,table)
        self.assertEqual(a.db.execute('SELECT count(*) FROM guest_wifi_profiles').fetchone()[0],2)

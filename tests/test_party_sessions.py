import concurrent.futures
import json
import threading
import unittest
from unittest.mock import Mock,patch
import test_parties as fixtures
from app.parties import Parties
from app.party_sessions import PartyGone


class SecureSessionsTests(unittest.TestCase):
    setUp=fixtures.PartyTests.setUp
    tearDown=fixtures.PartyTests.tearDown
    add=fixtures.PartyTests.add
    command=fixtures.PartyTests.command

    def test_same_name_always_has_new_session_and_hashed_independent_capabilities(self):
        parties=[self.party]+[self.service.host(self.uid,data={'name':'Friday night'}) for _ in range(5)]
        for key in ('id','guest_url','display_url'):self.assertEqual(len({p[key] for p in parties}),6)
        for p in parties:
            token=p['guest_url'].split('#')[1];display=p['display_url'].split('#')[1]
            self.assertNotEqual(token,display);self.assertNotIn(p['id'],token)
            row=self.service.row(p['id'],self.uid)
            self.assertEqual(row['join_token'],self.service.invites.digest(token));self.assertNotIn(token,json.dumps(row))
            self.assertTrue(p['guest_url'].startswith('https://arrify.example/party/join#'))

    def test_end_revokes_stale_guest_and_cannot_reactivate(self):
        self.add(1);self.command('play',party_id=self.party['id'])
        ended=self.service.host(self.uid,self.party['id'],{'action':'end'})
        self.assertEqual(ended['state'],'ended');self.assertNotIn('guest_url',ended);self.assertNotIn('qr_url',ended)
        self.assertEqual(self.a.db.execute('SELECT count(*) FROM party_guests').fetchone()[0],0)
        for call in [lambda:self.add(2),lambda:self.service.join(self.token,'Late'),lambda:self.service.guest(self.raw,self.secret,self.csrf),lambda:self.command('play',party_id=self.party['id']),lambda:self.service.host(self.uid,self.party['id'],{'action':'toggle'})]:
            with self.assertRaises(ValueError):call()
        self.assertEqual(self.service.host(self.uid,self.party['id'],{'action':'end'}),ended)
        s=self.p.load(self.uid);self.assertNotIn('party_id',s);self.assertFalse(s['listening']['playing'])

    def test_delete_idempotent_scoped_and_same_name_never_restores_invitation(self):
        self.add(1)
        with self.assertRaises(ValueError):self.service.delete(self.uid,self.party['id'])
        with self.assertRaises(ValueError):self.service.delete(self.uid+1,self.party['id'],True)
        result=self.service.delete(self.uid,self.party['id'],True)
        self.assertEqual(self.service.delete(self.uid,self.party['id']),result)
        for table in ('parties','party_guests','party_entries','party_acquisitions'):
            column='id' if table=='parties' else 'party_id'
            self.assertEqual(self.a.db.execute('SELECT count(*) FROM '+table+' WHERE '+column+'=?',(self.party['id'],)).fetchone()[0],0)
        self.assertEqual(self.a.db.execute('SELECT count(*) FROM users').fetchone()[0],1)
        with self.assertRaises(PartyGone):self.service.join(self.token,'Old invite')
        new=self.service.host(self.uid,data={'name':'Friday night'})
        self.assertNotEqual(new['id'],self.party['id']);self.assertNotEqual(new['guest_url'],self.party['guest_url'])

    def test_reserved_deleted_identifier_cannot_be_reused(self):
        self.service.delete(self.uid,self.party['id'],True)
        with patch('app.parties.secrets.token_hex',side_effect=[self.party['id'],'b'*32]):new=self.service.host(self.uid,data={'name':'Friday night'})
        self.assertEqual(new['id'],'b'*32)

    def test_legacy_plaintext_invitations_expire_without_deleting_history_or_settings(self):
        self.add(1)
        with self.a.lock:self.a.db.execute("UPDATE parties SET invitation_version=0,join_token=?,display_token=?,slot_limit=5 WHERE id=?",('x'*32,'y'*32,self.party['id']))
        migrated=Parties(self.a,self.p,origin='https://arrify.example')
        p=migrated.host(self.uid,self.party['id']);self.assertTrue(p['ended']);self.assertEqual(p['slot_limit'],5)
        self.assertEqual(self.a.db.execute('SELECT count(*) FROM party_entries').fetchone()[0],1)
        with self.assertRaises(PartyGone):migrated.join('x'*32,'Guest')
        self.assertEqual(self.a.db.execute('SELECT length(join_token) FROM parties').fetchone()[0],64)

    def test_restart_keeps_new_invitation_valid(self):
        again=Parties(self.a,self.p,origin='https://arrify.example')
        self.assertEqual(again.host(self.uid,self.party['id'])['guest_url'],self.party['guest_url'])
        self.assertFalse(again.row(token=self.token)['ended'])
    def test_missing_derivation_key_expires_invitations_instead_of_reissuing_them(self):
        (self.a.directory/'party-invitations.key').unlink()
        again=Parties(self.a,self.p,origin='https://arrify.example')
        self.assertTrue(again.host(self.uid,self.party['id'])['ended'])
        with self.assertRaises(PartyGone):again.join(self.token,'Guest')

    def test_remote_http_is_rejected_and_lan_only_http_is_preserved(self):
        with self.assertRaisesRegex(ValueError,'require HTTPS'):Parties(self.a,self.p,origin='http://public.example.com')
        local=Parties(self.a,self.p,origin='http://192.168.1.2:8080')
        self.assertTrue(local.host(self.uid,data={'name':'Local'})['guest_url'].startswith('http://192.168.1.2:8080/party/join#'))

    def test_concurrent_end_and_writes_cannot_leave_an_active_queue(self):
        gate=threading.Barrier(9)
        def write(i):
            gate.wait()
            try:self.add(i)
            except ValueError:pass
        def end():gate.wait();self.service.host(self.uid,self.party['id'],{'action':'end'})
        with concurrent.futures.ThreadPoolExecutor(max_workers=9) as pool:
            futures=[pool.submit(write,i) for i in range(1,9)]+[pool.submit(end)]
            for f in futures:f.result()
        self.assertEqual(self.a.db.execute("SELECT count(*) FROM party_entries WHERE status IN ('queued','playing')").fetchone()[0],0)
        with self.assertRaises(ValueError):self.add(12)

    def test_fifo_and_play_next_do_not_restart_current_track(self):
        for i in (1,2,3):self.add(i)
        self.command('play',party_id=self.party['id']);before=self.p.load(self.uid)
        p=self.service.host(self.uid,self.party['id']);self.assertEqual([x['id'] for x in p['items']],[1,2,3])
        self.service.host(self.uid,self.party['id'],{'action':'play_next','entry_id':p['items'][2]['entry_id']})
        after=self.p.load(self.uid);self.assertEqual(after['current'],1);self.assertEqual(after['upcoming'],[3,2]);self.assertEqual(after['listening']['intent'],before['listening']['intent'])

    def test_submission_finishing_after_end_does_not_repopulate_session(self):
        entered=threading.Event();release=threading.Event();submissions=Mock()
        def submit(*args):entered.set();release.wait(5);return {'id':'request','title':'Song','artist':'Artist'}
        submissions.create.side_effect=submit
        with concurrent.futures.ThreadPoolExecutor() as pool:
            work=pool.submit(self.service.request,self.token,self.secret,self.csrf,'selection',submissions)
            self.assertTrue(entered.wait(5));self.service.delete(self.uid,self.party['id'],True);release.set()
            with self.assertRaises(ValueError):work.result()
        self.assertEqual(self.a.db.execute('SELECT count(*) FROM party_acquisitions').fetchone()[0],0)

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
from app.accounts import Accounts
from app.library import Player,PlaybackConflict
from app.listening import Listening
from app.parties import Parties,qr_svg

class Library:
    def __init__(self):self.tracks={i:{'id':i,'title':f'Song {i}','artist':'Artist','album':'Album'} for i in range(1,30)}
    def track(self,i):
        if i not in self.tracks:raise ValueError('Unavailable song.')
        return self.tracks[i]

class PartyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.a=Accounts(self.tmp.name)
        self.a.register('partyhost','a-strong-password',email='party@example.invalid');self.a.approve('partyhost')
        self.uid=self.a.db.execute("SELECT id FROM users WHERE username='partyhost'").fetchone()[0]
        self.p=Player(self.a,Library());self.service=Parties(self.a,self.p,origin='https://arrify.example')
        self.party=self.service.host(self.uid,data={'name':'Friday night'})
        self.token=self.party['guest_url'].split('#')[1]
        self.raw,self.guest,self.secret=self.service.join(self.token,'Alex');self.csrf=self.guest['csrf']
        self.l=Listening(self.p)
    def tearDown(self):self.a.db.close();self.tmp.cleanup()
    def add(self,i):return self.service.add(self.token,self.secret,self.csrf,i)
    def command(self,action,**data):return self.l.command(self.uid,{'device_id':'a'*32,'expected_revision':self.l.state(self.uid)['listening']['revision'],'action':action,**data})
    def test_host_is_account_owned_guest_credentials_cannot_manage(self):
        with self.assertRaises(ValueError):self.service.host(self.uid+1,self.party['id'],{'action':'end'})
        with self.assertRaises(PermissionError):self.service.add(self.token,'wrong',self.csrf,1)
        with self.assertRaises(PermissionError):self.service.add(self.token,self.secret,'wrong',1)
        with self.assertRaises(ValueError):self.service.row(token=self.party['display_url'].split('#')[1])
    def test_three_initial_two_played_refill_two_and_refresh_cannot_reset(self):
        for i in (1,2,3):self.add(i)
        with self.assertRaises(ValueError):self.add(4)
        first=self.command('play',party_id=self.party['id']);self.assertEqual(first['current'],1)
        self.command('next',automatic=True,owner_epoch=first['listening']['epoch']);self.assertEqual(self.service.quota(self.raw,self.guest['id'])['available'],0)
        self.command('next',automatic=True,owner_epoch=first['listening']['epoch']);self.assertEqual(self.service.quota(self.raw,self.guest['id'])['available'],2)
        p,g,issued=self.service.join(self.token,'A new name',self.secret);self.assertIsNone(issued);self.assertEqual(g['id'],self.guest['id'])
        self.add(4);self.add(5)
        with self.assertRaises(ValueError):self.add(6)
    def test_duplicate_from_another_guest_and_simultaneous_writes(self):
        _,other,secret=self.service.join(self.token,'Sam')
        self.add(1)
        with self.assertRaisesRegex(ValueError,'Already in queue'):self.service.add(self.token,secret,other['csrf'],1)
        def add(i):
            try:self.add(i);return True
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=8) as workers:results=list(workers.map(add,range(2,10)))
        self.assertEqual(sum(results),2);self.assertEqual(self.service.quota(self.raw,self.guest['id'])['pending'],3)
    def test_queue_changes_preserve_current_and_playback_intent(self):
        self.add(1);self.add(2);first=self.command('play',party_id=self.party['id']);intent=first['listening']['intent']
        after=self.add(3);state=self.l.state(self.uid)
        self.assertEqual(state['current'],1);self.assertEqual(state['upcoming'],[2,3]);self.assertEqual(state['listening']['intent'],intent)
        entry=next(x for x in after['items'] if x['id']==3)['entry_id']
        self.service.host(self.uid,self.party['id'],{'action':'move','entry_id':entry,'direction':-1})
        self.assertEqual(self.l.state(self.uid)['upcoming'],[3,2]);self.assertEqual(self.l.state(self.uid)['current'],1)
    def test_transition_duplicate_does_not_consume_two_guest_credits(self):
        for i in (1,2,3):self.add(i)
        first=self.command('play',party_id=self.party['id']);data={'automatic':True,'owner_epoch':first['listening']['epoch'],'transition_id':'unique-transition','expected_current':1,'expected_next':2}
        self.command('next',**data);self.command('next',**data)
        self.assertEqual(self.service.quota(self.raw,self.guest['id'])['pending'],2)
    def test_normal_queue_controls_and_host_removals_return_credits(self):
        for i in (1,2,3):self.add(i)
        self.command('play',party_id=self.party['id']);self.command('remove',index=0)
        self.assertEqual(self.service.quota(self.raw,self.guest['id'])['available'],0)
        self.command('clear');self.assertEqual(self.service.quota(self.raw,self.guest['id'])['available'],2)
    def test_exhausted_active_party_restarts_on_new_guest_song(self):
        self.add(1);first=self.command('play',party_id=self.party['id']);self.command('next',automatic=True,owner_epoch=first['listening']['epoch'])
        self.assertIsNone(self.l.state(self.uid)['current']);self.add(2)
        state=self.l.state(self.uid);self.assertEqual(state['current'],2);self.assertTrue(state['listening']['playing'])
    def test_switching_to_regular_music_detaches_party_without_replacing_it(self):
        self.add(1);self.add(2);self.command('play',party_id=self.party['id']);self.command('play',ids=[9,10]);self.add(3)
        state=self.l.state(self.uid);self.assertEqual(state['current'],9);self.assertEqual(state['upcoming'],[10]);self.assertNotIn('party_id',state)
    def test_limits_validation_and_closed_and_ended_lifecycle(self):
        with self.assertRaises(ValueError):self.service.host(self.uid,self.party['id'],{'action':'settings','slot_limit':3,'refill':4})
        self.service.host(self.uid,self.party['id'],{'action':'settings','slot_limit':5,'refill':2});self.assertEqual(self.add(1)['quota']['limit'],5)
        self.service.host(self.uid,self.party['id'],{'action':'toggle'})
        with self.assertRaises(ValueError):self.add(2)
        self.service.host(self.uid,self.party['id'],{'action':'end'})
        with self.assertRaises(ValueError):self.command('play',party_id=self.party['id'])
    def test_display_shows_party_name_without_private_guest_credentials(self):
        self.add(1)
        with self.a.transaction():guest=self.service.view(self.raw,self.guest['id']);display=self.service.view(self.raw,display=True)
        self.assertEqual(display['items'][0]['requested_by'],'Requested by Alex');self.assertNotIn('requested_by',guest['items'][0]);self.assertNotIn('guest',guest['items'][0]);self.assertNotIn('guest_id',display['items'][0]);self.assertNotIn('display_url',guest)
        host=self.service.host(self.uid,self.party['id']);self.assertEqual(host['items'][0]['guest'],'Alex')
    def test_missing_music_request_does_not_use_credit_and_arrival_auto_queues(self):
        with self.a.lock:self.a.db.execute('CREATE TABLE IF NOT EXISTS request_progress(user_id INTEGER,request_id TEXT,payload TEXT,PRIMARY KEY(user_id,request_id))')
        submit=Mock();submit.create.return_value={'id':'r','title':'Song 9','artist':'Artist','album':'Album'}
        out=self.service.request(self.token,self.secret,self.csrf,'selection',submit)
        self.assertIn('without using',out['message']);self.assertEqual(self.service.quota(self.raw,self.guest['id'])['available'],3)
        self.service.request(self.token,self.secret,self.csrf,'selection',submit);self.assertEqual(submit.create.call_count,1)
        with self.a.lock:self.a.db.execute('INSERT INTO request_progress VALUES(?,?,?)',(self.uid,'r',json.dumps({'state':'available','track_id':9})))
        host=self.service.host(self.uid,self.party['id']);self.assertEqual(host['items'][0]['id'],9);self.assertEqual(host['items'][0]['guest'],'Alex')
        self.assertEqual(self.service.quota(self.raw,self.guest['id'])['available'],3)
    def test_durable_reopen_and_qr_svg_is_local(self):
        self.add(1);again=Parties(self.a,self.p,origin='https://arrify.example')
        self.assertEqual(again.host(self.uid,self.party['id'])['items'][0]['title'],'Song 1')
        svg=qr_svg(self.party['guest_url']);self.assertIn(b'<svg',svg);self.assertNotIn(b'https://',svg)

    def test_arrival_worker_runs_when_host_is_on_another_screen(self):
        with self.a.lock:self.a.db.execute('CREATE TABLE IF NOT EXISTS request_progress(user_id INTEGER,request_id TEXT,payload TEXT,PRIMARY KEY(user_id,request_id))')
        self.add(1);first=self.command('play',party_id=self.party['id'])
        submit=Mock();submit.create.return_value={'id':'ready','title':'Song 9','artist':'Artist','album':'Album'}
        self.service.request(self.token,self.secret,self.csrf,'another-selection',submit)
        with self.a.lock:self.a.db.execute('INSERT INTO request_progress VALUES(?,?,?)',(self.uid,'ready',json.dumps({'state':'available','track_id':9})))
        self.service.refresh_arrivals()
        state=self.l.state(self.uid);self.assertEqual(state['current'],1);self.assertEqual(state['upcoming'],[9]);self.assertEqual(state['listening']['intent'],first['listening']['intent'])
        self.service.refresh_arrivals();self.assertEqual(self.l.state(self.uid)['upcoming'],[9])

    def test_unconfirmed_submission_can_retry_after_interrupted_worker(self):
        import time
        with self.a.lock:self.a.db.execute("INSERT INTO party_acquisitions(party_id,guest_id,selection,metadata,created) VALUES(?,?,?,?,?)",(self.party['id'],self.guest['id'],'pending-selection','{}',int(time.time())-130))
        submit=Mock();submit.create.return_value={'id':'retried','title':'Song 9','artist':'Artist','album':'Album'}
        self.service.request(self.token,self.secret,self.csrf,'pending-selection',submit)
        submit.create.assert_called_once()
        self.assertEqual(self.a.db.execute("SELECT state FROM party_acquisitions WHERE selection='pending-selection'").fetchone()[0],'requested')

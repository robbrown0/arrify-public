import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from app.accounts import Accounts
from app.library import Player,PlaybackConflict
from app.listening import Listening
from app.parties import Parties
from test_parties import Library


class Catalog:
    def storefront(self,uid):return 'us'
    def track(self,region,sid):return {'id':str(sid),'title':'Song '+str(sid),'artist':'Artist','album':'Album','provider':'apple','isrc':'ISRC'+str(sid)}


class ApplePartyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.a=Accounts(self.tmp.name)
        self.a.db.execute("INSERT INTO users(id,username,password,approved,created) VALUES(1,'host','!fixture',1,1)")
        self.lib=Library();self.player=Player(self.a,self.lib);self.catalog=Catalog();self.s=Parties(self.a,self.player,origin='https://arrify.example',apple=self.catalog)
        self.party=self.s.host(1,data={'name':'Apple party','source':'apple'});self.pid=self.party['id'];self.raw=self.s.row(self.pid,1)
        self.token=self.party['guest_url'].split('#')[1];_,self.g,self.secret=self.s.join(self.token,'Guest')
        self.now=1000;self.s.apple_playback.now=lambda:self.now;self.device='a'*32
    def tearDown(self):self.a.db.close();self.tmp.cleanup()
    def add(self,sid):return self.s.add_apple(1,self.pid,self.catalog.track('us',str(sid)),self.token,self.secret,self.g['csrf'])
    def command(self,action,**data):
        state=self.s.apple_playback.load(1)
        return self.s.apple_playback.command(1,self.pid,{'action':action,'device_id':self.device,'epoch':state['epoch'],'revision':state['revision'],'entry_id':state['entry_id'],**data})
    def playing(self,seq=1):return self.command('heartbeat',sequence=seq,observed='playing',position=1,duration=180)
    def test_preview_does_not_complete_entry_or_refund_guest_credit(self):
        self.s.add_apple(1,self.pid,self.catalog.track('us','1')|{'duration':228},self.token,self.secret,self.g['csrf'])
        self.add(2);self.command('start')
        self.command('heartbeat',sequence=1,observed='playing',position=29,duration=30)
        before=self.s.quota(self.raw,self.g['id'])
        entry=self.s.apple_playback.load(1)['entry_id']
        with self.assertRaisesRegex(ValueError,'short preview'):self.command('next',automatic=True)
        self.assertEqual(self.s.apple_playback.load(1)['entry_id'],entry)
        self.assertEqual(self.s.quota(self.raw,self.g['id']),before)
        self.command('next',automatic=False)
        self.assertNotEqual(self.s.apple_playback.load(1)['entry_id'],entry)

    def test_source_is_fixed_and_default_local_parties_keep_working(self):
        local=self.s.host(1,data={'name':'Local'});self.assertEqual(local['source'],'arrify')
        self.s.host(1,local['id'],{'action':'add','track_id':1})
        with self.assertRaises(ValueError):self.s.host(1,self.pid,{'source':'arrify','action':'settings'})
        with self.assertRaises(ValueError):self.s.host(1,self.pid,{'action':'add','track_id':1})
        with self.assertRaises(ValueError):self.s.add_apple(1,local['id'],self.catalog.track('us','1'))
        with self.assertRaises(ValueError):self.s.request(self.token,self.secret,self.g['csrf'],'selection',None)
    def test_previous_song_is_an_uncharged_replay_without_changing_guest_credits(self):
        for i in (1,2,3):self.add(i)
        self.command('start');self.playing();self.command('next',automatic=True);self.playing()
        before=self.s.quota(self.raw,self.g['id'])
        p=self.command('previous')
        self.assertEqual([t['id'] for t in p['items']],['1','2','3'])
        self.assertEqual(self.s.quota(self.raw,self.g['id']),before)
        self.playing();self.command('next',automatic=True)
        self.assertEqual(self.s.quota(self.raw,self.g['id']),before)
        self.assertEqual(self.s.view(self.raw,host=True)['items'][0]['id'],'2')
    def test_quota_duplicate_and_playing_confirmation_before_automatic_credit(self):
        for i in (1,2,3):self.add(i)
        with self.assertRaises(ValueError):self.add(4)
        with self.assertRaisesRegex(ValueError,'Already in queue'):self.add(1)
        first=self.command('start');entry=first['apple_playback']['entry_id'];epoch=first['apple_playback']['epoch']
        self.assertFalse(first['playing'])
        with self.assertRaisesRegex(ValueError,'not confirmed'):self.command('next',automatic=True)
        heartbeat=self.playing();self.assertTrue(heartbeat['_partial']);self.assertNotIn('items',heartbeat);next1=self.command('next',automatic=True)
        self.assertEqual(self.s.quota(self.raw,self.g['id'])['available'],0)
        with self.assertRaises(PlaybackConflict):self.command('next',entry_id=entry,epoch=epoch,automatic=True)
        self.playing();self.command('next',automatic=True)
        self.assertEqual(self.s.quota(self.raw,self.g['id'])['available'],2)
        self.assertEqual(self.a.db.execute('SELECT count(*) FROM apple_music_history').fetchone()[0],2)
    def test_stale_device_and_duplicate_heartbeats_cannot_change_current(self):
        self.add(1);self.command('start');self.playing(2)
        self.command('heartbeat',sequence=1,observed='paused',position=0,duration=180)
        self.assertTrue(self.s.view(self.raw,host=True)['playing'])
        with self.assertRaises(PlaybackConflict):self.command('start',device_id='b'*32)
        self.command('start',device_id='b'*32,takeover=True)
        with self.assertRaises(PlaybackConflict):self.playing(3)
        self.now+=30;self.assertFalse(self.s.view(self.raw)['playing'])
    def test_os_pause_and_resume_observations_preserve_playback_intent(self):
        self.add(1);self.command('start');self.playing()
        self.command('heartbeat',sequence=2,observed='paused',position=3,duration=180)
        self.assertFalse(self.s.apple_playback.load(1)['desired_playing'])
        self.playing(3);self.assertTrue(self.s.apple_playback.load(1)['desired_playing'])
    def test_natural_end_keeps_queue_running_and_stale_completion_cannot_override_pause(self):
        self.add(1);self.add(2);self.command('start');self.playing()
        self.command('heartbeat',sequence=2,observed='paused',position=180,duration=180)
        self.assertTrue(self.s.apple_playback.load(1)['desired_playing'])
        p=self.command('next',automatic=True);self.assertTrue(p['apple_playback']['desired_playing'])
        self.playing();revision=self.s.apple_playback.load(1)['revision'];self.command('pause')
        with self.assertRaises(PlaybackConflict):self.command('next',automatic=True,revision=revision)
        with self.assertRaises(PlaybackConflict):self.command('next',automatic=True)
        self.assertFalse(self.s.apple_playback.load(1)['desired_playing'])

    def test_empty_started_party_accepts_first_arrival_and_resumes_after_exhaustion(self):
        p=self.command('start');self.assertIsNone(p['apple_playback']['entry_id']);self.assertTrue(p['apple_playback']['desired_playing'])
        self.add(1);self.assertIsNotNone(self.s.apple_playback.load(1)['entry_id']);self.playing();self.command('next',automatic=True)
        self.assertIsNone(self.s.apple_playback.load(1)['entry_id']);self.add(2)
        p=self.s.view(self.raw,host=True);self.assertEqual(p['items'][0]['id'],'2');self.assertTrue(p['apple_playback']['desired_playing'])

    def test_old_heartbeat_cannot_overwrite_a_new_control(self):
        self.add(1);first=self.command('start');revision=first['apple_playback']['revision'];self.command('pause')
        with self.assertRaises(PlaybackConflict):self.command('heartbeat',sequence=1,revision=revision,observed='playing',position=1,duration=180)
        self.assertFalse(self.s.view(self.raw,host=True)['playing'])

    def test_guest_and_tv_views_never_expose_device_lease(self):
        self.add(1);self.command('start');self.playing()
        for kwargs in ({'gid':self.g['id']},{'display':True}):
            text=json.dumps(self.s.view(self.raw,**kwargs));self.assertNotIn('apple_playback',text);self.assertNotIn(self.device,text)
    def test_queue_edits_do_not_restart_current_and_empty_queue_resumes_on_arrival(self):
        self.add(1);self.add(2);first=self.command('start');d=first['apple_playback'];self.playing()
        queued=next(t for t in first['items'] if t['status']=='queued')
        self.s.host(1,self.pid,{'action':'remove','entry_id':queued['entry_id']})
        self.assertEqual(self.s.apple_playback.load(1)['entry_id'],d['entry_id'])
        last=self.command('next',automatic=True);self.assertIsNone(last['apple_playback']['entry_id'])
        self.command('heartbeat',sequence=1,observed='paused',position=0,duration=0)
        self.assertTrue(self.s.apple_playback.load(1)['desired_playing'])
        self.add(3);self.assertIsNotNone(self.s.apple_playback.load(1)['entry_id'])
    def test_local_and_apple_playback_are_mutually_exclusive(self):
        listening=Listening(self.player);state=listening.command(1,{'action':'play','ids':[1],'device_id':self.device,'expected_revision':0})
        old_epoch=state['listening']['epoch'];self.add(1);self.command('start')
        self.assertFalse(self.player.load(1)['listening']['playing']);self.assertGreater(self.player.load(1)['listening']['epoch'],old_epoch)
        state=listening.state(1);listening.command(1,{'action':'resume','device_id':self.device,'expected_revision':state['listening']['revision']})
        self.assertIsNone(self.s.apple_playback.load(1)['party_id'])
    def test_admin_active_streams_include_apple_without_invented_audio_transfers(self):
        from app.connections import connected_users
        self.lib.index_lock=threading.RLock()
        self.a.db.execute("INSERT INTO sessions(digest,user_id,kind,expires) VALUES('fixture',1,'web',?)",(self.now+600,))
        self.a.db.execute('UPDATE users SET last_seen=? WHERE id=1',(self.now,))
        self.add(1);self.command('start');self.playing()
        d=connected_users(self.a,self.lib,{'admin':True},now=self.now)
        self.assertEqual(d['summary']['playing'],1);self.assertEqual(d['summary']['audio_requests'],0)
        self.assertEqual(d['items'][0]['quality'],'Apple Music');self.assertEqual(d['items'][0]['track']['title'],'Song 1')
        self.assertEqual(d['items'][0]['tracks_started'],1)

    def test_ending_party_revokes_apple_playback(self):
        self.add(1);self.command('start');self.playing();self.s.host(1,self.pid,{'action':'end'})
        with self.assertRaises(PermissionError):self.playing(2)

    def test_queue_limits_and_input_validation_cannot_be_bypassed(self):
        self.add(1);self.command('start')
        with self.assertRaises(ValueError):self.command('heartbeat',sequence=1,observed='playing',position=float('nan'),duration=180)
        with self.assertRaises(ValueError):self.command('heartbeat',sequence=1,observed='invented',position=0,duration=180)
        with self.assertRaises(ValueError):self.s.apple_playback.command(2,self.pid,{'action':'start','device_id':self.device})

import io
import json
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from app.accounts import Accounts
from app.connections import connected_users,ActiveStreams
from app.server import Handler,Error

class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.a=Accounts(self.tmp.name);self.now=200000
        for uid in range(1,5):
            self.a.db.execute('INSERT INTO users(id,username,password,approved,admin,created,last_seen) VALUES(?,?,?,1,?,1,?)',(uid,'user'+str(uid),'!fixture',uid==1,self.now-30 if uid<3 else self.now-600))
            self.a.db.execute('INSERT INTO sessions(digest,user_id,kind,expires) VALUES(?,?,?,?)',('secret'+str(uid),uid,'web',self.now+100 if uid<4 else self.now-1))
        self.lib=SimpleNamespace(index_lock=threading.RLock(),tracks={10:{'id':10,'title':'Fixture song','artist':'Fixture artist'}})
        self.streams=ActiveStreams(now=lambda:self.now)
        self.d={'owner':'a'*32,'epoch':1,'intent':4,'ack':4,'seen':self.now-5,'observed':'playing','device_name':'iPhone','output':'AirPlay','quality':'Lossless'}
        self.state={'current':10,'upcoming':[11,12],'listening':self.d}
        self.save()
    def tearDown(self):self.a.db.close();self.tmp.cleanup()
    def save(self):self.a.db.execute('INSERT OR REPLACE INTO player VALUES(2,?)',(json.dumps(self.state),))
    def snapshot(self):return connected_users(self.a,self.lib,{'admin':True},now=self.now,streams=self.streams)
    def test_active_streams_and_recent_presence_are_distinct_from_saved_sessions(self):
        self.a.db.execute('INSERT INTO history(user_id,track_id,played_at) VALUES(2,10,?)',(self.now-10,))
        self.a.db.execute('INSERT INTO history(user_id,track_id,played_at) VALUES(2,10,?)',(self.now-90000,))
        key=self.streams.open(2,10,'original');self.streams.sending(key)
        d=self.snapshot();u=next(u for u in d['items'] if u['id']==2)
        self.assertEqual(d['summary']['playing'],1);self.assertEqual(d['summary']['online'],2)
        self.assertEqual(d['summary']['signed_in'],3);self.assertEqual(d['summary']['audio_requests'],1)
        self.assertEqual((u['tracks_started'],u['tracks_started_24h'],u['queue_count']),(2,1,2))
        self.assertEqual(u['device'],'iPhone');self.assertEqual(u['track']['id'],10)
        self.streams.close(key);self.assertEqual(self.snapshot()['summary']['audio_requests'],0)
        self.assertEqual(self.snapshot()['summary']['playing'],1) # buffered playback continues
        encoded=json.dumps(d);self.assertNotIn('secret2',encoded);self.assertNotIn('a'*32,encoded);self.assertNotIn('player_state',encoded)
    def test_stale_or_replaced_player_does_not_look_active(self):
        self.d['seen']=self.now-60;self.save()
        self.a.db.execute('INSERT INTO listening_status VALUES(2,?)',(json.dumps({**self.d,'owner':'b'*32,'seen':self.now,'current':10}),))
        u=next(u for u in self.snapshot()['items'] if u['id']==2)
        self.assertEqual(u['playback'],'offline');self.assertIsNone(u['track'])
        self.a.db.execute('UPDATE listening_status SET status=? WHERE user_id=2',(json.dumps({**self.d,'seen':self.now,'current':10}),))
        self.assertEqual(self.snapshot()['summary']['playing'],1)
    def test_buffer_report_stays_timestamped_and_never_crosses_owner(self):
        report={'state':'active','downloaded_bytes':10,'items':[],'player_seconds':4,'limit_bytes':100}
        self.a.db.execute('INSERT INTO listening_status VALUES(2,?)',(json.dumps({**self.d,'current':10,'buffer':report,'buffer_at':self.now-7}),))
        self.now+=60
        u=next(u for u in self.snapshot()['items'] if u['id']==2)
        self.assertEqual(u['playback'],'offline');self.assertEqual(u['buffer'],report);self.assertEqual(u['buffer_at'],self.now-67)
        self.d['owner']='b'*32;self.save()
        u=next(u for u in self.snapshot()['items'] if u['id']==2)
        self.assertIsNone(u['buffer']);self.assertEqual(u['buffer_at'],0)

    def test_pending_intent_is_buffering_and_frozen_or_expired_sessions_excluded(self):
        self.d['ack']=3;self.save();self.assertEqual(self.snapshot()['summary']['buffering'],1)
        self.a.db.execute('UPDATE users SET frozen=1 WHERE id=2')
        self.assertEqual({u['id'] for u in self.snapshot()['items']},{1,3})
    def test_non_admin_route_and_snapshot_denied(self):
        with self.assertRaises(PermissionError):connected_users(self.a,self.lib,{'admin':False},now=self.now)
        h=Handler.__new__(Handler);h.path='/api/v1/admin/connections';h.server=SimpleNamespace(accounts=self.a,library=self.lib,player=None);h.identity=lambda:{'id':2,'admin':False}
        with self.assertRaises(Error) as e:h.get()
        self.assertEqual(e.exception.status,403)
    def test_audio_transfer_tracker_cleans_up_on_failure_and_does_not_count_head(self):
        for command in ('GET','HEAD'):
            h=Handler.__new__(Handler);h.command=command;h.headers={};h.identity=lambda:{'id':2};h.server=SimpleNamespace(active_streams=self.streams)
            def fail(*args):
                self.assertEqual(len(self.streams.snapshot()['items']),0 if command=='HEAD' else 1)
                raise BrokenPipeError()
            h._stream=fail
            with self.assertRaises(BrokenPipeError):h.stream(10,{'profile':['original']})
            self.assertEqual(self.streams.snapshot()['items'],[])

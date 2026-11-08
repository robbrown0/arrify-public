import http.client
import json
from pathlib import Path
import time
import unittest
from types import SimpleNamespace
from urllib.parse import urlencode
from test_party_http import PartyHTTPTests
from app.listening import Listening
from app.connections import ActiveStreams
from app.stream_grants import StreamGrants

class StreamGrantHTTPTests(unittest.TestCase):
    def setUp(self):
        PartyHTTPTests.setUp(self)
        self.s.listening=Listening(self.p)
        self.s.stream_grants=StreamGrants(self.a,self.p,Path(self.tmp.name)/'secrets')
        self.s.active_streams=ActiveStreams()
        self.audio=Path(self.tmp.name)/'track.mp3';self.audio.write_bytes(b'fixture-audio-bytes')
        self.lib.music=SimpleNamespace(_track_file=lambda tid:({'mime_type':'audio/mpeg'},str(self.audio)))
        self.s.sound=SimpleNamespace(status=lambda tid,mode:{'gain_db':0,'status':'off'})
        self.state=self.cmd('play',ids=[1,2,3])
    tearDown=PartyHTTPTests.tearDown
    call=PartyHTTPTests.call
    def cmd(self,action,**kwargs):
        d=getattr(self,'state',{}).get('listening',{})
        code,body,_=self.call('/api/v1/listening',{'action':action,'device_id':'a'*32,'device_name':'iPhone','owner_epoch':d.get('epoch',0),'expected_revision':d.get('revision',0),**kwargs},self.hostcookie,self.a.csrf(self.session))
        self.assertEqual(code,200)
        self.state=json.loads(body);return self.state
    def url(self,tid=1,token=None,**kwargs):
        grant=self.state['stream_grants'][str(tid)]['token'] if token is None else token
        return '/api/v1/stream/'+str(tid)+'?'+urlencode({'grant':grant,'device_id':'a'*32,'owner_epoch':self.state['listening']['epoch'],**kwargs})
    def raw(self,path,method='GET',**headers):
        c=http.client.HTTPConnection('127.0.0.1',self.s.server_port,timeout=5)
        c.request(method,path,headers=headers);r=c.getresponse();result=r.status,r.read(),dict(r.getheaders());c.close();return result
    def test_receiver_get_head_and_ranges_without_cookie(self):
        self.assertEqual(set(self.state['stream_grants']),{'1','2'})
        url=self.url();status,body,headers=self.raw(url)
        self.assertEqual(status,200);self.assertEqual(body,self.audio.read_bytes());self.assertEqual(headers['Cache-Control'],'private, no-store')
        self.assertEqual(self.raw(url,'HEAD')[:2],(200,b''))
        status,body,headers=self.raw(url,Range='bytes=3-8');self.assertEqual((status,body),(206,self.audio.read_bytes()[3:9]));self.assertEqual(headers['Content-Range'],'bytes 3-8/19')
        self.assertEqual(self.raw(url,Range='bytes=999-')[0],416)
        self.assertEqual(self.raw('/api/v1/stream/1')[0],403)
    def test_resume_validator_preserves_unchanged_audio_and_rejects_changed_bytes(self):
        url=self.url();_,original,headers=self.raw(url);etag=headers['ETag']
        self.assertEqual(self.raw(url,'HEAD')[2]['ETag'],etag)
        status,body,resumed=self.raw(url,Range='bytes=4-',**{'If-Range':etag})
        self.assertEqual((status,body),(206,original[4:]));self.assertEqual(resumed['ETag'],etag)
        self.audio.write_bytes(b'changed-audio-bytes!')
        status,body,changed=self.raw(url,Range='bytes=4-',**{'If-Range':etag})
        self.assertEqual((status,body),(200,self.audio.read_bytes()));self.assertNotEqual(changed['ETag'],etag)
        self.assertEqual(self.raw(url,Range='bytes=4-',**{'If-Range':'W/'+changed['ETag']})[0],200)

    def test_no_api_login_or_other_track_privilege(self):
        token=self.state['stream_grants']['1']['token']
        self.assertEqual(self.raw('/api/v1/me?grant='+token)[0],403)
        self.assertEqual(self.raw('/api/v1/me',Authorization='Bearer '+token)[0],403)
        self.assertEqual(self.raw(self.url(2,token))[0],403)
        self.assertEqual(self.raw(self.url(3,token))[0],403)
        self.assertEqual(self.raw(self.url(token=token[:-1]+('A' if token[-1]!='A' else 'B')))[0],403)
        self.assertEqual(self.raw(self.url(device_id='b'*32))[0],403)
        self.assertEqual(self.raw(self.url(owner_epoch=99))[0],403)
        self.assertEqual(self.raw(self.url()+'&grant=duplicate')[0],403)
    def test_expiry_new_grant_and_continuing_connection_revocation(self):
        old=self.url();claims=self.state['stream_grants']['1'];self.s.stream_grants.now=lambda:claims['expires']
        self.assertEqual(self.raw(old)[0],403)
        self.assertEqual(self.s.stream_grants.identity(claims['token'],1,'a'*32,self.state['listening']['epoch'],False),{'id':1})
        self.cmd('resume');self.assertEqual(self.raw(self.url())[0],200)
        with self.a.transaction():self.a.db.execute('DELETE FROM sessions WHERE digest=?',(self.a.digest(self.session),))
        self.assertEqual(self.raw(self.url())[0],403)
        with self.assertRaises(PermissionError):self.s.stream_grants.identity(claims['token'],1,'a'*32,self.state['listening']['epoch'],False)
    def test_takeover_queue_replacement_and_next_track(self):
        old=self.url();next_url=self.url(2)
        self.cmd('next');self.assertEqual(self.raw(next_url)[0],200);self.assertEqual(self.raw(old)[0],403)
        self.cmd('play',ids=[3]);self.assertEqual(self.raw(next_url)[0],403)
        active=self.url(3);self.cmd('takeover',device_id='b'*32);self.assertEqual(self.raw(active)[0],403)
    def test_frozen_or_unapproved_account_revokes_grant(self):
        url=self.url()
        self.a.db.execute('UPDATE users SET frozen=1 WHERE id=1');self.assertEqual(self.raw(url)[0],403)
        self.a.db.execute('UPDATE users SET frozen=0,approved=0 WHERE id=1');self.assertEqual(self.raw(url)[0],403)
    def test_key_survives_restart_and_invalid_key_is_never_replaced(self):
        url=self.url();key=Path(self.tmp.name)/'secrets/stream-grants.key';before=key.read_bytes()
        self.s.stream_grants=StreamGrants(self.a,self.p,key.parent);self.assertEqual(self.raw(url)[0],200);self.assertEqual(key.read_bytes(),before);self.assertEqual(key.stat().st_mode&0o777,0o600)
        key.write_bytes(b'invalid')
        with self.assertRaises(ValueError):StreamGrants(self.a,self.p,key.parent)
        self.assertEqual(key.read_bytes(),b'invalid')
    def test_ordinary_authenticated_stream_and_compact_heartbeat_unchanged(self):
        url='/api/v1/stream/1?'+urlencode({'device_id':'a'*32,'owner_epoch':self.state['listening']['epoch']})
        self.assertEqual(self.raw(url,Cookie=self.hostcookie)[0],200)
        d=self.state['listening'];result=self.cmd('heartbeat',intent=d['intent'],expected_current=1,position=0,duration=180,observed='playing')
        self.assertTrue(result['_partial']);self.assertNotIn('stream_grants',result)

    def test_receiver_artwork_is_track_scoped_and_revocable(self):
        self.lib.supplemental_artwork=lambda *args,**kwargs:b'artwork-fixture'
        url=self.url().replace('/stream/','/artwork/')
        status,body,headers=self.raw(url)
        self.assertEqual((status,body),(200,b'artwork-fixture'))
        self.assertEqual(headers['Cache-Control'],'private, no-store')
        self.assertEqual(self.raw('/api/v1/artwork/1')[0],403)
        self.assertEqual(self.raw(url.replace('/artwork/1','/artwork/3'))[0],403)
        self.s.stream_grants.now=lambda:self.state['stream_grants']['1']['expires']
        self.assertEqual(self.raw(url)[0],403)

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch,Mock
from types import SimpleNamespace
from app import server
from app.accounts import Accounts
from app.library import Player
from app.parties import Parties
from test_parties import Library

class PartyHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.a=Accounts(self.tmp.name)
        self.a.register('partyhost','a-strong-password',email='party@example.invalid');self.a.approve('partyhost');self.uid=1
        self.lib=Library();self.lib.search=SimpleNamespace(query=lambda q,offset,limit:{'tracks':[self.lib.track(1)]})
        self.p=Player(self.a,self.lib);self.s=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.base='http://127.0.0.1:'+str(self.s.server_port);self.origin=patch.object(server,'ORIGINS',frozenset([self.base]));self.origin.start()
        self.s.accounts,self.s.library,self.s.player=self.a,self.lib,self.p
        self.s.parties=Parties(self.a,self.p,origin=self.base)
        self.party=self.s.parties.host(self.uid,data={'name':'Friday'});self.token=self.party['guest_url'].split('#')[1];self.public='/api/v1/party-public/'+self.token
        self.session=self.a.login('partyhost','a-strong-password');self.hostcookie='arrify_session='+self.session
        self.thread=threading.Thread(target=self.s.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):self.s.shutdown();self.s.server_close();self.thread.join();self.a.db.close();self.origin.stop();self.tmp.cleanup()
    def call(self,path,data=None,cookie='',csrf='',origin=None,etag=None):
        c=http.client.HTTPConnection('127.0.0.1',self.s.server_port,timeout=5)
        headers={'Cookie':cookie,'Origin':self.base if origin is None else origin}
        if etag:headers['If-None-Match']=etag
        if data is not None:headers.update({'Content-Type':'application/json','X-CSRF-Token':csrf})
        c.request('GET' if data is None else 'POST',path,body=None if data is None else json.dumps(data),headers=headers);r=c.getresponse();raw=r.read();out=(r.status,raw,dict(r.getheaders()));c.close();return out
    def join(self):
        code,raw,headers=self.call(self.public+'/join',{'name':'Alex'});self.assertEqual(code,200)
        data=json.loads(raw);return headers['Set-Cookie'].split(';')[0],data['guest']['csrf']
    def test_guest_join_search_queue_and_cannot_stream_or_control_host(self):
        self.assertEqual(self.call(self.public)[0],200);self.assertEqual(self.call(self.public+'/search?q=Song')[0],403)
        cookie,csrf=self.join();self.assertEqual(self.call(self.public+'/search?q=Song',cookie=cookie)[0],200)
        code,raw,_=self.call(self.public+'/add',{'track_id':1},cookie,csrf);self.assertEqual(code,200);self.assertEqual(json.loads(raw)['quota']['available'],2)
        self.assertEqual(self.call('/api/v1/listening',{'action':'pause'},cookie,csrf)[0],403)
        self.assertEqual(self.call('/api/v1/stream/1',cookie=cookie)[0],403)
        self.assertEqual(self.call('/api/v1/parties/'+self.party['id'],cookie=cookie)[0],403)
        self.assertEqual(self.call(self.public+'/add',{'track_id':1},cookie,csrf)[0],400)
    def test_csrf_origin_and_cross_party_identity_are_enforced(self):
        cookie,csrf=self.join()
        self.assertEqual(self.call(self.public+'/add',{'track_id':1},cookie,'wrong')[0],403)
        self.assertEqual(self.call(self.public+'/add',{'track_id':1},cookie,csrf,origin='https://other.invalid')[0],403)
        other=self.s.parties.host(self.uid,data={'name':'Other'})['guest_url'].split('#')[1]
        self.assertEqual(self.call('/api/v1/party-public/'+other+'/add',{'track_id':1},cookie,csrf)[0],403)
    def test_only_display_can_be_framed_by_same_origin_host(self):
        for apple,spotify in [(False,False),(True,False),(False,True),(True,True)]:
            self.s.apple_music=SimpleNamespace(enabled=apple)
            self.s.spotify=SimpleNamespace(enabled=spotify)
            for path in ('/','/party-display','/party'):
                status,_,headers=self.call(path)
                self.assertEqual(status,200)
                csp=dict((p.strip().split()[0],p.strip().split()[1:]) for p in headers['Content-Security-Policy'].split(';') if p.strip())
                self.assertEqual(csp['frame-ancestors'],["'self'"] if path=='/party-display' else ["'none'"])
                if path=='/':self.assertIn("'self'",csp['frame-src'])

    def test_tv_display_is_read_only_and_qr_requires_a_capability(self):
        token=self.party['display_url'].split('#')[1];path='/api/v1/party-display/'+token
        code,body,_=self.call(path);self.assertEqual(code,200);self.assertNotIn('csrf',json.loads(body))
        self.assertEqual(self.call(path+'/qr.svg')[0],200)
        self.assertEqual(self.call('/api/v1/party-public/'+token+'/join',{'name':'Intruder'})[0],400)
        self.assertEqual(self.call('/api/v1/parties/'+self.party['id']+'/qr.svg')[0],403)
        self.assertEqual(self.call('/api/v1/parties/'+self.party['id']+'/qr.svg',cookie=self.hostcookie)[0],200)
    def test_missing_request_uses_host_submission_without_giving_guest_account_access(self):
        cookie,csrf=self.join();submissions=Mock();submissions.create.return_value={'id':'request','title':'Missing','artist':'Artist','album':'Album'}
        self.s.request_submissions=submissions;self.s.request_progress=SimpleNamespace(wake=threading.Event())
        code,body,_=self.call(self.public+'/request',{'selection':'verified-selection'},cookie,csrf);self.assertEqual(code,200)
        submissions.create.assert_called_once_with(self.uid,'verified-selection',0)
        self.assertEqual(self.s.parties.quota(self.s.parties.row(token=self.token),self.s.parties.guest(self.s.parties.row(token=self.token),cookie.split('=',1)[1])['id'])['available'],3)

    def test_unchanged_poll_is_bodyless_and_changes_refresh_it(self):
        cookie,csrf=self.join()
        code,body,headers=self.call(self.public,cookie=cookie)
        tag=headers['ETag'];self.assertEqual(code,200)
        code,body,_=self.call(self.public,cookie=cookie,etag=tag)
        self.assertEqual(code,304);self.assertEqual(body,b'')
        self.call(self.public+'/add',{'track_id':1},cookie,csrf)
        code,body,headers=self.call(self.public,cookie=cookie,etag=tag)
        self.assertEqual(code,200);self.assertNotEqual(headers['ETag'],tag)

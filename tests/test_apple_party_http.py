import json
from types import SimpleNamespace
import unittest
import test_party_http as party_fixture
from test_apple_parties import Catalog

class AppleHTTPTests(unittest.TestCase):
    setUp=party_fixture.PartyHTTPTests.setUp
    tearDown=party_fixture.PartyHTTPTests.tearDown
    call=party_fixture.PartyHTTPTests.call
    join=party_fixture.PartyHTTPTests.join
    def apple_party(self):
        class AppleCatalog(Catalog):
            enabled=True
            def search(self,region,query):return [self.track(region,'12345')]
        self.s.apple_music=AppleCatalog();self.s.parties.apple=self.s.apple_music
        csrf=self.a.csrf(self.session)
        code,raw,_=self.call('/api/v1/parties',{'name':'Apple Friday','source':'apple'},self.hostcookie,csrf)
        self.assertEqual(code,201);self.party=json.loads(raw);self.public='/api/v1/party-public/'+self.party['guest_url'].split('#')[1]
        return csrf
    def test_scoped_search_and_canonical_add_do_not_trust_guest_metadata(self):
        self.apple_party();cookie,csrf=self.join()
        code,raw,_=self.call(self.public+'/search?q=Song',cookie=cookie)
        self.assertEqual(code,200);self.assertEqual(json.loads(raw)['items'][0]['id'],'12345')
        code,raw,_=self.call(self.public+'/add',{'catalog_id':'12345','title':'Injected title','track_id':1},cookie,csrf)
        self.assertEqual(code,200);self.assertEqual(json.loads(raw)['items'][0]['title'],'Song 12345')
        self.assertEqual(self.call(self.public+'/request',{'selection':'anything'},cookie,csrf)[0],400)
        self.assertEqual(self.call(self.public+'/catalog?q=Song',cookie=cookie)[0],400)
    def test_authorization_handoff_sends_origin_without_cross_origin_paths(self):
        _,_,headers=self.call('/api/v1/health')
        self.assertEqual(headers['Referrer-Policy'],'same-origin')
        self.apple_party()
        _,_,headers=self.call('/api/v1/health')
        self.assertEqual(headers['Referrer-Policy'],'strict-origin-when-cross-origin')
    def test_heartbeats_are_compact_and_do_not_retransmit_an_unchanged_queue(self):
        csrf=self.apple_party();pid=self.party['id'];path='/api/v1/parties/'+pid
        self.call(path,{'action':'add','catalog_id':'12345'},self.hostcookie,csrf)
        code,raw,_=self.call(path+'/apple-playback',{'action':'start','device_id':'a'*32},self.hostcookie,csrf)
        self.assertEqual(code,200);d=json.loads(raw)['apple_playback']
        heartbeat={'action':'heartbeat','device_id':'a'*32,'epoch':d['epoch'],'revision':d['revision'],'entry_id':d['entry_id'],'observed':'playing','position':1,'duration':180,'sequence':1}
        code,raw,_=self.call(path+'/apple-playback',heartbeat,self.hostcookie,csrf)
        self.assertEqual(code,200);self.assertTrue(json.loads(raw)['_partial']);self.assertNotIn('items',json.loads(raw));self.assertLess(len(raw),1500)
        _,_,headers=self.call(path,cookie=self.hostcookie);etag=headers['ETag']
        self.call(path+'/apple-playback',{**heartbeat,'position':2,'sequence':2},self.hostcookie,csrf)
        self.assertEqual(self.call(path,cookie=self.hostcookie,etag=etag)[0],304)

    def test_guest_cannot_access_host_playback_tokens_or_change_source(self):
        hostcsrf=self.apple_party();cookie,csrf=self.join();pid=self.party['id']
        self.assertEqual(self.call('/api/v1/apple-music/token',cookie=cookie)[0],403)
        self.assertEqual(self.call('/api/v1/parties/'+pid+'/apple-playback',{'action':'start','device_id':'a'*32},cookie,csrf)[0],403)
        self.assertEqual(self.call('/api/v1/parties/'+pid,{'action':'settings','source':'arrify'},self.hostcookie,hostcsrf)[0],400)
        self.assertEqual(self.call(self.public+'/add',{'catalog_id':'12345'},cookie,'wrong')[0],403)
        self.assertEqual(self.call('/api/v1/parties/'+pid+'/search?q=Song',cookie=self.hostcookie)[0],200)

import http.client
import json
import unittest
from unittest.mock import patch
import test_party_http as fixtures
from app.listening_events import ListeningEvents


class SecurePartyHTTPTests(unittest.TestCase):
    setUp=fixtures.PartyHTTPTests.setUp
    def tearDown(self):
        # End streams before closing the fixture database, as production shutdown
        # also keeps storage alive while request workers drain.
        for row in self.a.db.execute('SELECT id FROM parties WHERE ended=0').fetchall():self.s.parties.host(self.uid,row['id'],{'action':'end'})
        for connection,response in getattr(self,'streams',[]):
            response.read();response.close();connection.close()
        fixtures.PartyHTTPTests.tearDown(self)
    call=fixtures.PartyHTTPTests.call
    join=fixtures.PartyHTTPTests.join
    def host(self,data):return self.call('/api/v1/parties/'+self.party['id'],data,self.hostcookie,self.a.csrf(self.session))
    def event(self,response):
        lines=[]
        while True:
            line=response.fp.readline().decode().strip()
            if not line:return '\n'.join(lines)
            lines.append(line)
    def connect(self,path,cookie=''):
        c=http.client.HTTPConnection('127.0.0.1',self.s.server_port,timeout=7);c.request('GET',path,headers={'Cookie':cookie});r=c.getresponse();self.assertEqual(r.status,200)
        if not hasattr(self,'streams'):self.streams=[]
        self.streams.append((c,r));return c,r

    def test_ended_qr_rejected_guest_invalidated_and_delete_returns_terminal_state(self):
        cookie,csrf=self.join();display='/api/v1/party-display/'+self.party['display_url'].split('#')[1]
        self.assertEqual(self.host({'action':'end'})[0],200)
        for path in [display+'/qr.svg','/api/v1/parties/'+self.party['id']+'/qr.svg']:
            code,raw,_=self.call(path,cookie=self.hostcookie);self.assertEqual(code,410);self.assertEqual(json.loads(raw)['state'],'ended')
        self.assertEqual(json.loads(self.call(self.public,cookie=cookie)[1])['items'],[])
        self.assertEqual(self.call(self.public+'/add',{'track_id':1},cookie,csrf)[0],410)
        self.assertEqual(self.call(self.public+'/join',{'name':'Late'})[0],410)
        self.assertEqual(self.host({'action':'delete'})[0],200)
        self.assertEqual(self.host({'action':'delete'})[0],200)
        self.assertEqual(self.call(display)[0],410);self.assertEqual(self.call(self.public)[0],410)

    def test_guest_and_display_cannot_end_delete_or_control_host(self):
        cookie,csrf=self.join()
        for action in ('end','delete','play_next','move','toggle'):
            self.assertEqual(self.call('/api/v1/parties/'+self.party['id'],{'action':action},cookie,csrf)[0],403)
        self.assertEqual(self.host({'action':'delete'})[0],400)
        self.assertEqual(self.host({'action':'delete','confirm_end':True})[0],200)

    def test_events_revalidate_and_stop_after_end_and_reconnect_to_latest_state(self):
        self.s.parties.events=ListeningEvents();display='/api/v1/party-display/'+self.party['display_url'].split('#')[1]
        c,r=self.connect(display+'/events')
        try:
            first=self.event(r);self.assertIn('event: session',first);self.assertNotIn('guest_url',first)
            self.host({'action':'add','track_id':1});change=self.event(r);self.assertIn('event: session',change)
            snapshot=json.loads(self.call(display)[1]);self.assertEqual(snapshot['items'][0]['id'],1);self.assertIn('playback',snapshot)
            self.host({'action':'end'});self.assertIn('event: terminal',self.event(r))
            self.assertEqual(r.fp.readline(),b'')
        finally:c.close()
        self.assertEqual(self.call(display+'/events')[0],410)

    def test_display_reconnect_reads_current_fifo_and_guest_events_require_cookie(self):
        self.s.parties.events=ListeningEvents();display='/api/v1/party-display/'+self.party['display_url'].split('#')[1]
        self.assertEqual(self.call(self.public+'/events')[0],403)
        self.host({'action':'add','track_id':1});self.host({'action':'add','track_id':2})
        c,r=self.connect(display+'/events')
        try:self.assertIn('event: session',self.event(r))
        finally:c.close()
        p=json.loads(self.call(display)[1]);self.assertEqual([x['id'] for x in p['items']],[1,2])

    def test_invitation_validation_is_rate_limited_without_revealing_sessions(self):
        key=('party-invitation','127.0.0.1')
        self.a.rate_limit(key,1,60)
        with self.a.lock:self.a.attempts[key]['hits']*=1200
        code,raw,_=self.call('/api/v1/party-public/'+'z'*32)
        self.assertEqual(code,400);self.assertIn('Too many attempts',json.loads(raw)['error'])
    def test_universal_link_association_requires_explicit_real_app_ids(self):
        with patch.dict('os.environ',{'ARRIFY_IOS_APP_IDS':''}):
            self.assertEqual(json.loads(self.call('/.well-known/apple-app-site-association')[1]),{'applinks':{'details':[]}})
        with patch.dict('os.environ',{'ARRIFY_IOS_APP_IDS':'AAAAAAAAAA.com.example.arrify,invalid'}):
            data=json.loads(self.call('/.well-known/apple-app-site-association')[1])
            self.assertEqual(data['applinks']['details'][0]['appIDs'],['AAAAAAAAAA.com.example.arrify'])

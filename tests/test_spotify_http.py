import json
import unittest
from urllib.parse import parse_qs,urlparse
import test_party_http as fixtures
from app.spotify import Spotify,SCOPES
from test_spotify import SID,song


class SpotifyHTTPTests(unittest.TestCase):
    tearDown=fixtures.PartyHTTPTests.tearDown
    call=fixtures.PartyHTTPTests.call
    join=fixtures.PartyHTTPTests.join
    def setUp(self):
        fixtures.PartyHTTPTests.setUp(self);self.calls=[]
        def fetch(url,method,data,headers):
            self.calls.append((url,method,data));path=urlparse(url).path
            if path=='/api/token':return dict(access_token='test-access',refresh_token='test-refresh',expires_in=3600,scope=SCOPES)
            if path=='/v1/me':return dict(id='host',display_name='Host',country='US',product='premium')
            if path=='/v1/search':return {'tracks':{'items':[song()]}}
            if path.startswith('/v1/tracks/'):return song()
            return {}
        self.s.spotify=Spotify(self.a,'https://arrify.example',env={'ARRIFY_SPOTIFY_ENABLED':'1','ARRIFY_SPOTIFY_CLIENT_ID':'a'*32},fetch=fetch)
        self.s.parties.spotify=self.s.spotify;self.csrf=self.a.csrf(self.session)
    def link(self):
        code,body,_=self.call('/api/v1/spotify',{'action':'connect'},self.hostcookie,self.csrf);self.assertEqual(code,200)
        state=parse_qs(urlparse(json.loads(body)['url']).query)['state'][0]
        code,body,headers=self.call('/api/v1/spotify/callback?state='+state+'&code=private-code',cookie=self.hostcookie)
        self.assertEqual(code,303);self.assertEqual(headers['Location'],'/?music_service=spotify&connection=connected');self.assertNotIn('private-code',headers['Location'])
    def create(self):
        self.link();code,body,_=self.call('/api/v1/parties',{'name':'Spotify night','source':'spotify'},self.hostcookie,self.csrf);self.assertEqual(code,201);self.party=json.loads(body);self.public='/api/v1/party-public/'+self.party['guest_url'].split('#')[1]
    def test_authentication_csrf_and_callback_account_binding(self):
        self.assertEqual(self.call('/api/v1/spotify')[0],403)
        self.assertEqual(self.call('/api/v1/spotify',{'action':'connect'},self.hostcookie,'bad')[0],403)
        self.link();code,body,headers=self.call('/api/v1/spotify/token',cookie=self.hostcookie)
        self.assertEqual(code,200);self.assertEqual(headers['Cache-Control'],'no-store')
        cookie,csrf=self.join();self.assertEqual(self.call('/api/v1/spotify/token',cookie=cookie)[0],403)
        self.assertEqual(self.call('/api/v1/spotify/player',{'action':'play'},cookie,csrf)[0],403)
    def test_guest_search_and_add_use_spotify_metadata_and_end_revokes_access(self):
        self.create();cookie,csrf=self.join();code,body,_=self.call(self.public+'/search?q=song',cookie=cookie);self.assertEqual(code,200);self.assertEqual(json.loads(body)['items'][0]['provider'],'spotify')
        code,body,_=self.call(self.public+'/add',{'catalog_id':SID,'title':'Forged title'},cookie,csrf);self.assertEqual(code,200);self.assertEqual(json.loads(body)['items'][0]['title'],'A song')
        self.assertEqual(self.call(self.public+'/request',{'selection':'anything'},cookie,csrf)[0],400)
        self.assertEqual(self.call('/api/v1/parties/'+self.party['id']+'/spotify-playback',{'action':'start','device_id':'a'*32,'spotify_device_id':'sdk-device'},cookie,csrf)[0],403)
        self.call('/api/v1/parties/'+self.party['id'],{'action':'end'},self.hostcookie,self.csrf)
        self.assertEqual(self.call(self.public+'/add',{'catalog_id':SID},cookie,csrf)[0],410)
    def test_player_cannot_play_outside_fenced_queue_or_after_end(self):
        self.create();pid=self.party['id'];self.call('/api/v1/parties/'+pid,{'action':'add','catalog_id':SID},self.hostcookie,self.csrf)
        code,body,_=self.call('/api/v1/parties/'+pid+'/spotify-playback',{'action':'start','device_id':'a'*32,'spotify_device_id':'sdk-device'},self.hostcookie,self.csrf);self.assertEqual(code,200);d=json.loads(body)['spotify_playback']
        command=dict(action='play',party_id=pid,device_id='a'*32,epoch=d['epoch'],revision=d['revision'],entry_id=d['entry_id'],track_id='forged')
        self.assertEqual(self.call('/api/v1/spotify/player',command,self.hostcookie,self.csrf)[0],200)
        requested=json.loads(next(data for url,method,data in reversed(self.calls) if '/me/player/play?' in url));self.assertEqual(requested['uris'],['spotify:track:'+SID])
        self.assertEqual(self.call('/api/v1/spotify/player',command|{'epoch':-1},self.hostcookie,self.csrf)[0],409)
        self.call('/api/v1/parties/'+pid,{'action':'end'},self.hostcookie,self.csrf)
        self.assertNotEqual(self.call('/api/v1/spotify/player',command,self.hostcookie,self.csrf)[0],200)
    def test_disconnect_clears_link_and_lease(self):
        self.create();code,body,_=self.call('/api/v1/spotify',{'action':'disconnect'},self.hostcookie,self.csrf);self.assertEqual(code,200);self.assertFalse(json.loads(body)['linked'])
        self.assertEqual(self.call('/api/v1/spotify/token',cookie=self.hostcookie)[0],400)

    def test_ending_during_provider_control_pauses_and_rejects_stale_result(self):
        self.create();pid=self.party['id'];self.s.parties.add_service(self.uid,pid,self.s.spotify.track(self.uid,SID),'spotify')
        state=self.s.parties.spotify_playback.command(self.uid,pid,dict(action='start',device_id='a'*32,spotify_device_id='sdk-device'))['spotify_playback']
        fetch=self.s.spotify.fetch
        def interrupted(url,method,data,headers):
            result=fetch(url,method,data,headers)
            if '/me/player/play?' in url:self.s.parties.host(self.uid,pid,{'action':'end'})
            return result
        self.s.spotify.fetch=interrupted
        command=dict(action='play',party_id=pid,device_id='a'*32,epoch=state['epoch'],revision=state['revision'],entry_id=state['entry_id'])
        self.assertEqual(self.call('/api/v1/spotify/player',command,self.hostcookie,self.csrf)[0],409)
        self.assertTrue(any('/me/player/pause?' in url for url,_,_ in self.calls))

    def test_logout_during_oauth_exchange_cannot_persist_a_new_link(self):
        code,body,_=self.call('/api/v1/spotify',{'action':'connect'},self.hostcookie,self.csrf)
        state=parse_qs(urlparse(json.loads(body)['url']).query)['state'][0];fetch=self.s.spotify.fetch
        def logout(url,method,data,headers):
            result=fetch(url,method,data,headers)
            if urlparse(url).path=='/v1/me':
                with self.a.lock:self.a.db.execute('DELETE FROM sessions WHERE digest=?',(self.a.digest(self.session),))
            return result
        self.s.spotify.fetch=logout
        code,body,headers=self.call('/api/v1/spotify/callback?state='+state+'&code=private-code',cookie=self.hostcookie)
        self.assertEqual(code,303);self.assertEqual(headers['Location'],'/?music_service=spotify&connection=failed');self.assertFalse(self.s.spotify.config(self.uid)['linked'])

import base64
import json
import tempfile
import threading
import unittest
from urllib.parse import parse_qs,urlparse
from app.accounts import Accounts
from app.spotify import Spotify,SCOPES,SpotifyNotFound
from app.library import Player,PlaybackConflict
from app.parties import Parties
from test_parties import Library


SID='A'*22
def song(identifier=SID):return {'id':identifier,'name':'A song','artists':[{'name':'Artist'}],'type':'track','duration_ms':180000,'album':{'name':'Album','images':[{'url':'https://i.scdn.co/image/example'}]},'external_ids':{'isrc':'USAAA0000001'}}


class SpotifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.a=Accounts(self.tmp.name)
        self.a.db.execute("INSERT INTO users(id,username,password,approved,created) VALUES(1,'host','!fixture',1,1)")
        self.now=10000;self.calls=[];self.token_count=0;self.fail=None
        def fetch(url,method,data,headers):
            self.calls.append((url,method,data,headers))
            if self.fail:raise self.fail
            path=urlparse(url).path
            if path=='/api/token':
                self.token_count+=1
                return dict(access_token='access-private-'+str(self.token_count),refresh_token='refresh-private',expires_in=3600,scope=SCOPES)
            if path=='/v1/me':return dict(id='account',display_name='Host',country='US',product='premium')
            if path.startswith('/v1/tracks/'):return song(path.rsplit('/',1)[1])
            if path=='/v1/search':return {'tracks':{'items':[song(),{'id':'bad'}]}}
            if path=='/v1/me/playlists':return {'items':[{'id':SID,'name':'Party list','owner':{'display_name':'Host'},'tracks':{'total':1}}]}
            if path.endswith('/items'):return {'items':[{'item':song()},{'item':None}],'total':2,'next':None}
            if path.startswith('/v1/playlists/'):return {'id':SID,'name':'Party list'}
            return {}
        self.m=Spotify(self.a,'https://arrify.example',env={'ARRIFY_SPOTIFY_ENABLED':'1','ARRIFY_SPOTIFY_CLIENT_ID':'a'*32},fetch=fetch,now=lambda:self.now)
    def tearDown(self):self.a.db.close();self.tmp.cleanup()
    def begin(self):return parse_qs(urlparse(self.m.begin(1,'session-one')['url']).query)
    def link(self):
        q=self.begin();self.m.complete(1,'session-one',q['state'][0],'code');return q
    def test_disconnect_during_fetch_cannot_repopulate_personal_cache(self):
        self.link()
        def fetch(url, method, data, headers):
            self.m.disconnect(1)
            return {'items': [{'name': 'Private playlist'}]}
        self.m.fetch = fetch
        with self.assertRaises(PermissionError):self.m.request(1, '/v1/me/playlists', cached=True)
        self.assertFalse(self.m.cache)

    def test_expired_device_is_distinct_from_unavailable_song(self):
        self.link()
        def missing(uid,path,*args,**kwargs):
            if path=='/v1/me/player/devices':return {'devices':[]}
            raise SpotifyNotFound('missing')
        self.m.request=missing
        with self.assertRaisesRegex(ValueError,'device disconnected'):
            self.m.player(1,'old-device','play',SID)
        def existing(uid,path,*args,**kwargs):
            if path=='/v1/me/player/devices':return {'devices':[{'id':'old-device'}]}
            raise SpotifyNotFound('missing')
        self.m.request=existing
        with self.assertRaisesRegex(ValueError,'could not start this song'):
            self.m.player(1,'old-device','play',SID)

    def test_pkce_session_binding_single_use_and_expiry(self):
        q=self.begin();self.assertEqual(q['code_challenge_method'],['S256']);self.assertNotIn('client_secret',q)
        with self.assertRaises(PermissionError):self.m.complete(1,'another-session',q['state'][0],'code')
        with self.assertRaises(PermissionError):self.m.complete(1,'session-one',q['state'][0],'code')
        q=self.begin();self.now+=601
        with self.assertRaises(PermissionError):self.m.complete(1,'session-one',q['state'][0],'code')
    def test_link_encrypts_tokens_and_refreshes_without_exposing_them_in_config(self):
        q=self.link();self.assertTrue(self.m.config(1)['linked']);text=json.dumps(self.m.config(1));stored=self.a.db.execute('SELECT payload FROM spotify_links').fetchone()[0]
        self.assertNotIn('access-private',stored+text);self.assertNotIn('refresh-private',stored+text)
        self.assertNotIn('access_token',text);self.assertEqual(self.m.token(1)['access_token'],'access-private-1');self.assertEqual(self.token_count,1)
        self.now+=3600;self.assertEqual(self.m.token(1)['access_token'],'access-private-2');self.assertEqual(self.token_count,2)
        with self.assertRaises(PermissionError):self.m.complete(1,'session-one',q['state'][0],'code')
        with self.assertRaises(ValueError):self.m.vault.open(2,stored)
        raw=bytearray(base64.b64decode(stored));raw[-1]^=1
        with self.assertRaises(ValueError):self.m.vault.open(1,base64.b64encode(raw).decode())
    def test_disconnect_revokes_pending_authentication_and_other_accounts_are_isolated(self):
        self.link();q=self.begin();self.m.disconnect(1)
        self.assertFalse(self.m.config(1)['linked']);self.assertFalse(self.m.config(2)['linked'])
        with self.assertRaises(PermissionError):self.m.complete(1,'session-one',q['state'][0],'code')
        with self.assertRaises(ValueError):self.m.token(1)
    def test_catalog_metadata_and_playlist_items_are_validated_and_cached(self):
        self.link();self.assertEqual(self.m.search(1,'song')[0]['provider'],'spotify');count=len(self.calls);self.m.search(1,'song');self.assertEqual(len(self.calls),count)
        self.assertIsNone(self.m.song(song()|{'is_local':True}));self.assertIsNone(self.m.song(song()|{'restrictions':{'reason':'market'}}))
        self.assertEqual(self.m.image([{'url':'https://evil.invalid/image'}]),'')
        self.assertEqual(len(self.m.playlist_tracks(1,SID)['tracks']),1);self.assertEqual(self.m.playlists(1)['items'][0]['kind'],'spotify')
        with self.assertRaises(ValueError):self.m.track(1,'../me')
    def test_disabled_or_bad_redirect_cannot_start_auth(self):
        disabled=Spotify(self.a,'https://arrify.example',env={})
        self.assertFalse(disabled.config(1)['available'])
        with self.assertRaises(ValueError):disabled.begin(1,'session')
        self.m.redirect='https://evil.invalid/callback'
        self.assertFalse(self.m.ready())
    def test_bad_vault_permissions_disable_only_spotify_and_unreadable_links_can_reconnect(self):
        self.link();self.a.db.execute("UPDATE spotify_links SET payload='invalid' WHERE user_id=1")
        self.assertFalse(self.m.config(1)['linked']);self.link();self.assertTrue(self.m.config(1)['linked'])
        self.m.vault.path.chmod(0o644)
        unavailable=Spotify(self.a,'https://arrify.example',env={'ARRIFY_SPOTIFY_ENABLED':'1','ARRIFY_SPOTIFY_CLIENT_ID':'a'*32})
        self.assertFalse(unavailable.config(1)['available'])
    def test_cancelled_consent_does_not_link_an_account(self):
        q=self.begin()
        with self.assertRaises(ValueError):self.m.complete(1,'session-one',q['state'][0],'','access_denied')
        self.assertFalse(self.m.config(1)['linked'])


class SpotifyPartyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.a=Accounts(self.tmp.name)
        self.a.db.execute("INSERT INTO users(id,username,password,approved,created) VALUES(1,'host','!fixture',1,1)")
        class Catalog:
            def storefront(self,uid):return 'US'
        self.lib=Library();self.player=Player(self.a,self.lib);self.s=Parties(self.a,self.player,origin='https://arrify.example',spotify=Catalog())
        self.party=self.s.host(1,data={'name':'Spotify party','source':'spotify'});self.pid=self.party['id'];self.raw=self.s.row(self.pid,1);self.token=self.party['guest_url'].split('#')[1];_,self.g,self.secret=self.s.join(self.token,'Guest');self.device='a'*32
    def tearDown(self):self.a.db.close();self.tmp.cleanup()
    def add(self,i):return self.s.add_service(1,self.pid,Spotify.song(song(str(i).zfill(22)))|{'isrc':'ISRC'+str(i)},'spotify',self.token,self.secret,self.g['csrf'])
    def command(self,action,**values):
        d=self.s.spotify_playback.load(1)
        return self.s.spotify_playback.command(1,self.pid,dict(action=action,device_id=self.device,epoch=d['epoch'],revision=d['revision'],entry_id=d['entry_id'],spotify_device_id='device-one')|values)
    def test_fifo_quota_replenishment_confirmed_completion_and_provider_isolation(self):
        for i in (1,2,3):self.add(i)
        with self.assertRaises(ValueError):self.add(4)
        p=self.command('start');self.assertIn('spotify_playback',p);self.assertNotIn('apple_playback',p)
        with self.assertRaises(ValueError):self.command('next',automatic=True)
        first=p['items'][0]['entry_id']
        for _ in range(2):
            p=self.command('heartbeat',sequence=1,observed='playing',position=1,duration=180);self.assertEqual(p['source'],'spotify')
            self.command('next',automatic=True)
        self.assertEqual(self.s.quota(self.raw,self.g['id'])['available'],2)
        with self.assertRaises(PlaybackConflict):self.command('next',entry_id=first)
        self.assertEqual(self.s.host(1,self.pid)['items'][0]['id'],'3'.zfill(22))
        with self.assertRaises(ValueError):self.s.add_apple(1,self.pid,Spotify.song(song()))
        with self.assertRaises(ValueError):self.s.host(1,self.pid,{'action':'settings','source':'apple'})
    def test_end_revokes_guest_writes_and_host_playback(self):
        self.add(1);self.command('start');self.s.host(1,self.pid,{'action':'end'})
        with self.assertRaises((ValueError,PermissionError)):self.add(2)
        with self.assertRaises((ValueError,PermissionError)):self.command('heartbeat',sequence=1,observed='playing',position=1,duration=180)
        self.assertIsNone(self.s.spotify_playback.load(1)['party_id'])
    def test_idle_queue_starts_new_arrival_without_native_library_playback(self):
        self.command('start');self.assertIsNone(self.s.spotify_playback.load(1)['entry_id']);self.add(1)
        d=self.s.spotify_playback.load(1);self.assertIsNotNone(d['entry_id']);self.assertTrue(d['desired_playing']);self.assertIsNone(self.player.load(1)['current'])
        view=self.s.view(self.raw,display=True);self.assertEqual(view['source'],'spotify');self.assertIn('playback',view);self.assertNotIn('spotify_playback',view)

    def test_concurrent_guests_cannot_bypass_request_limit(self):
        accepted=[]
        def add(i):
            try:self.add(i);accepted.append(i)
            except ValueError:pass
        workers=[threading.Thread(target=add,args=(i,)) for i in range(1,9)]
        for worker in workers:worker.start()
        for worker in workers:worker.join()
        self.assertEqual(len(accepted),3)
        self.assertEqual(len(self.s.host(1,self.pid)['items']),3)

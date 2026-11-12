import http.client
import json
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
import test_stream_grants_http as fixtures
import test_native_http as contract_test
from app.search import Search

CONTRACT = json.loads((Path(__file__).resolve().parents[1] / 'contracts/native-playback.openapi.json').read_text())
from app.native_auth import NativeSessions
from app.native_media import NativeMedia
from app.render_cache import RenderCache, RenderBusy


class NativeMediaHTTPTests(unittest.TestCase):
    cmd = fixtures.StreamGrantHTTPTests.cmd
    call = fixtures.StreamGrantHTTPTests.call
    raw = fixtures.StreamGrantHTTPTests.raw

    def setUp(self):
        fixtures.StreamGrantHTTPTests.setUp(self)
        self.s.render_cache = RenderCache(self.tmp.name)
        self.s.transcodes = threading.BoundedSemaphore(2)
        self.s.native_media = NativeMedia(self.s)
        self.auth = NativeSessions(self.a)
        self.pair = self.auth.login({'username':'partyhost','password':'a-strong-password','device_name':'iPhone'})

    def tearDown(self):
        self.s.native_media.close()
        fixtures.StreamGrantHTTPTests.tearDown(self)

    def test_manifest_includes_current_and_three_upcoming_and_revokes_removed(self):
        self.cmd('play',ids=[1,2,3,4])
        status, manifest=self.manifest()
        self.assertEqual(status,200)
        self.assertEqual([i['track_id'] for i in manifest['items']],[1,2,3,4])
        fourth=manifest['items'][3]['url']
        self.assertEqual(self.raw(fourth)[0],200)
        self.cmd('remove',index=2)
        self.assertEqual(self.raw(fourth)[0],403)

    def manifest(self, profile='original', token=None, **data):
        c = http.client.HTTPConnection('127.0.0.1', self.s.server_port, timeout=5)
        body = {'device_id':'a'*32,'owner_epoch':self.state['listening']['epoch'],'profile':profile, **data}
        c.request('POST', '/api/v1/native/playback/manifest', json.dumps(body),
                  {'Content-Type':'application/json','Authorization':'Bearer '+(token or self.pair['access_token'])})
        r = c.getresponse(); result = r.status, json.loads(r.read()); c.close()
        with patch.object(contract_test, 'CONTRACT', CONTRACT):
            contract_test.validate(self, result[1], {'$ref':'#/components/schemas/'+('Manifest' if result[0] == 200 else 'Error')})
        return result

    def test_native_window_get_head_range_and_no_broader_privilege(self):
        status, manifest = self.manifest()
        self.assertEqual(status, 200)
        self.assertEqual([i['track_id'] for i in manifest['items']], [1,2,3])
        for item in manifest['items']:
            self.assertEqual(self.raw(item['url'])[0], 200)
            self.assertEqual(self.raw(item['url'], 'HEAD')[0], 200)
        url = manifest['items'][0]['url']
        status, body, headers = self.raw(url, Range='bytes=3-8')
        self.assertEqual((status, body), (206, self.audio.read_bytes()[3:9]))
        self.assertEqual(self.raw(url, Range='bytes=999-')[0], 416)
        self.assertEqual(self.raw(url, Range='bytes=3-', **{'If-Range':'"old"'})[0], 200)
        self.assertEqual(self.raw(url.replace('/stream/1','/stream/4'))[0], 403)
        self.assertEqual(self.raw(url.replace('profile=original','profile=mp3'))[0], 403)
        self.assertEqual(self.raw(url.replace('level=off','level=track'))[0], 403)
        self.assertEqual(self.raw(url+'&profile=original')[0], 403)
        grant = parse_qs(urlparse(url).query)['native_grant'][0]
        self.assertEqual(self.raw('/api/v1/me', Authorization='Bearer '+grant)[0], 403)
        self.assertEqual(self.raw(url.replace('/stream/1','/artwork/1'))[0], 403)

    def test_access_expiry_and_rotation_keep_url_but_revoke_and_takeover_do_not(self):
        _, manifest = self.manifest(); url = manifest['items'][0]['url']
        self.a.db.execute('UPDATE sessions SET expires=0 WHERE digest=?', (self.a.digest(self.pair['access_token']),))
        self.assertEqual(self.raw(url)[0], 200)
        renewed = self.auth.refresh(self.pair['refresh_token'])
        self.assertEqual(self.raw(url)[0], 200)
        self.auth.revoke(1, renewed['session_id'])
        self.assertEqual(self.raw(url)[0], 403)

    def test_next_two_follow_queue_and_owner_epoch(self):
        _, manifest = self.manifest()
        old, next_url = manifest['items'][0]['url'], manifest['items'][1]['url']
        self.cmd('next')
        self.assertEqual(self.raw(next_url)[0], 200)
        self.assertEqual(self.raw(old)[0], 403)
        self.cmd('takeover', device_id='b'*32)
        self.assertEqual(self.raw(next_url)[0], 403)
        self.assertEqual(self.manifest()[0], 409)

    def test_source_replacement_invalidates_url_and_cache_key_even_same_mtime_size(self):
        _, manifest = self.manifest(); item = manifest['items'][0]
        before = self.s.render_cache.target(str(self.audio), 'aac', 0)
        stat = self.audio.stat()
        replacement = self.audio.with_suffix('.new'); replacement.write_bytes(b'x'*stat.st_size)
        os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns)); replacement.replace(self.audio)
        self.assertNotEqual(before, self.s.render_cache.target(str(self.audio), 'aac', 0))
        self.assertEqual(self.raw(item['url'])[0], 403)
        _, fresh = self.manifest()
        self.assertNotEqual(item['source_revision'], fresh['items'][0]['source_revision'])
        self.assertEqual(self.raw(fresh['items'][0]['url'])[0], 200)

    def test_preparation_is_bounded_and_evicted_rendition_does_not_block_stream(self):
        gate = threading.Event(); started = threading.Event()
        def render(filename, profile, gain, slots):
            started.set(); gate.wait(4)
            target = self.s.render_cache.target(filename, profile, gain)
            target.write_bytes(b'rendered-audio'); return target
        with patch.object(self.s.render_cache, 'render', side_effect=render) as mocked:
            try:
                status, pending = self.manifest('aac')
                self.assertEqual(status, 200); self.assertTrue(started.wait(1))
                self.assertTrue(all(i['url'] is None for i in pending['items']))
                self.assertEqual(mocked.call_count, 1)
            finally: gate.set()
            for job in self.s.native_media.jobs.values(): job['future'].result(timeout=3)
            _, ready = self.manifest('aac'); url = ready['items'][0]['url']
            self.assertIsNotNone(url)
            self.assertEqual(self.raw(url, Range='bytes=0-2')[:2], (206, b'ren'))
            self.s.render_cache.target(str(self.audio),'aac',0).unlink()
            self.assertEqual(self.raw(url)[0], 503)
            self.assertEqual(mocked.call_count, 1)

    def test_expiry_profile_validation_and_legacy_session_not_accepted(self):
        _, manifest = self.manifest(); item = manifest['items'][0]
        self.s.native_media.now = lambda: item['url_expires']
        self.assertEqual(self.raw(item['url'])[0], 403)
        q = parse_qs(urlparse(item['url']).query)
        self.assertEqual(self.s.native_media.identity(1,q,False), {'id':1})
        self.assertEqual(self.manifest('pcm')[0], 400)
        self.assertEqual(self.manifest(level='track')[0], 400)
        legacy = self.a.login('partyhost','a-strong-password','native')
        self.assertEqual(self.manifest(token=legacy)[0], 401)

    def api(self, path, data=None):
        c = http.client.HTTPConnection('127.0.0.1', self.s.server_port, timeout=5)
        method = 'POST' if data is not None else 'GET'
        c.request(method, '/api/v1/native/'+path, json.dumps(data) if data is not None else None,
                  {'Content-Type':'application/json','Authorization':'Bearer '+self.pair['access_token']})
        r = c.getresponse(); result = r.status, json.loads(r.read()); c.close()
        name = 'Error' if result[0] != 200 else 'QueuePage' if path.startswith('playback/queue') else 'CatalogPage' if path.startswith('catalog') else 'LibraryDetail' if path.startswith('library/detail') else 'LibraryOverview' if path == 'library' else 'LibraryEntry' if path == 'playlists' else 'Playback'
        with patch.object(contract_test, 'CONTRACT', CONTRACT):
            contract_test.validate(self, result[1], {'$ref':'#/components/schemas/'+name})
        return result

    def test_native_catalog_paging_aliases_and_stale_revision(self):
        tracks = [{'id':i, 'title':'Song '+str(i), 'artist':'P!nk','album':'Album'} for i in range(1,4)]
        self.lib.index_lock = threading.RLock()
        self.lib.search = Search(tracks, [], [])
        status, first = self.api('catalog?q=Pink&limit=1')
        self.assertEqual(status, 200); self.assertEqual(first['total'], 3)
        self.assertEqual(len(first['items']), 1)
        _, second = self.api('catalog?q=Pink&limit=1&offset=1&revision='+first['revision'])
        self.assertNotEqual(first['items'][0]['id'], second['items'][0]['id'])
        self.assertEqual(self.api('catalog?q=Pink&offset=1')[0], 409)
        self.lib.search = Search(tracks[:1], [], [])
        self.assertEqual(self.api('catalog?q=Pink&offset=1&revision='+first['revision'])[0], 409)
        self.assertEqual(self.api('catalog?limit=101')[0], 400)

    def test_native_library_http_auth_context_and_private_access(self):
        from app.playlists import Playlists
        self.lib.index_lock = threading.RLock()
        self.lib.search = Search(list(self.lib.tracks.values()), [], [])
        self.lib.albums = {}; self.lib.artists = {}; self.lib.collections = lambda: []; self.lib.suggestions = lambda: []
        playlists = Playlists(self.a, self.lib)
        code, created = self.api('playlists', {'name':'Native mix','visibility':'private','add_track_id':1})
        self.assertEqual(code,200)
        pid = int(created['id'])
        self.assertEqual(self.api('playlists', {'id':pid,'add_track_id':2})[0],200)
        self.assertEqual(self.api('playlists', {'id':pid,'add_track_id':3})[0],200)
        code, library = self.api('library')
        self.assertEqual(code,200); self.assertEqual(library['items'][0]['kind'],'playlists')
        self.assertEqual(self.raw('/api/v1/native/library')[0],401)
        path = 'library/detail?kind=playlists&id='+str(pid)
        code, detail = self.api(path+'&limit=1')
        self.assertEqual(code,200); self.assertEqual(detail['next_offset'],1)
        _, state = self.api('playback')
        code, started = self.api('playback', {'action':'play','source':{'kind':'playlists','id':str(pid)},
            'device_id':'a'*32,'expected_revision':state['queue_revision'],'track_id':2})
        self.assertEqual(code,200); self.assertEqual(started['current_track_id'],2)
        self.assertEqual(started['upcoming_track_ids'],[3])
        self.a.db.execute("INSERT INTO users(username,password,approved,created) VALUES('other','fixture',1,1)")
        other = self.a.db.execute("SELECT id FROM users WHERE username='other'").fetchone()[0]
        private = playlists.edit(other, {'name':'Private','track_ids':[1]})['id']
        self.assertEqual(self.api('library/detail?kind=playlists&id='+str(private))[0],404)
        self.assertEqual(self.api('playlists', {'id':private,'add_track_id':2})[0],404)
        self.assertEqual(self.api('playback', {'action':'play','source':{'kind':'playlists','id':str(private)},
            'device_id':'a'*32,'expected_revision':started['queue_revision']})[0],404)

    def test_native_cache_report_reaches_server_listening_status(self):
        _,state=self.api('playback')
        report={'state':'active','storage':'disk','worker_version':'ios-cache-v1','limit_bytes':512*1024*1024,
                'player_seconds':12,'sample_age_seconds':0,
                'items':[{'id':1,'loaded_bytes':19,'total_bytes':19,'state':'ready'}]}
        code,_=self.api('playback',{'action':'heartbeat','device_id':'a'*32,
            'owner_epoch':state['owner_epoch'],'expected_current':1,'intent':state['intent'],
            'position':1,'duration':100,'observed':'playing','report_seq':1,'buffer':report})
        self.assertEqual(code,200)
        status=json.loads(self.a.db.execute('SELECT status FROM listening_status WHERE user_id=?',(self.uid,)).fetchone()[0])
        self.assertEqual(status['buffer']['downloaded_bytes'],19)
        self.assertEqual(status['buffer']['storage'],'disk')
        self.assertEqual(status['buffer']['worker_version'],'ios-cache-v1')

    def test_native_events_and_remote_commands_share_web_owner(self):
        from app.listening_events import ListeningEvents
        self.s.listening_events = ListeningEvents()
        self.s.event_slots = threading.BoundedSemaphore(8)
        self.s.listening.events = self.s.listening_events
        c = http.client.HTTPConnection('127.0.0.1', self.s.server_port, timeout=5)
        c.request('GET', '/api/v1/listening/events', headers={'Authorization':'Bearer '+self.pair['access_token']})
        response = c.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader('Content-Type'), 'text/event-stream')
        def event():
            for _ in range(12):
                line = response.readline()
                if line.startswith(b'data: '): return int(line[6:])
            self.fail('No session event')
        first = event()
        _, state = self.api('playback')
        code, advanced = self.api('playback', {'action':'next','device_id':'b'*32,
            'expected_revision':state['queue_revision'],'transition_id':'remote-next',
            'expected_current':1,'expected_next':2})
        self.assertEqual(code, 200)
        self.assertEqual(advanced['owner_device_id'], 'a'*32)
        self.assertEqual(advanced['current_track_id'], 2)
        self.assertGreater(event(), first)
        response.close(); c.close()
        for _ in range(30):
            if not self.s.listening_events.clients: break
            # Wake the fixture's account without waiting for the production keepalive.
            for uid in list(self.s.listening_events.clients): self.s.listening_events.publish(uid)
            time.sleep(0.05)
        self.assertFalse(self.s.listening_events.clients)

    def test_native_queue_paging_duplicate_occurrences_and_stale_edits(self):
        _, state = self.api('playback')
        code, state = self.api('playback', {'action':'play','ids':[1,2,3,2,1], 'device_id':'a'*32, 'expected_revision':state['queue_revision']})
        self.assertEqual(code,200)
        code,page=self.api('playback/queue?limit=2')
        self.assertEqual(code,200);self.assertEqual(page['total'],4)
        self.assertEqual([x['track']['id'] for x in page['items']],[2,3])
        self.assertEqual(self.api('playback/queue?offset=2')[0],409)
        code,tail=self.api('playback/queue?offset=2&revision='+str(page['revision']))
        self.assertEqual([x['index'] for x in tail['items']],[2,3])
        code,moved=self.api('playback',{'action':'reorder','from':2,'to':0,'device_id':'a'*32,'expected_revision':page['revision']})
        self.assertEqual(code,200)
        self.assertEqual(self.api('playback',{'action':'remove','index':0,'device_id':'a'*32,'expected_revision':page['revision']})[0],409)
        self.assertEqual(self.api('playback/queue?offset=2&revision='+str(page['revision']))[0],409)
        code,jumped=self.api('playback',{'action':'queue_jump','index':2,'device_id':'a'*32,'expected_revision':moved['queue_revision']})
        self.assertEqual(code,200);self.assertEqual(jumped['current_track_id'],3)
        self.assertEqual(jumped['upcoming_track_ids'],[1]);self.assertTrue(jumped['playing'])

    def test_native_playback_snapshot_conflict_and_duplicate_next(self):
        status, state = self.api('playback')
        self.assertEqual(status, 200); self.assertEqual(state['upcoming_track_ids'], [2,3])
        body = {'action':'next','device_id':'a'*32,'expected_revision':state['queue_revision'],
                'transition_id':'native-next-1','expected_current':1,'expected_next':2}
        status, advanced = self.api('playback', body)
        self.assertEqual(status, 200); self.assertEqual(advanced['current_track_id'], 2)
        self.assertEqual(self.api('playback', body)[1]['current_track_id'], 2)
        self.assertEqual(self.api('playback', {'action':'pause','device_id':'a'*32,'expected_revision':0})[0], 409)
        _, heartbeat = self.api('playback', {'action':'heartbeat','device_id':'a'*32,
            'owner_epoch':advanced['owner_epoch'],'expected_current':2,'intent':advanced['intent'],
            'position':1,'duration':100,'observed':'playing','report_seq':1})
        self.assertTrue(heartbeat['partial']); self.assertIsNone(heartbeat['upcoming_track_ids'])

    def test_native_queue_controls_preserve_current_song_and_validate_repeat(self):
        _, state = self.api('playback')
        self.assertTrue(state['queue_controls_available'])
        for mode in ('all','one','off'):
            code, updated = self.api('playback', {'action':'repeat','mode':mode,'device_id':'a'*32,
                'expected_revision':state['queue_revision']})
            self.assertEqual(code,200);self.assertEqual(updated['repeat_mode'],mode)
            self.assertEqual(updated['current_track_id'],state['current_track_id'])
            state=updated
        code, shuffled=self.api('playback',{'action':'shuffle','device_id':'a'*32,'expected_revision':state['queue_revision']})
        self.assertEqual(code,200);self.assertEqual(shuffled['current_track_id'],state['current_track_id'])
        self.assertCountEqual(shuffled['upcoming_track_ids'],state['upcoming_track_ids'])
        self.assertEqual(self.api('playback',{'action':'repeat','mode':'invalid','device_id':'a'*32,'expected_revision':shuffled['queue_revision']})[0],400)

    def test_relaunched_client_heartbeat_advances_without_new_owner_epoch(self):
        _, state = self.api('playback')
        body={'action':'heartbeat','device_id':'a'*32,'owner_epoch':state['owner_epoch'],
              'expected_current':state['current_track_id'],'intent':state['intent'],
              'position':12,'duration':100,'observed':'playing','report_seq':245,'output':'native'}
        self.assertEqual(self.api('playback',body)[0],200)
        # Old clients restarted at 1: the server correctly ignores that stale report.
        self.assertEqual(self.api('playback',{**body,'report_seq':1,'position':15})[1]['position_seconds'],12)
        result=self.api('playback',{**body,'report_seq':1800000000000,'position':20,'output':'AirPlay · Apple TV'})[1]
        self.assertEqual(result['position_seconds'],20)
        self.assertEqual(result['owner_epoch'],state['owner_epoch'])

    def test_failed_preparation_stops_retrying_until_explicit_retry(self):
        now = [time.time()]
        self.s.native_media.now = lambda:now[0]
        with patch.object(self.s.render_cache, 'render', side_effect=ValueError('bad file')) as render:
            for attempt in range(3):
                self.s.native_media.prepare(str(self.audio), 'aac')
                for job in self.s.native_media.jobs.values():
                    with self.assertRaises(ValueError): job['future'].result(timeout=2)
                now[0] += 31
            self.assertEqual(self.s.native_media.prepare(str(self.audio), 'aac'), 'failed')
            self.assertEqual(render.call_count, 3)
            self.assertEqual(self.s.native_media.prepare(str(self.audio), 'aac', retry=True), 'preparing')
            for job in self.s.native_media.jobs.values():
                with self.assertRaises(ValueError): job['future'].result(timeout=2)
            self.assertEqual(render.call_count, 4)

    def test_actual_aac_preparation_streams_decodable_audio(self):
        import subprocess
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','sine=duration=1',
                        '-f','wav',str(self.audio)], check=True)
        deadline = time.monotonic() + 10
        url = None
        while time.monotonic() < deadline:
            _, manifest = self.manifest('aac')
            url = manifest['items'][0]['url']
            if url: break
            time.sleep(.1)
        self.assertIsNotNone(url)
        status, audio, headers = self.raw(url)
        self.assertEqual(status, 200); self.assertEqual(headers['Content-Type'], 'audio/mp4')
        self.assertLess(audio.index(b'moov'), audio.index(b'mdat'))
        subprocess.run(['ffmpeg','-v','error','-i','pipe:0','-f','null','-'], input=audio, check=True)

    def test_different_preparations_do_not_form_an_executor_backlog(self):
        second = Path(self.tmp.name)/'second.mp3'; second.write_bytes(b'other')
        gate = threading.Event()
        with patch.object(self.s.render_cache, 'render', side_effect=lambda *args:gate.wait(3)):
            try:
                self.assertEqual(self.s.native_media.prepare(str(self.audio), 'aac'), 'preparing')
                self.assertEqual(self.s.native_media.prepare(str(second), 'aac'), 'queued')
                self.assertEqual(len(self.s.native_media.jobs), 1)
            finally: gate.set()
            for job in self.s.native_media.jobs.values(): job['future'].result(timeout=2)

    def test_missing_source_marks_only_that_item_unavailable(self):
        from app.errors import CatalogError
        _, initial = self.manifest(); url = initial['items'][0]['url']
        original = self.lib.music._track_file
        def missing(tid):
            if tid == 1: raise CatalogError('track unavailable')
            return original(tid)
        self.lib.music._track_file = missing
        status, manifest = self.manifest()
        self.assertEqual(status, 200)
        self.assertEqual([i['state'] for i in manifest['items']], ['unavailable','ready','ready'])
        self.assertEqual(self.raw(url)[0], 403)

    def test_native_owner_session_binding_and_local_challenge_http(self):
        from types import SimpleNamespace
        from app.live_activity import LiveActivities
        self.s.live_activities=LiveActivities(self.a,self.s.listening,self.tmp.name,sender=SimpleNamespace(send=lambda *a:(200,'')),start_worker=False)
        self.addCleanup(self.s.live_activities.close)
        def request(path, data, token=None):
            c=http.client.HTTPConnection('127.0.0.1',self.s.server_port,timeout=5)
            c.request('POST','/api/v1/native/'+path,json.dumps(data),{'Content-Type':'application/json','Authorization':'Bearer '+(token or self.pair['access_token'])})
            r=c.getresponse();result=r.status,json.loads(r.read());c.close();return result
        before=self.s.listening.state(self.a.identity(self.pair['access_token'],'native')['id'])['listening']
        code,state=request('playback',dict(action='takeover',device_id='b'*32,device_name='Native',expected_revision=before['revision']))
        self.assertEqual(code,200,state)
        epoch=state['owner_epoch']
        data=dict(action='publish',owner_device_id='b'*32,owner_epoch=epoch,service='12'*16,secret='ab'*32)
        self.assertEqual(request('local-presence',data)[0],200)
        remote=self.auth.login({'username':'partyhost','password':'a-strong-password','device_name':'Controller'})
        self.assertEqual(request('local-presence',data,remote['access_token'])[0],403)
        code,challenge=request('local-presence',dict(action='challenge',owner_device_id='b'*32,owner_epoch=epoch,purpose='status'),remote['access_token'])
        self.assertEqual(code,200,challenge)
        import hashlib,hmac
        proof=hmac.new(bytes.fromhex('ab'*32),challenge['nonce'].encode(),hashlib.sha256).hexdigest()
        verify=dict(action='verify',activity_id='local-check',owner_device_id='b'*32,owner_epoch=epoch,nonce=challenge['nonce'],proof=proof)
        self.assertEqual(request('live-activity',verify,remote['access_token']), (200,{'controls_available':True}))
        self.assertEqual(request('live-activity',verify,remote['access_token']), (200,{'controls_available':False}))

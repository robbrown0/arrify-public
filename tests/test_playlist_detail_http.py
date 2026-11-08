import json
import threading
import unittest
import test_party_http as fixtures
from app.playlists import Playlists


class PlaylistDetailHTTPTests(unittest.TestCase):
    setUp = fixtures.PartyHTTPTests.setUp
    tearDown = fixtures.PartyHTTPTests.tearDown
    call = fixtures.PartyHTTPTests.call

    def test_optional_tracks_preserve_order_without_changing_default_response(self):
        self.lib.index_lock = threading.RLock()
        pid = Playlists(self.a,self.lib).edit(self.uid,{'name':'Ordered','track_ids':[3,1,2]})['id']
        path = '/api/v1/playlists/'+str(pid)
        status,body,_ = self.call(path,cookie=self.hostcookie)
        self.assertEqual(status,200)
        self.assertNotIn('tracks',json.loads(body))
        status,body,_ = self.call(path+'?include_tracks=1',cookie=self.hostcookie)
        self.assertEqual(status,200)
        self.assertEqual([t['id'] for t in json.loads(body)['tracks']],[3,1,2])
        self.assertEqual(self.call(path+'?include_tracks=1')[0],403)

    def test_private_playlist_denied_before_track_metadata_is_read(self):
        self.lib.index_lock = threading.RLock()
        pid = Playlists(self.a,self.lib).edit(self.uid,{'name':'Private','track_ids':[1]})['id']
        self.a.register('another','a-strong-password',email='another@example.invalid')
        self.a.approve('another')
        cookie='arrify_session='+self.a.login('another','a-strong-password')
        self.assertEqual(self.call('/api/v1/playlists/'+str(pid)+'?include_tracks=1',cookie=cookie)[0],404)
        Playlists(self.a,self.lib).edit(self.uid,{'id':pid,'visibility':'shared'})
        status,body,_=self.call('/api/v1/playlists/'+str(pid)+'?include_tracks=1',cookie=cookie)
        self.assertEqual(status,200)
        self.assertEqual([t['id'] for t in json.loads(body)['tracks']],[1])

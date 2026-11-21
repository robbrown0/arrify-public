"""Web catalog uses the same indexed pages as iOS, behind web session auth."""
import json
import unittest
import test_party_http as fixture
import test_native_library as catalog_fixture
from app.search import Search
from app.listening import Listening

class WebCatalogHTTPTests(unittest.TestCase):
    call = fixture.PartyHTTPTests.call
    tearDown = fixture.PartyHTTPTests.tearDown
    def setUp(self):
        fixture.PartyHTTPTests.setUp(self)
        catalog = catalog_fixture.NativeLibraryTests(); catalog.setUp()
        self.s.library = catalog.lib
        for track in catalog.lib.tracks.values(): track['quality']='Lossless'
        catalog.lib.track = lambda tid: catalog.lib.tracks[tid]
        self.p.lib = catalog.lib
        self.s.listening = Listening(self.p)
        artist = {'id': 'b'*24, 'name': 'P!nk', 'track_ids': list(catalog.lib.tracks)}
        catalog.lib.artists = {artist['id']: artist}
        catalog.lib.search = Search(list(catalog.lib.tracks.values()), list(catalog.lib.albums.values()), [artist])
    def test_auth_scoped_queries_sort_and_paging(self):
        self.assertIn(self.call('/api/v1/catalog')[0], (401,403))
        for kind,term in [('tracks','Song'),('albums','Album'),('artists','P!nk')]:
            code,raw,_ = self.call('/api/v1/catalog?kind='+kind+'&q='+term+'&sort=recent&limit=1', cookie=self.hostcookie)
            self.assertEqual(code,200,raw)
            data=json.loads(raw);self.assertEqual(data['kind'],kind);self.assertEqual(len(data['items']),1)
        code,raw,_ = self.call('/api/v1/catalog?kind=tracks&sort=title&limit=50',cookie=self.hostcookie)
        self.assertEqual(code,200,raw);first=json.loads(raw)
        self.assertEqual(first['items'][0]['quality'],'Lossless')
        code,raw,_ = self.call('/api/v1/catalog?kind=tracks&sort=title&limit=50&offset=50&revision='+first['revision'],cookie=self.hostcookie)
        self.assertEqual(code,200,raw);second=json.loads(raw)
        self.assertFalse({x['id'] for x in first['items']} & {x['id'] for x in second['items']})
        code,raw,_ = self.call('/api/v1/catalog?kind=tracks&offset=50&revision=stale',cookie=self.hostcookie)
        self.assertEqual(code,409,raw)
        self.assertEqual(self.call('/api/v1/catalog?sort=unsupported',cookie=self.hostcookie)[0],400)

    def test_play_source_expands_beyond_visible_page_and_preserves_sort(self):
        source = {'kind':'catalog', 'id':json.dumps({'q':'Song','sort':'title'})}
        code,raw,_ = self.call('/api/v1/listening', {'action':'play','device_id':'a'*32,
            'expected_revision':0,'source':source},cookie=self.hostcookie,csrf=self.a.csrf(self.session))
        self.assertEqual(code,200,raw)
        state=json.loads(raw)
        self.assertEqual(len(state['upcoming']),119)
        self.assertEqual(state['playback_context']['id'],source['id'])
        self.assertEqual(state['current'],1)
        self.assertEqual(state['upcoming'][:2],[2,3])
        revision=state['listening']['revision']
        code,raw,_ = self.call('/api/v1/listening', {'action':'play','device_id':'a'*32,
            'expected_revision':revision,'source':source,'shuffle':True},cookie=self.hostcookie,csrf=self.a.csrf(self.session))
        self.assertEqual(code,200,raw)
        shuffled=json.loads(raw)
        self.assertTrue(shuffled['shuffle'])
        self.assertEqual(set([shuffled['current']]+shuffled['upcoming']),set(range(1,121)))
        code,raw,_ = self.call('/api/v1/listening', {'action':'play','device_id':'a'*32,
            'expected_revision':shuffled['listening']['revision'],'source':source,'track_id':999},cookie=self.hostcookie,csrf=self.a.csrf(self.session))
        self.assertEqual(code,409,raw)

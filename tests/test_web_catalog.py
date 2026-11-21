"""Web catalog uses the same indexed pages as iOS, behind web session auth."""
import json
import unittest
from types import SimpleNamespace
import test_party_http as fixture
import test_native_library as catalog_fixture
from app.search import Search

class WebCatalogHTTPTests(unittest.TestCase):
    call = fixture.PartyHTTPTests.call
    tearDown = fixture.PartyHTTPTests.tearDown
    def setUp(self):
        fixture.PartyHTTPTests.setUp(self)
        catalog = catalog_fixture.NativeLibraryTests(); catalog.setUp()
        self.s.library = catalog.lib
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
        code,raw,_ = self.call('/api/v1/catalog?kind=tracks&sort=title&limit=50&offset=50&revision='+first['revision'],cookie=self.hostcookie)
        self.assertEqual(code,200,raw);second=json.loads(raw)
        self.assertFalse({x['id'] for x in first['items']} & {x['id'] for x in second['items']})
        code,raw,_ = self.call('/api/v1/catalog?kind=tracks&offset=50&revision=stale',cookie=self.hostcookie)
        self.assertEqual(code,409,raw)
        self.assertEqual(self.call('/api/v1/catalog?sort=unsupported',cookie=self.hostcookie)[0],400)

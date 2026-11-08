import json
import unittest
from unittest.mock import Mock
import test_party_http as fixtures
from app.playlists import Playlists
from app import party_import


class PlaylistImportTests(unittest.TestCase):
    call=fixtures.PartyHTTPTests.call
    join=fixtures.PartyHTTPTests.join
    tearDown=fixtures.PartyHTTPTests.tearDown
    def setUp(self):
        fixtures.PartyHTTPTests.setUp(self)
        self.lib.collections=lambda:[dict(slug='built-in',name='Built in playlist',section='playlists',tracks=3,artwork=''),dict(slug='mood',name='Mood',section='moods',tracks=2)]
        self.lib.collection_ids=lambda slug:[3,1,2]
        self.personal=Playlists(self.a,self.lib)
        self.pid=self.personal.edit(self.uid,dict(name='My list',track_ids=[2,1,3]))['id']
        self.path='/api/v1/parties/'+self.party['id']+'/import-playlists'
    def get(self,suffix=''):return self.call(self.path+suffix,cookie=self.hostcookie)
    def post(self,data):return self.call(self.path,data,self.hostcookie,self.a.csrf(self.session))
    def test_lists_personal_shared_and_playlist_section_only(self):
        self.a.register('otherhost','a-strong-password',email='other@example.invalid');self.a.approve('otherhost')
        other=self.a.db.execute("SELECT id FROM users WHERE username='otherhost'").fetchone()[0]
        shared=self.personal.edit(other,dict(name='Shared list',track_ids=[4],visibility='shared'))['id']
        private=self.personal.edit(other,dict(name='Private list',track_ids=[5]))['id']
        code,body,_=self.get();self.assertEqual(code,200)
        items=json.loads(body)['items'];self.assertEqual({p['group'] for p in items},{'mine','shared','collection'})
        self.assertNotIn('Private list',[p['name'] for p in items]);self.assertNotIn('Mood',[p['name'] for p in items])
        self.assertEqual(self.get('?kind=personal&id='+str(private))[0],403)
        self.assertEqual(self.get('?kind=personal&id='+str(shared))[0],200)
        self.assertEqual(self.get('?kind=collection&id=built-in')[0],200)
    def test_append_fifo_skip_duplicates_and_keep_playing(self):
        data=dict(kind='personal',playlist_id=self.pid,track_ids=[1,2,3])
        result=json.loads(self.post(data)[1]);self.assertEqual(result['added'],3)
        self.assertEqual([t['id'] for t in result['party']['items']],[2,1,3])
        self.assertEqual(json.loads(self.post(data)[1])['duplicates'],3)
        self.assertEqual(self.personal.get(self.uid,self.pid)['track_ids'],[2,1,3])
        self.assertEqual(self.post(dict(kind='personal',playlist_id=self.pid,track_ids=[8]))[0],400)
        from app.listening import Listening
        Listening(self.p).command(self.uid,dict(action='play',party_id=self.party['id'],device_id='a'*32,expected_revision=0,owner_epoch=0))
        extra=self.personal.edit(self.uid,dict(name='More songs',track_ids=[4,5]))['id']
        self.assertEqual(self.post(dict(kind='personal',playlist_id=extra,track_ids=[4,5]))[0],200)
        self.assertEqual(self.p.load(self.uid)['current'],2)
        self.assertEqual(self.p.load(self.uid)['upcoming'],[1,3,4,5])
    def test_guests_nonowners_wrong_source_and_ended_are_rejected(self):
        cookie,csrf=self.join();data=dict(kind='personal',playlist_id=self.pid,track_ids=[1])
        self.assertEqual(self.call(self.path,data,cookie,csrf)[0],403)
        self.assertEqual(self.call(self.path,data,self.hostcookie,'wrong')[0],403)
        self.assertEqual(self.post(dict(kind='apple',track_ids=['1']))[0],400)
        self.s.parties.host(self.uid,self.party['id'],dict(action='end'))
        self.assertEqual(self.post(data)[0],410)
    def test_host_import_does_not_consume_guest_slots(self):
        cookie,csrf=self.join();self.assertEqual(self.post(dict(kind='collection',playlist_id='built-in',track_ids=[3,1,2]))[0],200)
        self.assertEqual(json.loads(self.call(self.public,cookie=cookie)[1])['quota']['available'],3)
    def test_catalog_verification_and_ended_during_provider_read(self):
        self.a.db.execute("UPDATE parties SET source='apple',storefront='us' WHERE id=?",(self.party['id'],))
        self.s.parties.apple=Mock();self.s.parties.apple.tracks.return_value=[dict(id='10',title='Apple song',artist='Artist',album='Album',provider='apple')]
        result=json.loads(self.post(dict(kind='apple',track_ids=['10','11']))[1]);self.assertEqual(result['added'],1);self.assertEqual(result['unavailable'],1)
        def end(*args):
            self.s.parties.host(self.uid,self.party['id'],dict(action='end'));return [dict(id='12',title='Late',artist='Artist')]
        self.s.parties.apple.tracks.side_effect=end
        self.assertEqual(self.post(dict(kind='apple',track_ids=['12']))[0],410)
    def test_preview_truncates_and_capacity_is_enforced(self):
        self.lib.collection_ids=lambda slug:list(range(1,601))
        self.lib.track=lambda tid:dict(id=tid,title='Song '+str(tid),artist='Artist',album='Album')
        code,body,_=self.get('?kind=collection&id=built-in');self.assertEqual(code,200);self.assertTrue(json.loads(body)['truncated']);self.assertEqual(len(json.loads(body)['tracks']),500)
        result=json.loads(self.post(dict(kind='collection',playlist_id='built-in',track_ids=list(range(1,501))))[1]);self.assertEqual(result['added'],500)
        result=json.loads(self.post(dict(kind='collection',playlist_id='built-in',track_ids=[501]))[1]);self.assertEqual(result['full'],1)


class CatalogBatchTests(unittest.TestCase):
    def test_catalog_batch_reorders_response_and_skips_unavailable(self):
        from app.apple_music import AppleMusic
        service=AppleMusic.__new__(AppleMusic);paths=[]
        def cached(path):
            paths.append(path)
            return {'data':[{'id':sid,'attributes':{'name':'Song','artistName':'Artist','playParams':{'id':sid}}} for sid in ['1','2']]}
        service.cached=cached
        self.assertEqual([t['id'] for t in service.tracks('us',['2','1','3'])],['2','1'])
        self.assertEqual(len(paths),1)
        with self.assertRaises(ValueError):service.tracks('us',['https://bad.invalid/'])

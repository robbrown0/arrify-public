import unittest
from hitdiscovery.request_catalog import catalog,choose_release,query_text

RID='11111111-1111-1111-1111-111111111111'
AID='22222222-2222-2222-2222-222222222222'
GID='33333333-3333-3333-3333-333333333333'
REL='44444444-4444-4444-4444-444444444444'
def recording():
    return {'id':RID,'title':'Test Song','score':100,'first-release-date':'1984-01-01','artist-credit':[{'artist':{'id':AID,'name':'Test Artist'}}],
      'releases':[{'id':REL,'title':'Original Album','status':'Official','date':'1984-01-01','country':'US','release-group':{'id':GID,'title':'Original Album','primary-type':'Album'}}]}
class CatalogTests(unittest.TestCase):
    def test_album_before_single_and_compilation(self):
        r=recording();r['releases'].insert(0,dict(r['releases'][0],title='Single',**{'release-group':{'id':GID,'title':'Single','primary-type':'Single'}}))
        r['releases'].insert(0,dict(r['releases'][1],title='Hits',**{'release-group':{'id':GID,'title':'Hits','primary-type':'Album','secondary-types':['Compilation']}}))
        r['first-release-date']='1980-01-01';chosen=choose_release(r);self.assertEqual(chosen['album'],'Original Album');self.assertEqual(chosen['recording_id'],RID);self.assertEqual(chosen['album_year'],'1984');self.assertEqual(chosen['release_date'],'1980-01-01')
    def test_linked_selection_and_dedup(self):
        r=recording();calls=[]
        def get(path,**params):calls.append((path,params));return {'recordings':[r,r]} if path=='recording/' else r
        out=catalog('Test Artist Test Song',get)['items']
        self.assertEqual(len(out),1);self.assertEqual(out[0]['resolved']['release_id'],REL)
        self.assertTrue(out[0]['requestable']);self.assertEqual(out[0]['album'],'Original Album')
        self.assertIn('AND',calls[1][1]['query']);self.assertEqual(len(calls),4);self.assertNotEqual(out[0]['selection'],RID)
    def test_unverified_album_never_requestable(self):
        r=recording();r['releases']=[]
        out=catalog('Test Song',lambda path,**kw:{'recordings':[r]} if path=='recording/' else r)['items']
        self.assertFalse(out[0]['requestable']);self.assertEqual(out[0]['album'],'')
    def test_future_and_wrong_artist_rejected(self):
        r=recording();r['releases'][0]['date']='2999';self.assertIsNone(choose_release(r))
        r=recording();r['releases'][0]['artist-credit']=[{'artist':{'id':GID,'name':'Other'}}];self.assertIsNone(choose_release(r))
    def test_queries_escape_syntax_and_keep_artist_title(self):
        q=query_text('AC/DC "Song" OR *:*')
        self.assertIn('artist:"AC"',q);self.assertIn('recording:"Song"',q)
        self.assertIn('\\"Song\\"',q)

    def test_merle_haggard_is_artist_not_song_named_after_him(self):
        r=recording();r['artist-credit'][0]['artist']['name']='Merle Haggard';calls=[]
        wrong=dict(r,id=GID,title='Merle Haggard',**{'artist-credit':[{'artist':{'id':GID,'name':'Other Singer'}}]})
        def get(path,**params):
            calls.append((path,params))
            if path=='artist/':return {'artists':[{'id':AID,'name':'Merle Haggard','score':100}]}
            if path=='recording/':return {'recordings':[wrong,r]}
            return r
        out=catalog('Merle Haggard',get)['items']
        self.assertEqual(len(out),1);self.assertEqual(out[0]['artist'],'Merle Haggard')
        self.assertEqual(out[0]['album'],'Original Album');self.assertIn('arid:'+AID,calls[1][1]['query'])

    def test_page_size_and_provider_partial_results(self):
        records=[]
        for i in range(9):
            r=recording();r['id']=f'{i+10:08x}-1111-1111-1111-111111111111';r['title']='Song '+str(i);records.append(r)
        def get(path,**params):
            if path=='artist/':return {'artists':[]}
            if path=='recording/':return {'recordings':records}
            return {'releases':recording()['releases']}
        first=catalog('Test Artist',get)
        self.assertEqual(len(first['items']),6);self.assertEqual(first['next_offset'],6)
        second=catalog('Test Artist',get,6)
        self.assertEqual(len(second['items']),3);self.assertIsNone(second['next_offset'])
        def partial(path,**params):
            if path=='release/':raise TimeoutError('provider down')
            return get(path,**params)
        result=catalog('Test Artist',partial)
        self.assertTrue(result['incomplete']);self.assertEqual(result['items'],[])

    def test_artist_song_query_prefers_actual_artist_over_cover_title_mentions(self):
        original=recording();original.update(title='Mama Tried',score=14);original['artist-credit'][0]['artist']['name']='Merle Haggard'
        cover=recording();cover.update(id=GID,title='Mama Tried [Merle Haggard]',score=100);cover['artist-credit']=[{'artist':{'id':GID,'name':'Other Singer'}}]
        def get(path,**params):
            if path=='artist/':return {'artists':[]}
            if path=='recording/':return {'recordings':[cover,original]}
            return {'releases':recording()['releases']}
        result=catalog('Mama Tried Merle Haggard',get)['items']
        self.assertEqual(len(result),1);self.assertEqual(result[0]['title'],'Mama Tried');self.assertEqual(result[0]['artist'],'Merle Haggard')
        self.assertTrue(result[0]['requestable'])
        self.assertNotIn('artist:"by"',query_text('Mama Tried by Merle Haggard'))

    def test_structured_album_lead_finds_recording_beyond_initial_matches(self):
        old=recording();old.update(title='Mama Tried',score=14);old['artist-credit'][0]['artist']['name']='Merle Haggard';old['releases']=[]
        actual=recording();actual.update(id=GID,title='Mama Tried');actual['artist-credit'][0]['artist']['name']='Merle Haggard';actual['artist-credit'].append({'artist':{'id':REL,'name':'The Strangers'}})
        actual['releases'][0]['release-group']['title']='Mama Tried'
        def get(path,**params):
            if path=='artist/':return {'artists':[]}
            if path=='recording/':return {'recordings':[actual if 'release:' in params['query'] else old]}
            return {'releases':actual['releases'] if params['recording']==GID else []}
        result=catalog('Mama Tried Merle Haggard',get)['items']
        self.assertEqual(len(result),1);self.assertEqual(result[0]['album'],'Mama Tried')
        self.assertTrue(result[0]['requestable']);self.assertEqual(result[0]['recording_id'],GID)

class RadioSelectionTests(unittest.TestCase):
    def test_known_recording_uses_same_pinned_official_album_rules(self):
        from hitdiscovery.request_catalog import recording_selection
        r=recording();calls=[]
        def get(path,**params):calls.append(path);return {'releases':r['releases']} if path=='release/' else dict(r)
        out=recording_selection(RID,get)['items'][0]
        self.assertTrue(out['requestable']);self.assertEqual(out['resolved']['release_group_id'],GID)
        self.assertEqual(out['album'],'Original Album');self.assertNotEqual(out['selection'],RID)
        self.assertEqual(calls,['recording/'+RID,'release/'])
    def test_unverified_or_mismatched_recording_cannot_create_request(self):
        from hitdiscovery.request_catalog import recording_selection
        r=recording();r['releases']=[]
        result=recording_selection(RID,lambda p,**_: {'releases':[]} if p=='release/' else r)
        self.assertFalse(result['items'][0]['requestable'])
        with self.assertRaises(ValueError):recording_selection('https://bad',lambda *a,**k:r)
        with self.assertRaises(ValueError):recording_selection(RID,lambda *a,**k:{**r,'id':REL})

class DirectSongSelectionTests(unittest.TestCase):
    def get(self,path,**params):
        r=recording()
        if path=='recording/':return {'recordings':[r],'recording-count':1}
        if path=='release/':return {'releases':r['releases']}
        return r
    def test_named_song_resolves_verified_album_and_stable_selection(self):
        from hitdiscovery.request_catalog import song_selection
        d=song_selection('Test Artist','Test Song',self.get);self.assertEqual(len(d['items']),1);self.assertEqual(d['items'][0]['album'],'Original Album');self.assertNotEqual(d['items'][0]['selection'],RID);self.assertEqual(d['requested'],{'artist':'Test Artist','title':'Test Song'})
    def test_cover_and_unrequested_live_version_not_selected(self):
        from hitdiscovery.request_catalog import song_selection
        for field,value in [('title','Test Song (Live)'),('artist-credit',[{'artist':{'id':AID,'name':'Cover Artist'}}])]:
            def get(path,**params):
                r=recording();r[field]=value;return {'recordings':[r]} if path=='recording/' else r
            with self.assertRaises(ValueError):song_selection('Test Artist','Test Song',get)
    def test_unverified_and_truncated_candidates_not_submitted(self):
        from hitdiscovery.request_catalog import song_selection
        def no_release(path,**params):
            if path=='recording/':return {'recordings':[recording()]}
            if path=='release/':return {'releases':[]}
            return recording()
        with self.assertRaises(ValueError):song_selection('Test Artist','Test Song',no_release)
        with self.assertRaises(ValueError):song_selection('Test Artist','Test Song',lambda *a,**k:{'recordings':[recording()],'recording-count':100})
    def test_multiple_conflicting_albums_do_not_choose_first(self):
        from hitdiscovery.request_catalog import song_selection
        other='22222222-2222-4222-8222-222222222222';group='33333333-3333-4333-8333-333333333333'
        def get(path,**params):
            r=recording()
            if path=='recording/':return {'recordings':[r,r|{'id':other}]}
            if path=='recording/'+other:r['id']=other
            if path=='release/':
                if params['recording']==other:r['releases'][0]['release-group']['id']=group
                return {'releases':r['releases']}
            return r
        with self.assertRaises(ValueError):song_selection('Test Artist','Test Song',get)
    def test_guest_credit_is_verified_as_part_of_identity(self):
        from hitdiscovery.request_catalog import song_selection
        def get(path,**params):
            r=recording();r['artist-credit']=[{'artist':{'id':AID,'name':'Test Artist'},'joinphrase':' feat. '},{'artist':{'id':GID,'name':'Guest'}}]
            if path=='recording/':return {'recordings':[r]}
            if path=='release/':return {'releases':r['releases']}
            return r
        d=song_selection('Test Artist','Test Song (feat. Guest)',get);self.assertEqual(d['items'][0]['album'],'Original Album')
        with self.assertRaises(ValueError):song_selection('Test Artist','Test Song (feat. Wrong Guest)',get)
    def test_band_name_with_ampersand_is_not_reduced_to_primary_artist(self):
        from hitdiscovery.request_catalog import song_selection
        def get(path,**params):
            r=recording();r['artist-credit'][0]['artist']['name']='Earth, Wind & Fire'
            if path=='recording/':return {'recordings':[r]}
            if path=='release/':return {'releases':r['releases']}
            return r
        self.assertTrue(song_selection('Earth, Wind & Fire','Test Song',get)['items'][0]['requestable'])

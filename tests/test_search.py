import unittest
from app.search import RankedIndex,Search,normalize,near

class SearchTests(unittest.TestCase):
    def index(self):
        return RankedIndex([
            {'id':1,'title':'Tiny Dancer','artist':'Elton John','album':'Madman Across the Water'},
            {'id':2,'title':'Tiny Dancer (Live)','artist':'Elton John','album':'Live'},
            {'id':3,'title':'Another Song','artist':'Tiny Dancer','album':'Other'},
            {'id':4,'title':'Don’t Stop','artist':'Fleetwood Mac','album':'Rumours'},
            {'id':5,'title':'Halo','artist':'Beyoncé','album':'I Am… Sasha Fierce'},
            {'id':6,'title':'Thunderstruck','artist':'AC/DC','album':'The Razors Edge'}],('title','artist','album'))
    def test_ranking(self):
        s=self.index();r=s.search('Tiny Dancer')
        self.assertEqual(r['items'][0]['id'],1)
        self.assertEqual(r['items'][1]['id'],3)
        self.assertFalse(r['fuzzy'])
        self.assertEqual(s.search('elton tiny')['items'][0]['id'],1)
        self.assertEqual(s.search('water dancer')['items'][0]['id'],1)
        self.assertEqual(s.search('john elton')['total'],2)
    def test_normalization(self):
        s=self.index()
        for q,key in [('beyonce',5),('BEYONCÉ',5),('dont stop',4),('acdc',6),('ac dc',6),('the razors',6)]:
            self.assertEqual(s.search(q)['items'][0]['id'],key,q)
        self.assertEqual(normalize('  Don’t—Stop!  '),'dont stop')
    def test_prefix_typo_and_limits(self):
        s=self.index()
        self.assertEqual(s.search('elt dan')['items'][0]['id'],1)
        for q in ['Flleetwood Mac','Fleetwodo Mac','Gleetwood Mac']:
            self.assertEqual(s.search(q)['items'][0]['id'],4,q)
            self.assertTrue(s.search(q)['fuzzy'])
        self.assertFalse(s.search('zzzz unrelated')['items'])
        self.assertFalse(s.search('one two three four five six seven eight nine')['items'])
        self.assertFalse(s.search(' % _ * \" <script> ')['items'])
        self.assertFalse(s.search('  ')['items'])
        self.assertTrue(near('fleetwood','fleetwodo'))
        self.assertFalse(near('fleetwood','fleatwod'))
    def test_stable_pages(self):
        s=self.index();all_ids=[x['id'] for x in s.search('elton')['items']]
        pages=[s.search('elton',offset=i,limit=1)['items'][0]['id'] for i in range(2)]
        self.assertEqual(all_ids,pages)
        self.assertEqual(all_ids,[x['id'] for x in s.search('elton')['items']])
    def test_public_entity_results(self):
        s=Search([{'id':1,'title':'Halo','artist':'Beyoncé','album':'Test'}],[{'id':'a','name':'Test','artist':'Beyoncé','track_ids':[1]}],[{'id':'r','name':'Beyoncé','track_ids':[1]}])
        r=s.query('beyonce');self.assertEqual(r['total'],1)
        self.assertNotIn('track_ids',r['artists'][0]);self.assertNotIn('track_ids',r['albums'][0])
        self.assertEqual(r['artists'][0]['tracks'],1)

class ArtistAliasTests(unittest.TestCase):
    def test_pink_alias_ranks_exact_artist_first_without_hiding_other_artists(self):
        index=RankedIndex([{'id':1,'name':'P!nk'},{'id':2,'name':'Pink Floyd'},{'id':3,'name':'PinkPantheress'}],('name',))
        for query in ('Pink','P!nk','P!NK','pin'):
            result=index.search(query)
            self.assertEqual(result['items'][0]['id'],1)
            self.assertEqual(result['items'][0]['name'],'P!nk')
            self.assertFalse(result['fuzzy'])
        self.assertEqual(index.search('Pink Floyd')['items'][0]['id'],2)
    def test_combined_artist_and_album_or_song_queries(self):
        index=RankedIndex([{'id':1,'title':'Trouble','artist':'P!nk','album':'Try This'}],('title','artist','album'))
        for query in ('pink trouble','pink try this','p!nk trouble'):
            self.assertEqual(index.search(query)['total'],1)
        self.assertEqual(normalize('Stop! Now'),'stop now')

class SharedArtistAliasTests(unittest.TestCase):
    def test_aliases_across_artists_songs_and_albums(self):
        pairs=[('Kesha','Ke$ha'),('ASAP Rocky','A$AP Rocky'),('ASAP Ferg','A$AP Ferg'),('Ty Dolla Sign','Ty Dolla $ign'),('Churches','CHVRCHES'),('ACDC','AC/DC'),('AC DC','AC/DC'),('Beyonce','Beyoncé'),('Motorhead','Motörhead'),('REM','R.E.M.')]
        for typed,official in pairs:
            with self.subTest(artist=official,query=typed):
                search=Search([{'id':1,'title':'Example Song','artist':official,'album':'Example Album'}],[{'id':2,'name':'Example Album','artist':official,'track_ids':[1]}],[{'id':3,'name':official,'track_ids':[1]}])
                for query in (typed,official,typed.upper()):
                    result=search.query(query)
                    self.assertEqual(result['artists'][0]['name'],official)
                    self.assertEqual(result['tracks'][0]['id'],1)
                    self.assertEqual(result['albums'][0]['id'],2)
                self.assertEqual(search.query(typed+' Example Song')['tracks'][0]['id'],1)
                self.assertEqual(search.query(typed+' Example Album')['albums'][0]['id'],2)
    def test_no_general_symbol_substitution_or_embedded_alias_rewrite(self):
        self.assertEqual(normalize('Money $ign'),'money ign')
        self.assertEqual(normalize('Stop! Now'),'stop now')
        self.assertEqual(normalize('xCHVRCHESx'),'xchvrchesx')
        self.assertEqual(normalize('A$AP Someone Else'),'a ap someone else')
    def test_plain_and_stylized_indexed_names_match_both_directions(self):
        index=RankedIndex([{'id':1,'name':'Kesha'},{'id':2,'name':'ASAP Rocky'}],('name',))
        self.assertEqual(index.search('Ke$ha')['items'][0]['id'],1)
        self.assertEqual(index.search('A$AP Rocky')['items'][0]['id'],2)

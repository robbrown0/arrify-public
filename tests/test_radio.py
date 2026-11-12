import json
import random
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from app.accounts import Accounts
from app.library import Player
from app.radio import Radio,RadioIndex,SimilarArtists,artist_key,recording_key

MBID='deb2448c-6fdf-4d31-921e-757630515dbd'
RID='616fb500-9a1a-4ac9-9d18-fc4358897b2b'

def track(i,artist,title=None):
    return {'id':i,'artist':artist,'artist_id':artist_key(artist).replace(' ','')+'a'*16,'title':title or 'Song '+str(i),'album':'Album','artwork':'/api/v1/artwork/'+str(i)}

class RadioTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.accounts=Accounts(self.tmp.name)
        self.tracks={i:track(i,a) for i,a in [(1,'Alpha'),(2,'Alpha'),(3,'Beta'),(4,'Beta'),(5,'Gamma'),(6,'Unrelated')]}
        self.memberships=[('rock',1),('rock',3),('broad',2),('broad',5)]
        self.lib=SimpleNamespace(index_lock=threading.RLock(),radio_index=RadioIndex(self.tracks,self.memberships))
        def lookup(i):
            if i not in self.lib.radio_index.tracks:raise ValueError('Unavailable')
            return self.lib.radio_index.tracks[i]
        self.lib.track=lookup;self.related=SimilarArtists(self.tmp.name,enabled=False);self.radio=Radio(self.tmp.name,self.lib,self.related);self.radio.rng=random.Random(42);self.lib.radio=self.radio;self.player=Player(self.accounts,self.lib)
    def tearDown(self):self.related.db.close();self.accounts.db.close();self.tmp.cleanup()
    def test_shuffle_toggle_restores_remaining_occurrences_after_edits(self):
        self.player.command(1,{'action':'play','ids':[1,2,3,2,4]})
        with patch('app.library.secrets.SystemRandom') as rng:
            rng.return_value.shuffle.side_effect=lambda queue:queue.reverse()
            shuffled=self.player.command(1,{'action':'shuffle'})
        self.assertEqual(shuffled['upcoming'],[4,2,3,2])
        self.assertNotIn('shuffle_order',shuffled)
        self.assertEqual(self.player.command(1,{'action':'next'})['current'],4)
        self.player.command(1,{'action':'remove','index':0})
        self.player.command(1,{'action':'queue','ids':[6]})
        restored=self.player.command(1,{'action':'shuffle'})
        self.assertFalse(restored['shuffle'])
        self.assertEqual(restored['current'],4)
        self.assertEqual(restored['upcoming'],[2,3,6])
        self.assertEqual(self.player.command(1,{'action':'next'})['current'],2)

    def test_shuffle_duplicate_removal_preserves_the_correct_occurrence(self):
        self.player.command(1,{'action':'play','ids':[1,2,3,2,4]})
        with patch('app.library.secrets.SystemRandom') as rng:
            rng.return_value.shuffle.side_effect=lambda queue:queue.reverse()
            self.player.command(1,{'action':'shuffle'})
        self.player.command(1,{'action':'remove','index':3})
        restored=self.player.command(1,{'action':'shuffle'})
        self.assertEqual(restored['upcoming'],[3,2,4])

    def test_shuffle_play_keeps_original_order_for_toggle_off(self):
        with patch('app.library.secrets.SystemRandom') as rng:
            rng.return_value.shuffle.side_effect=lambda queue:queue.reverse()
            state=self.player.command(1,{'action':'play','ids':[1,2,3,4],'shuffle':True})
        self.assertTrue(state['shuffle'])
        self.assertEqual(state['current'],4)
        self.assertEqual(state['upcoming'],[3,2,1])
        self.assertEqual(self.player.command(1,{'action':'shuffle'})['upcoming'],[1,2,3])
        self.assertFalse(self.player.command(1,{'action':'play','ids':[3,2,1]})['shuffle'])

    def test_song_seed_first_and_only_similarity_qualified_local_music(self):
        d=self.radio.preview('track','1');self.assertEqual(d['tracks'][0]['id'],1)
        self.assertNotIn(6,[t['id'] for t in d['tracks']]);self.assertGreater(d['artists'],1)
        self.assertEqual(len({recording_key(t) for t in d['tracks']}),len(d['tracks']))
    def test_verified_external_neighbours_take_precedence_over_broad_context(self):
        self.related.cache['alpha']=(100,['Beta']);self.related.popular[('unrelated','song 6')]=10**9
        d=self.radio.preview('track','1');self.assertEqual({t['artist'] for t in d['tracks']},{'Alpha','Beta'})
    def test_invalid_artist_and_track_seeds_rejected(self):
        for kind,seed in [('track','999'),('artist','missing'),('url','https://bad')]:
            with self.assertRaises(ValueError):self.radio.preview(kind,seed)
    def test_stable_next_manual_queue_and_clear(self):
        s=self.player.command(1,{'action':'play','radio':{'kind':'track','seed':'1'}});nxt=s['next']['id']
        self.assertEqual(s['current'],1);self.assertEqual(self.player.state(1)['next']['id'],nxt)
        self.player.command(1,{'action':'queue','ids':[6]})
        moved=self.player.command(1,{'action':'next','expected_current':1,'expected_next':nxt,'transition_id':'radio-next'})
        self.assertEqual(moved['current'],nxt);self.assertEqual(moved['next']['id'],6)
        self.assertEqual(self.player.command(1,{'action':'next','transition_id':'radio-next'})['current'],nxt)
        cleared=self.player.command(1,{'action':'clear'});self.assertIsNone(cleared['station']);self.assertFalse(cleared['upcoming'])

    def test_displayed_mix_advances_in_exact_order_and_stops_at_end(self):
        ids=[t['id'] for t in self.radio.preview('track','1')['tracks']]
        self.assertGreater(len(ids),2)
        with patch.object(self.radio,'pick',side_effect=AssertionError('Mix must not pick a random next song')):
            state=self.player.command(1,{'action':'play','radio':{'kind':'track','seed':'1','ids':ids}})
            self.assertEqual(state['station']['mode'],'mix');self.assertFalse(state['shuffle'])
            self.assertEqual(state['upcoming'],ids[1:])
            for index,track_id in enumerate(ids):
                self.assertEqual(state['current'],track_id)
                self.assertEqual(state['upcoming'],ids[index+1:])
                self.assertEqual(Player(self.accounts,self.lib).state(1)['upcoming'],ids[index+1:])
                state=self.player.command(1,{'action':'next'})
            self.assertIsNone(state['current']);self.assertEqual(state['upcoming'],[])

    def test_selected_mix_row_starts_at_that_position_and_previous_returns_in_order(self):
        ids=[1,3,2,4]
        state=self.player.command(1,{'action':'play','radio':{'kind':'track','seed':'1','ids':ids},'track_id':3})
        self.assertEqual(state['current'],3);self.assertEqual(state['upcoming'],[2,4])
        self.player.command(1,{'action':'next','expected_current':3,'expected_next':2,'transition_id':'mix-next'})
        self.assertEqual(self.player.command(1,{'action':'next','transition_id':'mix-next'})['current'],2)
        previous=self.player.command(1,{'action':'previous'})
        self.assertEqual(previous['current'],3);self.assertEqual(previous['upcoming'],[2,4])

    def test_mix_shuffle_is_explicit_preserves_membership_and_play_restores_order(self):
        selection={'action':'play','radio':{'kind':'track','seed':'1','ids':[1,3,2,4]}}
        self.player.command(1,selection)
        with patch('app.library.secrets.SystemRandom') as rng:
            rng.return_value.shuffle.side_effect=lambda queue:queue.reverse()
            shuffled=self.player.command(1,{'action':'shuffle'})
        self.assertEqual(shuffled['current'],1);self.assertEqual(shuffled['upcoming'],[4,2,3]);self.assertTrue(shuffled['shuffle'])
        self.assertEqual(self.player.command(1,{'action':'next'})['current'],4)
        restored=self.player.command(1,selection)
        self.assertEqual(restored['upcoming'],[3,2,4]);self.assertFalse(restored['shuffle'])

    def test_mix_snapshot_survives_new_recommendations_and_skips_removed_files(self):
        self.player.command(1,{'action':'play','radio':{'kind':'track','seed':'1','ids':[1,3,2,4]}})
        self.related.cache['alpha']=(500,['Gamma'])
        self.assertEqual(self.player.state(1)['upcoming'],[3,2,4])
        tracks=dict(self.tracks);tracks.pop(3)
        self.lib.radio_index=RadioIndex(tracks,self.memberships)
        self.assertEqual(self.player.state(1)['upcoming'],[2,4])
        self.assertEqual(self.player.command(1,{'action':'next'})['current'],2)

    def test_mix_queue_edits_do_not_refill_randomly(self):
        self.player.command(1,{'action':'play','radio':{'kind':'track','seed':'1','ids':[1,3,2]}})
        self.player.command(1,{'action':'queue','ids':[6]})
        self.assertEqual(self.player.command(1,{'action':'reorder','from':2,'to':0})['upcoming'],[6,3,2])
        for _ in range(3):self.player.command(1,{'action':'remove','index':0})
        self.assertEqual(self.player.state(1)['upcoming'],[])
        self.assertIsNone(self.player.command(1,{'action':'next'})['current'])

    def test_invalid_mix_snapshot_does_not_replace_existing_playback(self):
        self.player.command(1,{'action':'play','ids':[1,2]})
        before=self.player.load(1)
        for ids in ([],[1,1],[True],['1'],[0],None,list(range(1,52)),[999]):
            with self.subTest(ids=ids),self.assertRaises(ValueError):
                self.player.command(1,{'action':'play','radio':{'kind':'track','seed':'1','ids':ids}})
            self.assertEqual(self.player.load(1),before)
        with self.assertRaises(ValueError):self.player.command(1,{'action':'play','radio':{'kind':'track','seed':'1','ids':[1,2]},'track_id':3})

    def test_active_legacy_station_keeps_its_existing_rotation_behavior(self):
        state=self.player.command(1,{'action':'play','radio':{'kind':'track','seed':'1'}})
        self.assertNotIn('mode',state['station']);self.assertEqual(len(state['upcoming']),1)
        with self.assertRaises(ValueError):self.player.command(1,{'action':'shuffle'})
    def test_regular_selection_leaves_station_and_foreign_seed_song_refused(self):
        self.player.command(1,{'action':'play','radio':{'kind':'track','seed':'1'}})
        with self.assertRaises(ValueError):self.radio.start(self.radio.describe('track','1'),6)
        normal=self.player.command(1,{'action':'play','ids':[6]});self.assertIsNone(normal['station'])
    def test_new_catalog_content_joins_and_removed_content_leaves(self):
        self.related.cache['alpha']=(100,['Beta']);seed=self.radio.describe('track','1')
        new={**self.tracks,7:track(7,'Beta')};new.pop(3)
        self.lib.radio_index=RadioIndex(new,self.memberships+[('rock',7)])
        ids={t['id'] for t in self.radio.preview('track','1')['tracks']};self.assertIn(7,ids);self.assertNotIn(3,ids)
        self.assertNotEqual(self.radio.pick(seed,[1,2,4]),3)
    def test_no_repetition_before_pool_exhaustion_or_immediate_same_recording(self):
        seed=self.radio.describe('track','1');history=[1]
        for _ in range(4):history.append(self.radio.pick(seed,history))
        self.assertEqual(len(history),len(set(history)))
        for _ in range(20):
            nxt=self.radio.pick(seed,history);self.assertNotEqual(recording_key(self.tracks[nxt]),recording_key(self.tracks[history[-1]]));history.append(nxt)
    def test_duplicate_remasters_do_not_fill_preview(self):
        rows={1:track(1,'Alpha','Hit'),2:track(2,'Alpha','Hit (2012 Remastered)'),3:track(3,'Alpha','Other')}
        self.lib.radio_index=RadioIndex(rows,[]);self.assertEqual(len(self.radio.preview('track','1')['tracks']),2)
    def test_single_track_station_ends_without_repeating_itself(self):
        self.lib.radio_index=RadioIndex({1:self.tracks[1]},[]);self.assertIsNone(self.radio.pick(self.radio.describe('track','1'),[1]))
    def test_hits_are_preferred_but_not_exclusive(self):
        rows={i:track(i,'Alpha') for i in range(1,101)};self.lib.radio_index=RadioIndex(rows,[])
        self.related.popular[recording_key(rows[2])]=100000
        seed=self.radio.describe('artist',rows[1]['artist_id']);picks=[self.radio.pick(seed,[]) for _ in range(500)]
        self.assertGreater(picks.count(2),375);self.assertLess(picks.count(2),490)
    def test_artist_names_normalize_articles_without_fuzzy_title_matches(self):
        self.assertEqual(artist_key('Cure, The'),artist_key('The Cure'))
        self.assertEqual(artist_key('Earth, Wind & Fire'),artist_key('Earth Wind and Fire'))
        self.assertNotEqual(recording_key(track(1,'Alpha','Hit')),recording_key(track(1,'Alpha','Another Hit')))
    def test_isolated_user_station_state(self):
        self.player.command(1,{'action':'play','radio':{'kind':'track','seed':'1'}})
        self.assertIsNone(self.player.state(2)['current']);self.assertNotIn('station',self.player.state(2))

    def test_sparse_station_recommends_at_most_twenty_missing_diverse_albums(self):
        candidates=[{'recording_id':str(i).zfill(36),'artist':'Related '+str(i//3),'title':'New '+str(i),'album':'Album '+str(i),'listens':1000-i} for i in range(40)]
        candidates.insert(0,{'recording_id':RID,'artist':'Alpha','title':'Song 1','album':'Album','listens':99999})
        self.related.recommendations['alpha']=candidates
        d=self.radio.preview('track','1');self.assertTrue(d['sparse']);self.assertEqual(len(d['suggestions']),20)
        self.assertNotIn(RID,[x['recording_id'] for x in d['suggestions']])
        from collections import Counter
        self.assertLessEqual(max(Counter(x['artist'] for x in d['suggestions']).values()),2)
        self.assertEqual(self.radio.recommendation('track','1',d['suggestions'][0]['recording_id']),d['suggestions'][0])
        with self.assertRaises(ValueError):self.radio.recommendation('track','1',RID)

    def test_removed_next_is_pruned_and_controller_receives_new_revision(self):
        from app.listening import Listening
        listening=Listening(self.player)
        state=listening.command(1,{'action':'play','device_id':'a'*32,'device_name':'Fixture','expected_revision':0,'radio':{'kind':'track','seed':'1'}})
        old=state['next']['id'];revision=state['listening']['revision'];intent=state['listening']['intent']
        rows={i:t for i,t in self.tracks.items() if i!=old};self.lib.radio_index=RadioIndex(rows,self.memberships)
        update=listening.state(1,revision=revision)
        self.assertNotIn('_partial',update);self.assertGreater(update['listening']['revision'],revision);self.assertEqual(update['listening']['intent'],intent)
        self.assertNotEqual(update['next']['id'],old);self.assertEqual(update['current'],1)

    def test_catalog_seed_matches_owned_neighbours_and_survives_reload(self):
        seed=self.radio.register('Catalog Artist','Catalog Song');self.assertEqual(seed['kind'],'catalog')
        self.related.cache['catalog artist']=(100,['Beta'])
        result=self.radio.preview('catalog',seed['seed']);self.assertEqual({t['artist'] for t in result['tracks']},{'Beta'})
        self.assertIn(self.radio.start(seed),[3,4])
        reloaded=Radio(self.tmp.name,self.lib,self.related);self.assertEqual(reloaded.describe('catalog',seed['seed'])['name'],'Catalog Song Radio')
    def test_catalog_seed_without_owned_matches_is_not_fabricated(self):
        seed=self.radio.register('Catalog Artist');result=self.radio.preview('catalog',seed['seed']);self.assertEqual(result['tracks'],[])
        with self.assertRaises(ValueError):self.radio.start(seed)
        with self.assertRaises(ValueError):self.radio.register('')
        with self.assertRaises(ValueError):self.radio.register('Artist',{})
    def test_catalog_seed_prefers_existing_local_identity(self):
        self.assertEqual(self.radio.register('Alpha','Song 1')['kind'],'track')
        self.assertEqual(self.radio.register('Alpha')['kind'],'artist')

class EnrichmentTests(unittest.TestCase):
    def test_exact_identity_popularity_mapping_persistence_and_outage(self):
        with tempfile.TemporaryDirectory() as directory:
            calls=[]
            def fetch(url):
                calls.append(url)
                if 'musicbrainz.org' in url:return {'artists':[{'id':MBID,'name':'Merle Haggard','score':100}]}
                if '/lb-radio/' in url:return {'artist':[{'recording_mbid':RID,'similar_artist_name':'Dwight Yoakam','total_listen_count':1000}]}
                return {RID:{'recording':{'name':'A Hit'},'artist':{'name':'Dwight Yoakam'}}}
            d=SimilarArtists(directory,fetch=fetch,enabled=False,now=lambda:100);d.update('merle haggard','Merle Haggard')
            self.assertEqual(d.get('Merle Haggard')[0],['Dwight Yoakam']);self.assertEqual(d.popular[('dwight yoakam','a hit')],1000)
            self.assertTrue(any('pop_begin=50&pop_end=100' in x for x in calls));self.assertTrue(all('user_id' not in x for x in calls));d.db.close()
            d=SimilarArtists(directory,fetch=lambda _:(_ for _ in ()).throw(TimeoutError()),enabled=False,now=lambda:200);d.update('merle haggard','Merle Haggard')
            self.assertEqual(d.get('Merle Haggard')[0],['Dwight Yoakam']);self.assertEqual(d.popular[('dwight yoakam','a hit')],1000);d.db.close()
    def test_ambiguous_artist_not_guessed(self):
        with tempfile.TemporaryDirectory() as directory:
            d=SimilarArtists(directory,enabled=False,fetch=lambda _: {'artists':[{'id':MBID,'name':'Alpha','score':100},{'id':RID,'name':'Alpha','score':100}]})
            self.assertEqual(d.resolve('Alpha'),([],[],[]));d.db.close()
    def test_slow_provider_never_blocks_local_playback_or_db(self):
        with tempfile.TemporaryDirectory() as directory:
            entered=threading.Event();release=threading.Event()
            def fetch(_):entered.set();release.wait(2);raise TimeoutError()
            d=SimilarArtists(directory,fetch=fetch,enabled=True)
            lib=SimpleNamespace(index_lock=threading.RLock(),radio_index=RadioIndex({1:track(1,'Alpha'),2:track(2,'Alpha')},[]));r=Radio(directory,lib,d)
            try:
                started=time.monotonic();result=r.preview('track','1');self.assertLess(time.monotonic()-started,.2);self.assertEqual(len(result['tracks']),2);self.assertTrue(entered.wait(1))
                started=time.monotonic();self.assertEqual(r.pick(result['station'],[1]),2);self.assertLess(time.monotonic()-started,.2)
            finally:release.set();d.jobs.join();d.db.close()

class GenreTests(unittest.TestCase):
    def test_high_popularity_does_not_override_genre_mismatch(self):
        from app.radio import genre_profile,genre_compatible
        country=genre_profile({'tag':{'artist':[{'tag':'country','count':100},{'tag':'outlaw country','count':40}]}})
        rock=genre_profile({'tag':{'artist':[{'tag':'hard rock','count':100},{'tag':'country rock','count':1}]}})
        neighbour=genre_profile({'tag':{'artist':[{'tag':'americana','count':40},{'tag':'folk','count':10}]}})
        self.assertFalse(genre_compatible(country,rock));self.assertTrue(genre_compatible(country,neighbour));self.assertFalse(genre_compatible(country,{}))
    def test_known_related_but_unowned_artists_do_not_widen_to_unrelated_owned_music(self):
        rows={1:track(1,'Alpha'),2:track(2,'Gamma')};index=RadioIndex(rows,[('broad',1),('broad',2)])
        seed=index.seed('track','1');self.assertEqual(set(index.weights(seed,['Unowned neighbour'])),{'alpha'})

class CacheLockTests(unittest.TestCase):
    def test_slow_cache_disk_write_does_not_lock_memory_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            d=SimilarArtists(directory,enabled=False);real=d.db;entered=threading.Event();release=threading.Event()
            class SlowDisk:
                def execute(self,*args):
                    if args[0]=='BEGIN IMMEDIATE':entered.set();release.wait(2)
                    return real.execute(*args)
                def __getattr__(self,name):return getattr(real,name)
            d.db=SlowDisk();d.resolve=lambda _:(['Beta'],[],[])
            worker=threading.Thread(target=d.update,args=('alpha','Alpha'));worker.start()
            try:
                self.assertTrue(entered.wait(1));started=time.monotonic();self.assertEqual(d.get('Alpha')[0],['Beta']);self.assertLess(time.monotonic()-started,.1)
            finally:release.set();worker.join();real.close()

class VerifiedNeighbourTests(unittest.TestCase):
    def test_live_shape_with_popular_wrong_genre_excludes_outlier(self):
        with tempfile.TemporaryDirectory() as directory:
            ids=[RID,'11111111-1111-4111-8111-111111111111','22222222-2222-4222-8222-222222222222'];names=['Merle Haggard','Dwight Yoakam','Guns N Roses']
            def fetch(url):
                if 'musicbrainz.org' in url:return {'artists':[{'id':MBID,'name':names[0],'score':100}]}
                if '/lb-radio/' in url:return {'group':[{'recording_mbid':rid,'similar_artist_name':name,'total_listen_count':1000000 if i==2 else 1000} for i,(rid,name) in enumerate(zip(ids,names))]}
                return {rid:{'artist':{'name':name},'recording':{'name':'Song '+str(i)},'tag':{'artist':[{'tag':'hard rock' if i==2 else 'country','count':100}]}} for i,(rid,name) in enumerate(zip(ids,names))}
            d=SimilarArtists(directory,enabled=False,fetch=fetch);related,_,suggestions=d.resolve(names[0]);self.assertEqual(related,[names[1]]);self.assertNotIn(names[2],[x['artist'] for x in suggestions]);d.db.close()

class CatalogCacheBoundsTests(unittest.TestCase):
    def test_new_catalog_discards_prior_radio_pool_references(self):
        with tempfile.TemporaryDirectory() as directory:
            tracks={1:track(1,'Alpha'),2:track(2,'Beta')};lib=SimpleNamespace(index_lock=threading.RLock(),radio_index=RadioIndex(tracks,[('hits',1),('hits',2)]));similar=SimilarArtists(directory,enabled=False);radio=Radio(directory,lib,similar)
            try:
                for _ in range(5):
                    lib.radio_index=RadioIndex(tracks,[('hits',1),('hits',2)]);radio.preview('track','1')
                    self.assertEqual({key[0] for key in radio.pools},{id(lib.radio_index)})
            finally:similar.db.close()

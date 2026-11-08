import contextlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from app.browse import Browse,genres,tag_info,chart_items,year,CHART_REFRESH_SECONDS
from app.radio import RadioIndex,SimilarArtists
from app.library import Player
from app.accounts import Accounts

class BrowseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.lib=SimpleNamespace(index_lock=threading.RLock())
        tracks={i:{'id':i,'title':'Song '+str(i),'artist':'Artist','artist_id':'a','album':'Album','album_id':'b','artwork':'/art'} for i in range(1,206)}
        self.lib.radio_index=RadioIndex(tracks,[('hits',1),('hits',2),('hits',3)])
        self.similar=SimilarArtists(self.tmp.name,enabled=False);self.lib.radio=SimpleNamespace(similar=self.similar)
        self.b=Browse(self.tmp.name,self.lib,start=False);self.lib.discovery=self.b
        self.b.tags={i:(123,{'genres':['rock'],'year':1984,'date_source':'original'}) for i in tracks}
        self.lib.track=lambda i:self.lib.radio_index.tracks[i]
    def tearDown(self):self.similar.db.close();self.tmp.cleanup()
    def test_tags_multigenre_and_word_boundaries(self):
        self.assertEqual(genres('Bluegrass'),['bluegrass','country']);self.assertEqual(genres('Hip-Hop/Rap'),['hip-hop']);self.assertEqual(genres('Indie Rock'),['alternative','rock']);self.assertEqual(genres('R&B / Soul'),['soul']);self.assertEqual(genres('Trap'),['hip-hop']);self.assertEqual(genres('Contraption'),['other'])
    def test_established_categories_and_clear_aliases(self):
        cases={'Spoken & Audio':['spoken-word'], 'Soundtracks':['soundtrack'],
               'International':['world'], 'Musiques du monde':['world'],
               'New Age':['new-age'], 'Christmas':['holiday'], 'Humour':['comedy'],
               'Instrumental':['instrumental'], 'Vocal/Easy Listening':['easy-listening','vocal'],
               "Children's Music":['children'], 'RandB':['soul'], 'Synthpop':['pop'], 'Britpop':['pop']}
        for value,expected in cases.items():
            with self.subTest(value=value):self.assertEqual(genres(value),expected)
        for value in ('', 'Miscellaneous', 'Other', 'Avant-Garde', 'Religious', 'Fitness & Workout', 'Worldly', 'Filmstrip'):
            self.assertEqual(genres(value),['other'])

    def test_raw_tags_remap_without_losing_reviewed_genres(self):
        self.b.tags[1]=(123,{'genres':['other'],'raw_genre':'Soundtracks','year':1984})
        self.assertEqual(self.b.select({'genre':'soundtrack'}),[1])
        self.assertNotIn(1,self.b.select({'genre':'other'}))
        tracks=dict(self.lib.radio_index.tracks)
        tracks[1]=dict(tracks[1],catalog_genres=['Jazz','Unrecognized'])
        self.lib.radio_index=RadioIndex(tracks,[])
        self.assertEqual(self.b.select({'genre':'jazz'}),[1])
        self.assertEqual(self.b.select({'genre':'soundtrack'}),[])
        self.assertNotIn(1,self.b.select({'genre':'other'}))
        self.assertEqual(tag_info(SimpleNamespace(genre='New Age',year='',other={}))['raw_genre'],'New Age')

    def test_original_year_precedes_remaster_and_unknown_not_fabricated(self):
        t=SimpleNamespace(genre='Rock',year='2023',other={'originaldate':['1967-05-01']});self.assertEqual(tag_info(t)['year'],1967);self.assertEqual(tag_info(t)['date_source'],'original')
        t.other={};self.assertEqual(tag_info(t)['date_source'],'release-tag');t.year=None;self.assertEqual(tag_info(t)['year'],0);self.assertEqual(year('3000'),0)
    def test_genre_contains_all_content_not_only_essentials(self):
        self.assertEqual(self.b.page({'genre':'rock','kind':'songs'})['total'],205)
        self.assertEqual(self.b.page({'genre':'rock','kind':'hits'})['total'],3)
        self.assertEqual(len(self.b.select({'genre':'rock','kind':'songs'})),205)
    def test_decade_filter_unknown_and_new_catalog(self):
        self.b.tags[1]=(123,{'genres':['country'],'year':1967,'date_source':'original'});self.b.tags[2]=(123,{'genres':['rock'],'year':0})
        self.assertEqual(self.b.select({'decade':'1960','genre':'country'}),[1]);self.assertNotIn(2,self.b.select({'decade':'1980'}))
        tracks={1:self.lib.radio_index.tracks[1]};self.lib.radio_index=RadioIndex(tracks,[]);self.assertEqual(self.b.page({'genre':'country'})['total'],1)
    def test_invalid_filter_rejected(self):
        for f in ({'genre':'url'},{'decade':'1950'},{'kind':'invalid'}):
            with self.assertRaises(ValueError):self.b.select(f)
    def test_albums_artists_counts_and_pagination(self):
        self.assertEqual(self.b.page({'genre':'rock','kind':'albums'})['items'][0]['tracks'],205)
        self.assertEqual(self.b.page({'kind':'artists'})['total'],1)
        d=self.b.page({'genre':'rock'},100,100);self.assertEqual(len(d['tracks']),100);self.assertEqual(d['next_offset'],200)
    def test_empty_and_classification_progress(self):
        self.b.tags={};d=self.b.overview();self.assertEqual(next(x for x in d['genres'] if x['id']=='other')['count'],205);self.assertEqual(self.b.select({'decade':'1980'}),[])
    def test_chart_matches_exact_owned_recording_and_does_not_invent_music(self):
        self.b.charts['overall']={'items':[{'artist':'Artist','title':'Song 1','rank':1},{'artist':'Unknown','title':'Missing','rank':2}],'fetched':time.time()}
        d=self.b.chart();self.assertEqual(d['items'][0]['track']['id'],1);self.assertIsNone(d['items'][1]['track']);self.assertFalse(d['stale'])
    def test_chart_matches_collaborator_separator_variations_after_import(self):
        chart={'artist':'KAROL G, Judeline & rusowsky','title':'BbY WOW','rank':1}
        self.b.charts['overall']={'items':[chart],'fetched':time.time()}
        self.assertIsNone(self.b.chart()['items'][0]['track'])
        tracks=dict(self.lib.radio_index.tracks)
        tracks[900]={'id':900,'title':'BbY WOW','artist':'Karol G;Judeline;Rusowsky','artist_id':'kg','album':'Album','album_id':'kg-album'}
        with self.lib.index_lock:self.lib.radio_index=RadioIndex(tracks,[])
        self.assertEqual(self.b.chart()['items'][0]['track']['id'],900)
        # Same title with missing/different collaborators must remain unmatched.
        self.b.charts['overall']['items']=[dict(chart,artist='KAROL G & Someone Else'),dict(chart,title='BbY WOW (Live)')]
        self.assertTrue(all(x['track'] is None for x in self.b.chart()['items']))

    def test_recent_chart_releases_exclude_future_and_old(self):
        import datetime as dt
        self.b.charts['overall']={'items':[{'artist':'A','title':str(i),'rank':i,'release_date':date} for i,date in enumerate([dt.date.today().isoformat(),'1980-01-01','2999-01-01','oops'],1)],'fetched':time.time()}
        self.assertEqual(len(self.b.chart(new=True)['items']),1)
    def test_chart_pending_stale_cache_retained_on_outage(self):
        self.assertTrue(self.b.chart()['pending']);self.b.charts['overall']={'items':[{'artist':'A','title':'Song','rank':1}],'fetched':time.time()-CHART_REFRESH_SECONDS-1}
        for key,_,_ in __import__('app.browse',fromlist=['GENRES']).GENRES:
            if key!='overall':self.b.charts[key]={'fetched':time.time()}
        self.b.fetch=lambda url:(_ for _ in ()).throw(OSError('offline'))
        with patch('app.browse.time.sleep'):self.b.update_charts()
        self.assertTrue(self.b.chart()['stale']);self.assertEqual(len(self.b.chart()['items']),1)
        before=self.b.charts['overall']['attempted']
        self.b.fetch=lambda url:self.fail('must back off')
        with patch('app.browse.time.sleep'):self.b.update_charts()
        self.assertEqual(self.b.charts['overall']['attempted'],before)
    def test_daily_snapshot_no_network_on_read_or_when_fresh(self):
        for key,_,_ in [('overall','',None)]+__import__('app.browse',fromlist=['GENRES']).GENRES:self.b.charts[key]={'fetched':time.time()}
        self.b.fetch=lambda url:self.fail('network must not run')
        self.b.overview();self.b.page({'genre':'rock'});self.b.chart()
        with patch('app.browse.time.sleep'):self.b.update_charts()
    def test_daily_boundary_refresh_and_next_update(self):
        now=2000000000
        for key,_,_ in [('overall','',None)]+__import__('app.browse',fromlist=['GENRES']).GENRES:
            self.b.charts[key]={'fetched':now}
        self.b.charts['overall']={'items':[{'artist':'Old','title':'Old','rank':1}],'fetched':now-86400,'attempted':now-86400}
        urls=[]
        def fetch(url):
            urls.append(url)
            return {'feed':{'results':[{'artistName':'New','name':'New'}]}}
        self.b.fetch=fetch
        with patch('app.browse.time.time',return_value=now),patch('app.browse.time.sleep'):
            self.assertTrue(self.b.chart()['stale'])
            self.b.update_charts()
            self.assertEqual(len(urls),1)
            self.assertEqual(self.b.chart()['items'][0]['title'],'New')
            self.assertEqual(self.b.chart()['next_update'],now+86400)
            self.assertFalse(self.b.chart()['stale'])
        with patch('app.browse.time.time',return_value=now+86399),patch('app.browse.time.sleep'):
            self.b.update_charts();self.assertEqual(len(urls),1)

    def test_feed_parsers_validate_and_dedupe(self):
        d=chart_items({'feed':{'results':[{'artistName':'A','name':'Song'},{'artistName':'A','name':'Song'},{'artistName':'','name':'bad'}]}})
        self.assertEqual(len(d['items']),1);self.assertIn('Apple Music',d['source'])
        d=chart_items({'feed':{'entry':[{'im:artist':{'label':'A'},'im:name':{'label':'Song'},'im:releaseDate':{'label':'2001-01-01'}}]}},True)
        self.assertIn('iTunes',d['source']);self.assertEqual(d['items'][0]['rank'],1)
        with self.assertRaises(ValueError):chart_items({'feed':{'results':[]}})
    def test_full_browse_selection_plays_beyond_first_page(self):
        a=Accounts(self.tmp.name)
        try:
            s=Player(a,self.lib).command(1,{'action':'play','browse':{'genre':'rock','kind':'songs'}})
            self.assertEqual(len(s['upcoming']),204)
        finally:a.db.close()
    def test_tag_cache_persists_without_touching_media(self):
        with contextlib.closing(self.b.connect()) as db:db.execute('INSERT INTO browse_tags VALUES(?,?,?)',(1,123,json.dumps({'genres':['jazz'],'year':1959})));db.commit()
        b=Browse(self.tmp.name,self.lib,start=False);self.assertEqual(b.tags[1][1]['genres'],['jazz'])
    def test_request_entry_uses_only_current_authoritative_chart(self):
        item={'artist':'Artist','title':'Song 1','rank':1};self.b.charts['overall']={'items':[item],'fetched':time.time()};key=self.b.chart()['items'][0]['request_key'];self.assertEqual(self.b.request_entry('overall',key),item)
        with self.assertRaises(ValueError):self.b.request_entry('overall','a'*24)
        with self.assertRaises(ValueError):self.b.request_entry('invalid',key)

    def test_compilation_year_waits_for_recording_evidence(self):
        self.lib.radio_index.tracks[1]['album']='Greatest Hits (Remastered)'
        self.assertNotIn(1,self.b.select({'decade':'1980'}))
        self.assertIn(1,self.b.select({'genre':'rock'}))
        self.lib.radio_index.tracks[1].update(catalog_year=1984,catalog_date_source='researched')
        self.b.index_key=None
        self.assertIn(1,self.b.select({'decade':'1980'}))

    def test_overview_does_not_build_song_order_or_chart_matching(self):
        with patch.object(self.b,'_ordered_ids',side_effect=AssertionError('unexpected sorting')),patch('app.browse.chart_recording_key',side_effect=AssertionError('unexpected chart index')):
            self.assertEqual(self.b.overview()['genres'][1]['count'],205)

    def test_warm_pages_reuse_order_counts_and_grouping(self):
        for kind in ('songs','albums','artists'):
            filters={'genre':'rock','kind':kind}
            expected=self.b.page(filters,0,10)
            with patch('app.browse.normalize',side_effect=AssertionError('repeated normalization')):
                self.assertEqual(self.b.page(filters,0,10),expected)
                self.b.page(filters,100,10)
        self.assertEqual(len(self.b.select({'genre':'rock'})),205)

    def test_returned_selections_and_summaries_cannot_poison_cache(self):
        expected=self.b.select({'genre':'rock'})
        ids=self.b.select({'genre':'rock'});ids.clear()
        self.assertEqual(self.b.select({'genre':'rock'}),expected)
        before=self.b.page({'kind':'albums'})
        changed=self.b.page({'kind':'albums'});changed['items'][0]['name']='changed';changed['genre_counts'][0]['count']=0
        self.assertEqual(self.b.page({'kind':'albums'}),before)

    def test_tag_change_invalidates_filter_order_and_counts(self):
        self.assertEqual(self.b.page({'genre':'country'})['total'],0)
        self.b.tags[1]=(124,{'genres':['country'],'year':1967,'date_source':'original'});self.b.tag_version+=1
        page=self.b.page({'genre':'country','decade':'1960'})
        self.assertEqual([t['id'] for t in page['tracks']],[1])
        self.assertEqual(page['genre_counts'],[{'id':'country','name':'Country','count':1}])

    def test_simultaneous_cold_pages_sort_catalog_only_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from app.browse import normalize
        with patch('app.browse.normalize',wraps=normalize) as serial:
            expected=self.b.page({'genre':'rock'},0,10)
            cold_calls=serial.call_count
        self.b.index=None;self.b.index_key=None
        barrier=threading.Barrier(6)
        def request(_):
            barrier.wait(timeout=5)
            return self.b.page({'genre':'rock'},0,10)
        with patch('app.browse.normalize',wraps=normalize) as normalizer:
            with ThreadPoolExecutor(max_workers=6) as pool:results=list(pool.map(request,range(6)))
            self.assertEqual(normalizer.call_count,cold_calls)
        self.assertEqual(results[0],expected)
        self.assertTrue(all(r==results[0] for r in results))

    def test_simultaneous_chart_fallback_builds_only_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from app.browse import chart_recording_key
        v=self.b.view();barrier=threading.Barrier(6)
        def request(_):
            barrier.wait(timeout=5)
            return self.b._chart_recordings(v)
        with patch('app.browse.chart_recording_key',wraps=chart_recording_key) as key:
            with ThreadPoolExecutor(max_workers=6) as pool:results=list(pool.map(request,range(6)))
            self.assertEqual(key.call_count,205)
        self.assertTrue(all(r is results[0] for r in results))

    def test_filtered_cache_evicts_by_entries_and_item_budget(self):
        expected=self.b.select({'genre':'rock'})
        with patch('app.browse.CACHE_ENTRIES',2),patch('app.browse.CACHE_ITEMS',250):
            # Use a fresh snapshot so no previously cached values precede limits.
            self.b.tag_version+=1
            for filters in ({'genre':'rock'},{'decade':'1980'},{'genre':'country'},{'genre':'rock'}):
                self.b.select(filters)
                v=self.b.view()
                self.assertLessEqual(len(v['cache']),2)
                self.assertLessEqual(v['cache_items'],250)
            self.assertEqual(self.b.select({'genre':'rock'}),expected)

    def test_hits_follow_live_popularity_after_cached_song_requests(self):
        self.b.page({'kind':'songs'})
        self.assertNotIn(205,self.b.select({'kind':'hits'}))
        self.similar.popular[self.lib.radio_index.recordings[205]]=100
        self.assertIn(205,self.b.select({'kind':'hits'}))

    def test_page_uses_one_catalog_snapshot_during_replacement(self):
        original=self.b._select
        def replace(v,filters):
            self.lib.radio_index=RadioIndex({},[])
            return original(v,filters)
        with patch.object(self.b,'_select',side_effect=replace):
            self.assertEqual(self.b.page({'genre':'rock'})['total'],205)
        self.assertEqual(self.b.page({'genre':'rock'})['total'],0)

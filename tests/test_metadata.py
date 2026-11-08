import contextlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from app.metadata import Metadata,matched,voted_genres
from app.radio import RadioIndex
from app.browse import Browse

RID='12345678-1234-1234-1234-123456789abc'
AID='abcdef01-1234-1234-1234-123456789abc'
class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.file=Path(self.tmp.name)/'song.mp3';self.file.write_bytes(b'untouched audio');self.modified=self.file.stat().st_mtime_ns
        self.track={'id':1,'title':'Song','artist':'Artist','album':'Album','artist_id':'a','album_id':'b','artwork':'/art'}
        import sqlite3
        self.db=sqlite3.connect(':memory:');self.db.execute('create table tracks(id,modified_ns,available)');self.db.execute('insert into tracks values(?,?,1)',(1,self.modified))
        self.lib=SimpleNamespace(music=SimpleNamespace(lock=threading.RLock(),db=self.db,_track_file=lambda tid:({'modified_ns':self.modified},str(self.file))),discovery=SimpleNamespace(lock=threading.RLock(),tags={1:(self.modified,{'genres':['other'],'year':0})}),track=lambda tid:self.track,index_lock=threading.RLock())
        self.record={'id':RID,'title':'Song','first-release-date':'1967-05-01','length':180000,'artist-credit':[{'name':'Artist','artist':{'name':'Artist','id':AID}}],'genres':[{'name':'rock','count':5}]}
        self.calls=[]
        def fetch(url):
            self.calls.append(url)
            if '/recording/?' in url:return {'recordings':[self.record]}
            if '/artist/' in url:return {'id':AID,'name':'Artist','genres':[{'name':'rock','count':5}]}
            return self.record
        self.m=Metadata(self.tmp.name,self.lib,fetch);self.lib.metadata=self.m;self.tag=SimpleNamespace(duration=180,other={})
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def pending(self):
        self.m.enqueue();return self.m.jobs[1]
    def proposal(self):
        j=self.pending();p,_=self.m.lookup(self.track,self.tag)
        return self.m.save_job({**j,'state':'review','proposal':p,'before':{'genres':['other'],'year':0}})
    def test_exact_identity_duration_genres_and_original_year(self):
        p,_=self.m.lookup(self.track,self.tag);self.assertTrue(p['auto']);self.assertEqual(p['values'],{'year':1967,'genres':['rock']});self.assertEqual(len(self.calls),2)
    def test_duration_mismatch_never_corrected(self):
        self.tag.duration=250;p,_=self.m.lookup(self.track,self.tag);self.assertIsNone(p)
    def test_ambiguous_recordings_not_guessed(self):
        self.m.fetch=lambda url:{'recordings':[self.record,self.record|{'id':'12345678-1234-1234-1234-123456789abd'}]};p,_=self.m.lookup(self.track,self.tag);self.assertIsNone(p)
    def test_cover_and_live_not_confused(self):
        self.assertFalse(matched(self.track,self.record|{'artist-credit':[{'name':'Other Artist'}]}));self.assertFalse(matched(self.track,self.record|{'disambiguation':'live'}))
    def test_missing_duration_requires_review(self):
        self.tag.duration=0;p,_=self.m.lookup(self.track,self.tag);self.assertFalse(p['auto'])
    def test_artist_genres_require_explicit_review(self):
        self.record['genres']=[];p,_=self.m.lookup(self.track,self.tag);self.assertEqual(p['values']['genres'],['rock']);self.assertFalse(p['auto']);self.assertEqual(p['genre_level'],'artist')
    def test_genre_votes_do_not_accept_one_vote_or_nonmusic_tags(self):
        self.assertEqual(voted_genres({'genres':[{'name':'rock','count':1},{'name':'seen live','count':99}]}),[])
    def test_verified_embedded_id_with_wrong_title_proposed_not_auto(self):
        self.tag.other={'musicbrainz_recordingid':[RID]};p,_=self.m.lookup(self.track|{'title':'Bad title'},self.tag);self.assertEqual(p['values']['title'],'Song');self.assertFalse(p['auto'])
    def test_apply_persists_and_undo_restores_original_without_media_write(self):
        before=self.file.read_bytes();j=self.proposal();self.m.edit({'action':'accept','id':1,'revision':self.m.revision(j)});self.assertEqual(self.m.overrides[1]['values']['year'],1967)
        recovered=Metadata(self.tmp.name,self.lib);self.assertEqual(recovered.overrides[1]['values']['genres'],['rock']);j=self.m.jobs[1];self.m.edit({'action':'undo','id':1,'revision':self.m.revision(j)});self.assertNotIn(1,self.m.overrides);self.assertEqual(self.file.read_bytes(),before);self.assertEqual(self.file.stat().st_mtime_ns,self.modified)
        with contextlib.closing(self.m.connect()) as db:self.assertEqual(db.execute('select count(*) from metadata_audit').fetchone()[0],2)
    def test_stale_review_and_changed_file_rejected(self):
        j=self.proposal()
        with self.assertRaises(ValueError):self.m.edit({'action':'accept','id':1,'revision':'old'})
        os.utime(self.file,ns=(self.modified,self.modified+1000000))
        with self.assertRaises(ValueError):self.m.edit({'action':'accept','id':1,'revision':self.m.revision(j)})
    def test_keep_current_never_changes_catalog_and_no_requeue(self):
        j=self.proposal();self.m.edit({'action':'reject','id':1,'revision':self.m.revision(j)});self.assertEqual(self.m.overrides,{});self.assertEqual(self.m.enqueue(),0)
    def test_automatic_research_only_applies_strong_recording_metadata(self):
        j=self.pending()
        with patch('app.metadata.TinyTag.get',return_value=self.tag):self.m.research(j)
        self.assertEqual(self.m.jobs[1]['state'],'applied');self.assertTrue(self.m.jobs[1]['automatic'])
    def test_verified_date_does_not_apply_artist_level_genre(self):
        self.record['genres']=[]
        job=self.pending()
        with patch('app.metadata.TinyTag.get',return_value=self.tag):self.m.research(job)
        self.assertEqual(self.m.overrides[1]['values'],{'year':1967})
        self.assertEqual(self.m.jobs[1]['deferred_genres'],['rock'])

    def test_failed_provider_is_not_immediately_requeued(self):
        j=self.pending();self.m.save_job({**j,'state':'failed'});self.assertEqual(self.m.enqueue(),0)
    def test_changed_files_invalidate_prior_research(self):
        self.pending();self.modified+=100;self.db.execute('update tracks set modified_ns=?',(self.modified,));self.lib.discovery.tags[1]=(self.modified,{'genres':['other'],'year':0});self.assertEqual(self.m.enqueue(),1)
    def test_published_browse_uses_researched_values(self):
        self.lib.radio_index=RadioIndex({1:self.track|{'catalog_genres':['rock'],'catalog_year':1967,'catalog_date_source':'researched'}},[])
        b=Browse(self.tmp.name,self.lib,start=False);self.assertEqual(b.select({'genre':'rock','decade':'1960'}),[1]);self.assertEqual(b.view()['metadata'][1]['date_source'],'researched')
    def test_public_rows_have_revision_and_no_previous_internal_state(self):
        self.proposal();d=self.m.public();self.assertEqual(d['counts']['review'],1);self.assertEqual(d['items'][0]['title'],'Song');self.assertNotIn('previous',d['items'][0]);self.assertEqual(len(d['items'][0]['revision']),24)
    def test_non_admin_cannot_read_or_apply_research(self):
        from app.server import Handler,Error
        h=Handler.__new__(Handler);h.path='/api/v1/admin/metadata';h.server=SimpleNamespace(accounts=SimpleNamespace(),library=self.lib,player=None);h.identity=lambda **kw:{'id':1,'admin':False};h.body=lambda:{'action':'accept','id':1}
        for method in (h.get,h.post):
            with self.assertRaises(Error) as error:method()
            self.assertEqual(error.exception.status,403)
    def test_truncated_search_results_never_presented_as_unique(self):
        self.m.fetch=lambda url:{'recordings':[self.record],'recording-count':200}
        p,_=self.m.lookup(self.track,self.tag);self.assertIsNone(p)

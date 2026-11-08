import json
import tempfile
import threading
import unittest
from types import SimpleNamespace
from app.accounts import Accounts
from app.request_progress import RequestProgress

class RequestProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.a=Accounts(self.temp.name)
        for uid in (1,2):self.a.db.execute('INSERT INTO users(id,username,password,approved,admin,created) VALUES(?,?,?,1,?,1)',(uid,'fixture'+str(uid),'!',int(uid==1)))
        self.lib=SimpleNamespace(index_lock=threading.RLock(),tracks={},request_track_ready=lambda _:True);self.state='requested';self.remote_extra={};self.offline=False;self.calls=0;self.clock=100
        def fetch(path):
            self.calls+=1
            if self.offline:raise ValueError('offline')
            return {'items':[{'id':rid,'state':self.state,**self.remote_extra} for rid in path.split('=')[1].split(',')]}
        self.fetch=fetch;self.progress=RequestProgress(self.a,self.lib,fetch,now=lambda:self.clock)
        self.rid='a'*32;self.metadata={'title':'Song','artist':'Artist','album':'Album'}
        for uid in (1,2):self.a.db.execute('INSERT INTO music_requests VALUES(?,?,10,?)',(uid,self.rid,json.dumps(self.metadata)))
    def tearDown(self):self.a.db.close();self.temp.cleanup()
    def add_track(self,album='Album'):
        self.lib.tracks[9]={'id':9,'artist':'Artist','album':album,'title':'Song','album_id':'album-1','artwork':'/api/v1/artwork/9'}
    def test_completion_is_verified_by_local_index_and_notifies_each_requester_once(self):
        self.state='available';self.progress.refresh();row=self.progress.rows(1)['items'][0]
        self.assertEqual(row['state'],'imported-awaiting-library-check');self.assertEqual(self.progress.notifications(1)['unread'],0)
        self.add_track('Different album');self.progress.refresh();self.assertEqual(self.progress.notifications(1)['unread'],0)
        self.add_track();self.progress.refresh();self.progress.refresh()
        row=self.progress.rows(1)['items'][0];self.assertEqual(row['stage'],4);self.assertEqual(row['track_id'],9)
        self.assertEqual([x['state'] for x in row['timeline']],['queued','imported-awaiting-library-check','available'])
        self.assertEqual(self.progress.notifications(1)['unread'],1);self.assertEqual(self.progress.notifications(2)['unread'],1)

    def test_featured_credits_match_but_live_versions_do_not(self):
        self.add_track();self.lib.tracks[9].update(artist='Artist;Guest',title='Song (feat. Guest)')
        self.progress.refresh();self.assertEqual(self.progress.rows(1)['items'][0]['state'],'available')
        self.lib.tracks[9]['title']='Song (Live)';self.progress.refresh()
        self.assertNotEqual(self.progress.rows(1)['items'][0]['state'],'available')
    def test_read_is_private_durable_and_not_reset_by_later_refresh(self):
        self.add_track();self.progress.refresh();one=self.progress.notifications(1)['items'][0]['id'];two=self.progress.notifications(2)['items'][0]['id']
        self.progress.mark_read(1,[two]);self.assertEqual(self.progress.notifications(2)['unread'],1)
        self.progress.mark_read(1,[one]);self.progress=RequestProgress(self.a,self.lib,self.fetch);self.progress.refresh()
        self.assertEqual(self.progress.notifications(1)['unread'],0);self.assertEqual(self.progress.notifications(2)['unread'],1)
        with self.assertRaises(ValueError):self.progress.mark_read(1,['1'])
    def test_outage_preserves_last_update_and_reads_do_not_call_upstream(self):
        self.progress.refresh();self.offline=True;self.clock=200;self.progress.refresh();before=self.calls
        row=self.progress.rows(1)['items'][0];self.assertEqual(row['state'],'requested');self.assertTrue(row['status_stale']);self.assertEqual(row['updated'],100)
        self.assertEqual(self.progress.rows(2)['total'],1);self.assertEqual(self.calls,before)
    def test_removed_track_is_not_offered_for_play_and_unknown_user_sees_nothing(self):
        self.add_track();self.progress.refresh();self.lib.tracks.clear();self.progress.refresh()
        self.assertNotIn('track_id',self.progress.rows(1)['items'][0]);self.assertIsNone(self.progress.notifications(1)['items'][0]['track_id'])
        self.assertEqual(self.progress.rows(7)['items'],[]);self.assertEqual(self.progress.notifications(7)['items'],[])
    def test_account_deletion_purges_notifications_and_observations(self):
        self.add_track();self.progress.refresh();self.a.manage_user(1,{'id':2,'action':'delete','confirm_username':'fixture2'})
        for table in ('request_progress','request_events','request_notifications'):self.assertEqual(self.a.db.execute('SELECT count(*) FROM '+table+' WHERE user_id=2').fetchone()[0],0)
        self.progress.refresh();self.assertEqual(self.progress.notifications(2)['items'],[])
    def test_invalid_saved_metadata_does_not_stop_other_requests_or_create_false_notice(self):
        self.a.db.execute('UPDATE music_requests SET metadata=? WHERE user_id=2',('invalid-json',))
        self.progress.refresh();self.assertEqual(self.progress.rows(1)['items'][0]['state'],'requested')
        self.assertEqual(self.progress.notifications(2)['unread'],0)

    def test_unavailable_or_changed_file_cannot_trigger_readiness(self):
        self.add_track();self.lib.request_track_ready=lambda _:False;self.progress.refresh()
        self.assertNotEqual(self.progress.rows(1)['items'][0]['state'],'available');self.assertEqual(self.progress.notifications(1)['unread'],0)
    def test_featured_artist_tags_match_without_hiding_recording_versions(self):
        self.add_track()
        track=next(iter(self.lib.tracks.values()))
        track['artist']=self.metadata['artist']+'; Guest'
        track['title']=self.metadata['title']+' (feat. Guest)'
        self.progress.refresh()
        self.assertEqual(self.progress.rows(1)['items'][0]['state'],'available')
        self.assertEqual(self.progress.notifications(1)['unread'],1)
        self.progress.refresh()
        self.assertEqual(self.progress.notifications(1)['unread'],1)
        track['title']=self.metadata['title']+' (Live)'
        self.progress.refresh()
        self.assertNotEqual(self.progress.rows(1)['items'][0]['state'],'available')
    def test_different_album_does_not_falsely_complete_request(self):
        self.add_track()
        next(iter(self.lib.tracks.values()))['album']='An unrelated album'
        self.progress.refresh()
        self.assertNotEqual(self.progress.rows(1)['items'][0]['state'],'available')
    def test_mark_all_read_does_not_touch_another_account(self):
        self.add_track();self.progress.refresh();self.progress.mark_all_read(1)
        self.assertEqual(self.progress.notifications(1)['unread'],0);self.assertEqual(self.progress.notifications(2)['unread'],1)

    def test_verified_companion_phase_and_percentage_are_used_without_stale_progress(self):
        self.remote_extra={'phase':'downloading','progress_percent':42.5};self.progress.refresh()
        row=self.progress.rows(1)['items'][0];self.assertEqual(row['state'],'downloading');self.assertEqual(row['stage'],2);self.assertEqual(row['progress_percent'],42.5)
        self.remote_extra={'phase':'importing','progress_percent':float('nan')};self.progress.refresh()
        row=self.progress.rows(1)['items'][0];self.assertEqual(row['state'],'importing');self.assertNotIn('progress_percent',row)
        self.remote_extra={};self.progress.refresh();self.assertEqual(self.progress.rows(1)['items'][0]['state'],'requested')

    def test_newest_submission_first_even_with_equal_seconds_and_pagination(self):
        # Same-second inserts must follow submission order, not lexical IDs or
        # progress-update timestamps. Other users' requests stay separate.
        for rid,created in [('z'*32,20),('b'*32,20),('c'*32,30)]:
            self.a.db.execute('INSERT INTO music_requests VALUES(?,?,?,?)',(1,rid,created,json.dumps(self.metadata)))
        self.assertEqual([x['id'] for x in self.progress.rows(1,limit=2)['items']],['c'*32,'b'*32])
        self.assertEqual([x['id'] for x in self.progress.rows(1,offset=2,limit=2)['items']],['z'*32,self.rid])
        self.assertEqual(self.progress.rows(2)['total'],1)

class ListenerCopyTests(unittest.TestCase):
 setUp=RequestProgressTests.setUp
 tearDown=RequestProgressTests.tearDown
 def test_technical_backend_detail_is_not_sent_to_listener(self):
  self.state='waiting-for-release';self.remote_extra={'detail':'No matching downloads were found by the configured indexers. Lidarr API rejected release.'}
  self.progress.refresh();row=self.progress.rows(1)['items'][0]
  self.assertEqual(row['description'],'This song isn’t available right now. We’ll keep checking.')
  self.assertEqual(row['detail'],'');self.assertNotIn('indexers',json.dumps(row));self.assertNotIn('Lidarr',json.dumps(row))


class NotificationDismissalTests(RequestProgressTests):
    def test_dismissal_is_scoped_durable_and_does_not_delete_request(self):
        self.add_track();self.progress.refresh()
        one=self.progress.notifications(1)['items'][0]['id'];two=self.progress.notifications(2)['items'][0]['id']
        self.progress.dismiss(1,[two]);self.assertEqual(self.progress.notifications(2)['unread'],1)
        self.progress.dismiss(1,[one]);self.progress.refresh()
        self.progress=RequestProgress(self.a,self.lib,self.fetch,now=lambda:self.clock)
        self.progress.refresh();self.assertEqual(self.progress.notifications(1),{'items':[],'unread':0})
        self.assertEqual(self.progress.rows(1)['total'],1);self.assertFalse(self.progress.rows(1)['items'][0]['unread'])
        self.assertEqual(self.progress.notifications(2)['unread'],1)
        self.assertEqual(self.a.db.execute('SELECT count(*) FROM request_notifications WHERE user_id=1').fetchone()[0],1)
    def test_clear_all_preserves_other_accounts_and_new_notifications(self):
        self.add_track();self.progress.refresh();self.progress.dismiss(1,all=True)
        self.assertEqual(self.progress.notifications(1)['items'],[]);self.assertEqual(self.progress.notifications(2)['unread'],1)
        self.a.db.execute('INSERT INTO music_requests VALUES(?,?,10,?)',(1,'c'*32,json.dumps(self.metadata)))
        self.progress.refresh();self.assertEqual(self.progress.notifications(1)['unread'],1)
    def test_dismiss_rejects_invalid_ids(self):
        for ids in [None,'all',[True],['1'],list(range(51))]:
            with self.assertRaises(ValueError):self.progress.dismiss(1,ids)
    def test_schema_upgrade_keeps_legacy_notifications(self):
        self.add_track();self.progress.refresh()
        self.a.db.execute('ALTER TABLE request_notifications DROP COLUMN dismissed_at')
        self.progress=RequestProgress(self.a,self.lib,self.fetch)
        self.assertEqual(self.progress.notifications(1)['unread'],1)
    def test_owned_label_does_not_invent_playable_track(self):
        self.state='already-owned';self.progress.refresh();row=self.progress.rows(1)['items'][0]
        self.assertEqual(row['label'],'Verifying in library');self.assertNotIn('track_id',row)
        self.assertEqual(row['stage'],3);self.assertEqual(self.progress.notifications(1)['unread'],0)

    def test_crow_album_alias_requires_same_artist_recording_and_readable_file(self):
        metadata={'artist':'Steve Martin','album':'The Crow','title':'Calico Train'}
        self.a.db.execute('UPDATE music_requests SET metadata=?',(json.dumps(metadata),))
        self.lib.tracks[9]={'id':9,**metadata,'album':'The Crow-New Songs For The 5-String Banjo','album_id':'crow','artwork':'/api/v1/artwork/9'}
        self.state='already-owned'
        self.progress.refresh()
        self.assertEqual(self.progress.rows(1)['items'][0]['state'],'available')
        self.lib.request_track_ready=lambda _:False
        self.progress.refresh()
        self.assertNotEqual(self.progress.rows(1)['items'][0]['state'],'available')
        self.lib.request_track_ready=lambda _:True
        self.lib.tracks[9]['title']='Calico Train (Live)'
        self.progress.refresh()
        self.assertNotEqual(self.progress.rows(1)['items'][0]['state'],'available')

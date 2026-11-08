import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from hitdiscovery.store import Store,now
from hitdiscovery.config import Config
from hitdiscovery.acquire import Acquirer
from hitdiscovery.request_priority import explicit,pending,download_limit
from hitdiscovery import request_service as service
from hitdiscovery.expansion import maintenance

class PriorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.s=Store(self.root/'discovery.sqlite3');self.addCleanup(self.s.db.close)
        self.c=Config(data=self.root,reports=self.root,acquire=True,reviewed=True,expansion=True,budget_date='daily',budget_bytes=10**12,weekly_limit=1000)
        self.group={'release_group_id':'rg','artist':'Artist','artist_mbid':'artist','album':'Album','album_type':'Album','songs':[{'title':'Song'}]}
        self.artist={'id':1,'foreignArtistId':'artist','artistName':'Artist','monitored':False,'metadataProfileId':1,'monitorNewItems':'none'}
        self.album={'id':5,'foreignAlbumId':'rg','artistId':1,'title':'Album','monitored':False,'anyReleaseOk':True,'statistics':{}}
        self.api=Mock();self.state={'indexer':[{'id':1,'priority':25}], 'album':[self.album],'artist':[self.artist],'queue':{'records':[]},'rootfolder':[{'path':'/music','defaultQualityProfileId':2,'defaultMetadataProfileId':1}], 'qualityprofile':[{'id':2,'name':'Existing'}], 'metadataprofile':[{'id':1,'name':'Existing','primaryAlbumTypes':[{'allowed':True,'albumType':{'name':'Album'}}]}], 'release':[]}
        self.api.get.side_effect=lambda path,**kw:self.state[path]
    def authorize(self):
        self.s.cache('arrify-priority:rg',{'requested':now()});self.s.cache('arrify-authorization:rg',{'request_id':'user'})
    def test_explicit_request_enables_artist_and_only_target_album(self):
        self.authorize()
        self.assertEqual(Acquirer(self.api,self.s,self.c).request(self.group),'prepared')
        writes=[c.args for c in self.api.write.call_args_list]
        self.assertEqual([w[0] for w in writes],['artist/1','album/5'])
        self.assertTrue(writes[0][2]['monitored']);self.assertEqual(writes[0][2]['monitorNewItems'],'none')
        self.assertTrue(writes[1][2]['monitored']);self.assertEqual(writes[1][2]['id'],5)
        self.api.write.reset_mock()
        self.assertEqual(Acquirer(self.api,self.s,self.c).request(self.group),'already-recorded');self.api.write.assert_not_called()
    def test_background_does_not_enable_unmonitored_artist(self):
        self.assertEqual(Acquirer(self.api,self.s,self.c).request(self.group),'artist-unmonitored-review-required');self.api.write.assert_not_called()
    def test_priority_requires_durable_user_authorization(self):
        self.s.cache('arrify-priority:rg',{'requested':now()});self.assertFalse(explicit(self.s,'rg'))
        self.assertFalse(pending(self.s));self.authorize();self.assertTrue(pending(self.s))
    def test_pending_request_goes_first_and_background_leaves_a_lane(self):
        self.assertEqual(download_limit(self.s,self.c,'background'),4)
        self.authorize();self.assertEqual(download_limit(self.s,self.c,'background'),0)
        self.assertEqual(download_limit(self.s,self.c,'rg'),5)
        self.s.reserve('rg',1000);self.s.request_state('rg','requested',5)
        self.assertFalse(pending(self.s));self.assertEqual(download_limit(self.s,self.c,'background'),4)
    def test_no_background_starvation_while_user_waits_for_release_retry(self):
        from hitdiscovery.budget import Budget
        self.authorize();self.s.reserve('rg',1000);self.s.request_state('rg','waiting-for-release',5)
        budget=Budget.for_config(self.s,self.c);budget.put('last-search:rg',now())
        self.assertFalse(pending(self.s))
    def test_maintenance_keeps_requested_album_monitored(self):
        self.authorize();self.s.reserve('rg',1000);self.s.request_state('rg','prepared',5)
        scoped=Config(data=self.root,reports=self.root,acquire=True,reviewed=True,expansion=True,budget_date='daily',budget_bytes=10**12);scoped.arrify_request_rg='rg'
        with patch('hitdiscovery.expansion.Lidarr',return_value=self.api):maintenance(scoped,priority_only=True)
        writes=[c.args for c in self.api.write.call_args_list if c.args[0]=='album/5']
        self.assertEqual(len(writes),1);self.assertTrue(writes[0][2]['monitored'])
    def test_old_unmonitored_review_recovers_without_resubmission(self):
        for folder in ('inbox','status'):(self.root/folder).mkdir()
        rid='a'*32;recording='11111111-1111-1111-1111-111111111111'
        item={'recording_id':recording,'title':'Song','artist':'Artist','album':'Album','resolved':{'recording_id':recording,'title':'Song','artist':'Artist','album':'Album','release_group_id':'rg','artist_mbid':'artist'}}
        path=self.root/'inbox'/f'{rid}.json';path.write_text(json.dumps({'id':rid,'selection':item,'created':now()}))
        (self.root/'status'/f'{rid}.json').write_text(json.dumps({'state':'policy-review'}))
        self.s.cache('arrify-policy:rg',{'detail':'Existing artist is unmonitored; operator review is required to preserve that setting.'})
        with patch.object(service,'ROOT',self.root):
            service.process(self.c,path);out=service.status(rid)
        self.assertEqual(out['state'],'priority-queued');self.assertEqual(out['detail'],'')
    def test_explicit_preparation_checks_target_without_scanning_unrelated_albums(self):
        from hitdiscovery.preparation import prepare
        self.authorize();self.c.arrify_request_rg='rg'
        song={'resolution_status':'resolved','artist':'Artist','artist_mbid':'artist','title':'Song','album':'Album','recording_id':'rec','release_group_id':'rg','release_id':'release','release_date':'2020-01-01','discovery_channel':'arrify-user-request','evidence':[{'source':'Arrify user request','kind':'current','date':now(),'unranked':True}]}
        self.s.cache('resolution:fixture',song)
        self.state['album'].append({'id':99,'foreignAlbumId':'unrelated','artistId':1,'statistics':{'trackFileCount':10}})
        with patch('hitdiscovery.preparation.Lidarr',return_value=self.api):result=prepare(self.c,priority_only=True)
        self.assertEqual(result[0]['state'],'prepared')
        self.assertFalse(any(c.args[0]=='track' for c in self.api.get.call_args_list))
    def test_excluded_release_profile_automatically_gets_compatible_copy(self):
        self.authorize();self.state['metadataprofile'][0]['primaryAlbumTypes'][0]['allowed']=False
        with patch('hitdiscovery.metadata.ensure',return_value=7) as ensure:
            self.assertEqual(Acquirer(self.api,self.s,self.c).request(self.group),'prepared')
        ensure.assert_called_once_with(self.api,1)
        artist_write=next(c for c in self.api.write.call_args_list if c.args[0]=='artist/1')
        self.assertEqual(artist_write.args[2]['metadataProfileId'],7)
        self.assertTrue(artist_write.args[2]['monitored'])
    def test_artist_refresh_album_creation_race_reconciles_without_duplicate_post(self):
        self.authorize();self.state['album']=[];self.state['album/lookup']=[dict(self.album)]
        def write(path,method,payload):
            if path=='album' and method=='POST':
                self.state['album']=[self.album]
                raise RuntimeError('POST private/api/v1/album HTTP 400')
            return dict(payload)
        self.api.write.side_effect=write
        self.assertEqual(Acquirer(self.api,self.s,self.c).request(self.group),'prepared')
        self.assertEqual(sum(c.args[0]=='album' and c.args[1]=='POST' for c in self.api.write.call_args_list),1)
        self.assertEqual(self.s.db.execute('SELECT lidarr_id FROM requests WHERE rg=?',('rg',)).fetchone()[0],5)
    def test_recover_prior_album_creation_race_without_another_add(self):
        self.authorize();self.s.reserve('rg',1000);self.s.request_state('rg','held-for-review',detail='POST private/api/v1/album HTTP 400')
        self.s.cache('prepared-group:rg',self.group);self.c.arrify_request_rg='rg'
        with patch('hitdiscovery.expansion.Lidarr',return_value=self.api):maintenance(self.c,priority_only=True)
        row=self.s.db.execute('SELECT state,lidarr_id FROM requests WHERE rg=?',('rg',)).fetchone()
        self.assertEqual(row['state'],'waiting-for-release');self.assertEqual(row['lidarr_id'],5)
        self.assertFalse(any(c.args[0]=='album' and c.args[1]=='POST' for c in self.api.write.call_args_list))
    def test_empty_indexer_results_explain_why_nothing_reaches_downloader(self):
        self.authorize();self.s.reserve('rg',1000);self.s.request_state('rg','prepared',5);self.c.arrify_request_rg='rg'
        with patch('hitdiscovery.expansion.Lidarr',return_value=self.api):maintenance(self.c,priority_only=True)
        row=self.s.db.execute('SELECT state,detail FROM requests WHERE rg=?',('rg',)).fetchone()
        self.assertEqual(row['state'],'waiting-for-release');self.assertEqual(row['detail'],'This song isn’t available right now. We’ll keep checking.');self.assertEqual(json.loads(self.s.db.execute("SELECT payload FROM audit WHERE event='arrify-release-search-waiting' ORDER BY id DESC LIMIT 1").fetchone()[0])['returned'],0)
        self.assertFalse(any(c.args[0]=='release' and c.args[1]=='POST' for c in self.api.write.call_args_list))
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM transfers').fetchone()[0],0)
    def test_rejected_downloads_have_different_reason_from_empty_results(self):
        self.authorize();self.s.reserve('rg',1000);self.s.request_state('rg','prepared',5);self.c.arrify_request_rg='rg'
        self.state['release']=[{'guid':'wrong','indexerId':1,'size':1000,'downloadAllowed':True,'approved':False,'rejected':True,'rejections':['Wrong artist'],'quality':{'quality':{'id':6,'name':'FLAC'}}}]
        with patch('hitdiscovery.expansion.Lidarr',return_value=self.api):maintenance(self.c,priority_only=True)
        row=self.s.db.execute('SELECT detail FROM requests WHERE rg=?',('rg',)).fetchone()
        self.assertEqual(row['detail'],'This song isn’t available right now. We’ll keep checking.');self.assertEqual(json.loads(self.s.db.execute("SELECT payload FROM audit WHERE event='arrify-release-search-waiting' ORDER BY id DESC LIMIT 1").fetchone()[0])['returned'],1)

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from hitdiscovery import request_service as r
from hitdiscovery.store import Store,now
from hitdiscovery.preparation import cached_songs

ID='12345678-1234-1234-1234-123456789012'
class RequestTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        for name in ('inbox','status'):(self.root/name).mkdir()
        self.patch=patch.object(r,'ROOT',self.root);self.patch.start()
        self.config=SimpleNamespace(data=self.root,contact='https://example.org',acquire=True,reviewed=True,
            budget_bytes=1000000000,budget_date='2000-01-01',budget_timezone='America/New_York')
        self.s=Store(self.root/'discovery.sqlite3')
        self.item={'title':'Test Song','artist':'Test Artist','recording_id':ID,'selection':ID}
        self.s.cache('arrify-selection:'+ID,self.item)
    def tearDown(self):self.s.db.close();self.patch.stop();self.tmp.cleanup()
    def test_duplicate_selection_is_one_durable_request(self):
        one=r.submit(self.config,{'selection':ID,'user_id':1})
        two=r.submit(self.config,{'selection':ID,'user_id':2})
        self.assertEqual(one,two);self.assertEqual(len(list((self.root/'inbox').glob('*.json'))),1)
    def test_arbitrary_selection_rejected(self):
        with self.assertRaises(ValueError):r.submit(self.config,{'selection':'../../etc/passwd','user_id':1})
    def test_expired_daily_budget_does_not_block_explicit_priority(self):
        out=r.submit(self.config,{'selection':ID,'user_id':1});path=next((self.root/'inbox').glob('*.json'))
        resolved={'title':'Test Song','artist':'Test Artist','recording_id':ID,'release_group_id':ID,'release_id':ID,
            'release_date':'1984-01-01','artist_mbid':ID,'album':'Test Album','resolution_status':'resolved',
            'evidence':[{'source':'Arrify user request','independent_family':'Arrify','kind':'current','date':now(),'unranked':True}]}
        with patch.object(r.Resolver,'resolve',return_value=resolved):r.process(self.config,path)
        self.assertEqual(r.status(out['id'])['state'],'priority-queued')
        self.assertIsNotNone(self.s.cached('arrify-authorization:'+ID))
        self.assertIsNotNone(self.s.cached('arrify-priority:'+ID))
        self.assertEqual(len(cached_songs(self.s)),1) # older-era requests are not chart-year gated
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM requests').fetchone()[0],0)
    def test_ambiguous_recording_held(self):
        out=r.submit(self.config,{'selection':ID,'user_id':1});path=next((self.root/'inbox').glob('*.json'))
        with patch.object(r.Resolver,'resolve',return_value={'resolution_status':'unresolved'}):r.process(self.config,path)
        self.assertEqual(r.status(out['id'])['state'],'needs-review')
        self.assertNotIn('requested_by',r.status(out['id']))

if __name__=='__main__':unittest.main()

class PinnedRequestTests(RequestTests):
    def test_verified_displayed_album_is_the_request_target(self):
        album_id='55555555-5555-5555-5555-555555555555'
        resolved={'title':'Test Song','artist':'Test Artist','recording_id':ID,'release_group_id':album_id,'release_id':ID,
            'release_date':'1984-01-01','artist_mbid':ID,'album':'Displayed Album','album_type':'Album','resolution_status':'resolved'}
        item=dict(self.item,album='Displayed Album',resolved=resolved,requestable=True)
        self.s.cache('arrify-selection:'+ID,item)
        out=r.submit(self.config,{'selection':ID,'user_id':1});path=next((self.root/'inbox').glob('*.json'))
        with patch.object(r.Resolver,'resolve',side_effect=AssertionError('Must not reselect a different album')):
            r.process(self.config,path)
        self.assertEqual(r.status(out['id'])['album'],'Displayed Album')
        self.assertIsNotNone(self.s.cached('arrify-authorization:'+album_id))
        self.assertIsNone(self.s.cached('arrify-authorization:'+ID))
        cached=self.s.cached('resolution:'+r.key('Test Artist','Test Song'))
        self.assertEqual(cached['album'],'Displayed Album')
    def test_waiting_reason_reaches_private_request_status(self):
        resolved={'recording_id':ID,'title':'Test Song','artist':'Test Artist','album':'Displayed Album','release_group_id':ID,'artist_mbid':ID}
        self.s.cache('arrify-selection:'+ID,dict(self.item,resolved=resolved))
        result=r.submit(self.config,{'selection':ID,'user_id':1})
        self.s.reserve(ID,1000);self.s.request_state(ID,'waiting-for-release',5,'No matching downloads were found. Arrify will retry automatically.')
        with patch.object(r.Resolver,'resolve',side_effect=AssertionError('already verified')):
            r.process(self.config,next((self.root/'inbox').glob('*.json')))
        self.assertEqual(r.status(result['id'])['detail'],'This song isn’t available right now. We’ll keep checking.')

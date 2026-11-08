import tempfile
import unittest
from app.accounts import Accounts

class SearchHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.a=Accounts(self.tmp.name)
        self.a.bootstrap('owner','strong-test-password','owner@example.invalid')
        self.a.register('other','strong-test-password','other@example.invalid');self.a.approve('other')
        self.uid=self.a.identity(self.a.login('owner','strong-test-password'),'web')['id']
        self.other=self.a.identity(self.a.login('other','strong-test-password'),'web')['id']
    def tearDown(self):self.a.db.close();self.tmp.cleanup()
    def remember(self,q,uid=None):return self.a.edit_search_history(uid or self.uid,{'action':'remember','query':q})
    def test_recent_order_normalized_deduplication_and_user_isolation(self):
        self.remember('Bill Monroe');self.remember('Oingo');self.remember(' bill   MONROE ')
        self.assertEqual(self.a.recent_searches(self.uid)['items'],['bill MONROE','Oingo'])
        self.assertEqual(self.a.recent_searches(self.other)['items'],[])
    def test_bound_remove_and_clear_are_scoped(self):
        self.remember('Other listener',self.other)
        for i in range(25):self.remember('query '+str(i))
        items=self.a.recent_searches(self.uid)['items'];self.assertEqual(len(items),20);self.assertEqual(items[0],'query 24');self.assertNotIn('query 0',items)
        self.a.edit_search_history(self.uid,{'action':'remove','query':'QUERY 24'})
        self.assertNotIn('query 24',self.a.recent_searches(self.uid)['items'])
        self.a.edit_search_history(self.uid,{'action':'clear','user_id':self.other})
        self.assertEqual(self.a.recent_searches(self.uid)['items'],[])
        self.assertEqual(self.a.recent_searches(self.other)['items'],['Other listener'])
    def test_invalid_requests_do_not_write(self):
        for data in ({'action':'other'},{'action':'remember','query':''},{'action':'remember','query':'a'*201},{'action':'remember','query':'a\x00b'},{'action':'remove','query':[]}):
            with self.assertRaises(ValueError):self.a.edit_search_history(self.uid,data)
        self.assertEqual(self.a.recent_searches(self.uid)['items'],[])
    def test_clearing_still_works_after_recording_rate_limit(self):
        from app.server import Handler
        from email.message import Message
        from types import SimpleNamespace
        token=self.a.login('owner','strong-test-password','native')
        h=Handler.__new__(Handler);h.path='/api/v1/search/history';h.headers=Message();h.headers['Authorization']='Bearer '+token
        h.server=SimpleNamespace(accounts=self.a,library=None,player=None);h.json=lambda data,*args,**kwargs:data
        for i in range(180):
            h.body=lambda i=i:{'action':'remember','query':'term '+str(i)};h.post()
        h.body=lambda:{'action':'remember','query':'one too many'}
        with self.assertRaises(ValueError):h.post()
        h.body=lambda:{'action':'clear'}
        self.assertEqual(h.post()['items'],[])

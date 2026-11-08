import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from hitdiscovery.store import Store
from hitdiscovery.budget import Budget,RequestBudget

class RequestBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(Path(self.tmp.name)/'test.db')
        self.config=SimpleNamespace(budget_bytes=1000,budget_date='2000-01-01',budget_timezone='America/New_York')
        self.s.cache('arrify-authorization:album',{'request_id':'explicit-request'})
    def tearDown(self):self.s.db.close();self.tmp.cleanup()
    def budget(self):
        self.config.arrify_request_rg='album';return Budget.for_config(self.s,self.config)
    def test_expired_normal_allowance_stays_expired(self):
        self.assertEqual(Budget.for_config(self.s,self.config).remaining(),0)
        scoped=self.budget();tid=scoped.reserve('album','release',1,1001,4)
        self.assertIsNotNone(tid)
        grant=self.s.db.execute('SELECT * FROM arrify_request_grants').fetchone()
        self.assertEqual(grant['authorized_bytes'],1102)
        del self.config.arrify_request_rg
        self.assertEqual(Budget.for_config(self.s,self.config).remaining(),0)
    def test_cannot_fund_other_album_or_unsized_release(self):
        b=self.budget()
        self.assertIsNone(b.reserve('unrelated','release',1,1000,4))
        self.assertIsNone(b.reserve('album','release',1,0,4))
        self.assertIsNone(b.reserve('album','release',1,None,4))
        self.assertEqual(b.spent(),0)
    def test_duplicate_does_not_mint_credit_twice(self):
        b=self.budget();self.assertIsNotNone(b.reserve('album','release',1,1000,4))
        self.assertIsNone(b.reserve('album','release',1,1000,4))
        self.assertEqual(b.spent(),1100)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM arrify_request_grants').fetchone()[0],1)
    def test_failed_attempts_stay_charged_during_quality_fallback(self):
        b=self.budget();first=b.reserve('album','lossless',1,1000,4);b.state(first,'failed')
        self.assertIsNotNone(b.reserve('album','lossy',1,500,3))
        self.assertEqual(b.spent(),1650)
    def test_no_authorization_means_no_reservation(self):
        b=RequestBudget(self.s,'unknown')
        self.assertEqual(b.remaining(),0)
        self.assertIsNone(b.reserve('unknown','release',1,1000,4))

if __name__=='__main__':unittest.main()

class SharedDailyCapTests(unittest.TestCase):
 def test_explicit_requests_share_recurring_daily_limit(self):
  from types import SimpleNamespace
  from hitdiscovery.budget import Budget,RequestBudget
  with tempfile.TemporaryDirectory() as folder:
   s=Store(Path(folder)/'daily.db')
   try:
    s.cache('arrify-authorization:requested',{'request_id':'r'})
    cfg=SimpleNamespace(budget_date='daily',budget_bytes=1000,budget_timezone='America/New_York')
    background=Budget(s,1000,'daily',downloads=True);self.assertIsNotNone(background.reserve('background','first',1,200,4))
    requested=RequestBudget(s,'requested',cfg);self.assertEqual(requested.remaining(),780)
    self.assertIsNone(requested.reserve('requested','too-large',1,800,4));self.assertIsNotNone(requested.reserve('requested','fits',1,500,4));self.assertEqual(background.remaining(),230)
   finally:s.db.close()

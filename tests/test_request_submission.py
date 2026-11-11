import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from app.accounts import Accounts
from app.request_submission import RequestSubmissions, RequestLimit

class SubmissionTests(unittest.TestCase):
    def test_slow_companion_does_not_hold_playback_account_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            a=Accounts(directory);entered=threading.Event();release=threading.Event()
            def submit(path,data):
                entered.set()
                if not release.wait(3):raise TimeoutError('test release missing')
                return {'id':'request-1','title':'Song'}
            requests=RequestSubmissions(a,submit)
            with ThreadPoolExecutor(max_workers=2) as workers:
                pending=workers.submit(requests.create,1,'selection',1)
                try:
                    self.assertTrue(entered.wait(1))
                    # Stream authorization, login and heartbeat all use this lock.
                    self.assertTrue(a.lock.acquire(timeout=.3),'Network request blocked playback authorization')
                    a.lock.release()
                finally:release.set()
                self.assertEqual(pending.result()['id'],'request-1')
            with self.assertRaises(RequestLimit):requests.create(1,'another',1)
            a.db.close()

    def test_concurrent_submissions_cannot_bypass_daily_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            a=Accounts(directory);calls=[]
            def submit(path,data):calls.append(data);return {'id':'one'}
            requests=RequestSubmissions(a,submit)
            def create():
                try:requests.create(1,'selection',1);return 'accepted'
                except RequestLimit:return 'limited'
            with ThreadPoolExecutor(max_workers=2) as workers:
                results=list(workers.map(lambda _:create(),range(2)))
            self.assertCountEqual(results,['accepted','limited']);self.assertEqual(len(calls),1)
            self.assertEqual(calls[0]['user_id'],0)  # No account ID leaves the Arrify server.
            a.db.close()

class BrowseSubmissionTests(unittest.TestCase):
    def test_repeat_click_reuses_request_without_spending_another_quota_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            a=Accounts(directory);calls=[]
            def submit(path,data):calls.append(data);return {'id':'one','title':'Song','artist':'Artist','album':'Album'}
            requests=RequestSubmissions(a,submit);key='a'*24
            first=requests.create(1,'selection',1,source={'browse_key':key});second=requests.create(1,'selection',1,source={'browse_key':key})
            self.assertEqual(first['id'],second['id']);self.assertTrue(second['already_requested']);self.assertEqual(len(calls),1);self.assertIsNone(requests.existing_browse(2,key));self.assertEqual(requests.browse_requests(1)[key]['album'],'Album');a.db.close()

class SharedRequestMarkerTests(unittest.TestCase):
    def test_browse_marker_visible_to_all_without_private_request_id(self):
        with tempfile.TemporaryDirectory() as directory:
            a=Accounts(directory)
            try:
                requests=RequestSubmissions(a,lambda path,data:{'id':'private-request','title':'Song','artist':'Artist','album':'Album'})
                key='b'*24;requests.create(1,'selection',source={'browse_key':key})
                shared=requests.browse_requests(2)[key]
                self.assertTrue(shared['already_requested']);self.assertNotIn('id',shared);self.assertNotIn('user_id',shared)
                self.assertIsNone(requests.existing_browse(2,key))
            finally:a.db.close()
    def test_catalog_request_marks_same_song_across_charts_but_not_live_version(self):
        with tempfile.TemporaryDirectory() as directory:
            a=Accounts(directory)
            try:
                requests=RequestSubmissions(a,lambda path,data:{'id':'private-request','title':'Song','artist':'Artist','album':'Album'})
                requests.create(1,'selection')
                items=[{'artist':'ARTIST','title':'Song (2020 Remaster)','selection':'different-edition'},{'artist':'Artist','title':'Song (Live)','selection':'live'}]
                requests.mark_requested(items);self.assertTrue(items[0]['requested']);self.assertFalse(items[1]['requested'])
            finally:a.db.close()

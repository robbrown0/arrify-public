"""Regressions from the 2026-10-26 independent security/efficiency review."""
import tempfile
import threading
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from app.accounts import Accounts
from app import party_http
from app.library import Player
from app.parties import Parties
from test_parties import Library

class ReviewSecurityTests(unittest.TestCase):
    def test_short_window_cannot_reset_long_window_or_hourly_limits(self):
        with tempfile.TemporaryDirectory() as folder:
            a=Accounts(folder)
            try:
                with patch('app.accounts.time.monotonic',return_value=1000):
                    a.rate_limit('login',2,900);a.rate_limit('login',2,900)
                    a.rate_limit('party-request',1,3600)
                with patch('app.accounts.time.monotonic',return_value=1061):
                    a.rate_limit('search',60,60)
                    with self.assertRaises(ValueError):a.rate_limit('login',2,900)
                    with self.assertRaises(ValueError):a.rate_limit('party-request',1,3600)
                with patch('app.accounts.time.monotonic',return_value=1901):
                    a.rate_limit('login',2,900)
                    with self.assertRaises(ValueError):a.rate_limit('party-request',1,3600)
            finally:a.db.close()
    def test_guest_catalog_rejects_excess_concurrency_without_waiting(self):
        release=threading.Event();ready=threading.Event();lock=threading.Lock();active=0
        def slow(query):
            nonlocal active
            with lock:
                active+=1
                if active==3:ready.set()
            release.wait(5)
            return {'items':[]}
        with patch.object(party_http.acquisition,'search',side_effect=slow),ThreadPoolExecutor(max_workers=3) as workers:
            futures=[workers.submit(party_http.catalog_search,'artist') for _ in range(3)]
            try:
                self.assertTrue(ready.wait(2))
                with self.assertRaises(party_http.CatalogBusy):party_http.catalog_search('another artist')
            finally:release.set()
            for future in futures:self.assertEqual(future.result(),[])
        with patch.object(party_http.acquisition,'search',side_effect=ValueError('provider error')):
            with self.assertRaises(ValueError):party_http.catalog_search('artist')
        with patch.object(party_http.acquisition,'search',return_value={'items':[]}):self.assertEqual(party_http.catalog_search('artist'),[])
    def test_delete_host_purges_party_capabilities_and_guest_names(self):
        with tempfile.TemporaryDirectory() as folder:
            a=Accounts(folder)
            try:
                for name in ('owner','host','other'):
                    a.register(name,'a-strong-password',email=name+'@example.invalid');a.approve(name,admin=name=='owner')
                p=Player(a,Library());parties=Parties(a,p,origin='https://arrify.example')
                party=parties.host(2,data={'name':'Private named party'});other=parties.host(3,data={'name':'Keep this'})
                token=party['guest_url'].split('#')[1];raw,g,secret=parties.join(token,'Private guest')
                parties.add(token,secret,g['csrf'],1)
                a.manage_user(1,{'id':2,'action':'delete','confirm_username':'host'})
                for table,field in [('parties','id'),('party_guests','party_id'),('party_entries','party_id'),('party_acquisitions','party_id')]:self.assertEqual(a.db.execute('SELECT count(*) FROM '+table+' WHERE '+field+'=?',(party['id'],)).fetchone()[0],0)
                self.assertEqual(parties.host(3,other['id'])['name'],'Keep this')
                with self.assertRaises(ValueError):parties.row(token=token)
            finally:a.db.close()

    def test_http_admission_rejects_excess_work_and_recovers(self):
        from app.server import ArrifyHTTPServer,Handler
        from unittest.mock import Mock
        import time
        server=ArrifyHTTPServer(('127.0.0.1',0),Handler)
        release=threading.Event();started=threading.Event();count=0;lock=threading.Lock()
        def handle(request,address):
            nonlocal count
            with lock:
                count+=1
                if count==24:started.set()
            release.wait(5)
        server.process_request_thread=handle
        server.shutdown_request=Mock()
        try:
            for i in range(48):server.process_request(Mock(),('127.0.0.1',i))
            self.assertTrue(started.wait(2))
            rejected=Mock();server.process_request(rejected,('127.0.0.1',999))
            self.assertIn(b'503 Service Unavailable',rejected.sendall.call_args.args[0]);server.shutdown_request.assert_called_with(rejected)
            release.set();server.workers.shutdown(wait=True)
            self.assertEqual(count,48)
            # Every accepted future returned its admission slot.
            slots=[server.request_slots.acquire(blocking=False) for _ in range(49)]
            self.assertEqual(sum(slots),48)
        finally:release.set();server.server_close()

    def test_legacy_password_is_preserved_then_upgraded_after_successful_login(self):
        import hashlib
        with tempfile.TemporaryDirectory() as folder:
            a=Accounts(folder)
            try:
                a.register('legacy','a-strong-password',email='legacy@example.invalid');a.approve('legacy')
                salt='0123456789abcdef0123456789abcdef'
                legacy=salt+':'+hashlib.scrypt(b'a-strong-password',salt=salt.encode(),n=16384,r=8,p=1).hex()
                a.db.execute('UPDATE users SET password=? WHERE username=?',(legacy,'legacy'))
                with self.assertRaises(ValueError):a.login('legacy','wrong-password')
                self.assertEqual(a.db.execute('SELECT password FROM users').fetchone()[0],legacy)
                token=a.login('legacy','a-strong-password')
                self.assertEqual(a.identity(token,'web')['username'],'legacy')
                upgraded=a.db.execute('SELECT password FROM users').fetchone()[0]
                self.assertTrue(upgraded.startswith('scrypt5$'))
                self.assertEqual(a.hash_password('a-strong-password',upgraded.split(':')[0]),upgraded)
                self.assertTrue(a.login('legacy','a-strong-password'))
            finally:a.db.close()
    def test_password_work_rejects_excess_concurrency_and_releases_slots(self):
        from unittest.mock import Mock
        release=threading.Event();ready=threading.Event();count=0;lock=threading.Lock()
        def slow(*args,**kwargs):
            nonlocal count
            with lock:
                count+=1
                if count==2:ready.set()
            release.wait(5);return b'digest'
        with patch('app.accounts.hashlib.scrypt',side_effect=slow),ThreadPoolExecutor(max_workers=2) as workers:
            futures=[workers.submit(Accounts.hash_password,'password') for _ in range(2)]
            try:
                self.assertTrue(ready.wait(2))
                with self.assertRaisesRegex(ValueError,'busy'):Accounts.hash_password('password')
            finally:release.set()
            for future in futures:self.assertTrue(future.result().startswith('scrypt5$'))
        with patch('app.accounts.hashlib.scrypt',return_value=b'digest'):self.assertTrue(Accounts.hash_password('password'))

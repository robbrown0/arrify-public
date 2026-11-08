import tempfile
import unittest
from email.message import Message
from unittest.mock import patch
from app.accounts import Accounts
from app.server import Handler

class BrowserSetupTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.a=Accounts(self.tmp.name)
    def tearDown(self):self.a.db.close();self.tmp.cleanup()
    def test_explicit_owner_setup_and_later_signup_approval(self):
        self.a.bootstrap('owner','strong-owner-password', email=str('owner')+ '@example.invalid')
        token=self.a.login('owner','strong-owner-password')
        self.assertEqual(self.a.identity(token,'web')['admin'],1)
        self.assertFalse(self.a.needs_setup())
        self.a.register('listener','strong-listener-password', email=str('listener')+ '@example.invalid')
        with self.assertRaises(PermissionError):self.a.login('listener','strong-listener-password')
        with self.assertRaises(PermissionError):self.a.bootstrap('second-owner','another-strong-password', email=str('second-owner')+ '@example.invalid')
    def test_existing_pending_name_requires_its_password(self):
        self.a.register('owner','strong-owner-password', email=str('owner')+ '@example.invalid')
        with self.assertRaises(ValueError):self.a.bootstrap('owner','incorrect-password', email=str('owner')+ '@example.invalid')
        self.assertTrue(self.a.needs_setup())
        self.a.bootstrap('owner','strong-owner-password', email=str('owner')+ '@example.invalid')
        self.assertFalse(self.a.needs_setup())

class OriginTests(unittest.TestCase):
    def handler(self,origin,host,peer='192.168.1.50'):
        h=Handler.__new__(Handler);h.headers=Message();h.headers['Origin']=origin;h.headers['Host']=host;h.client_address=(peer,1234);return h
    def test_lan_origin_and_cookie(self):
        with patch('app.server.ORIGINS',frozenset(['http://192.168.1.70:8792'])),patch('app.server.SECURE',True):
            h=self.handler('http://192.168.1.70:8792','192.168.1.70:8792')
            self.assertTrue(h.valid_origin());self.assertTrue(h.setup_allowed())
            self.assertNotIn('; Secure',h.cookie('example'))
    def test_https_cookie_and_proxy_cannot_bootstrap(self):
        with patch('app.server.ORIGINS',frozenset(['https://arrify.robnets.com'])),patch('app.server.SECURE',True):
            h=self.handler('https://arrify.robnets.com','arrify.robnets.com','10.66.66.1')
            h.headers['X-Forwarded-For']='192.168.1.50'
            self.assertTrue(h.valid_origin());self.assertFalse(h.setup_allowed())
            self.assertIn('; Secure',h.cookie('example'))
    def test_origin_must_match_host(self):
        with patch('app.server.ORIGINS',frozenset(['http://192.168.1.70:8792'])):
            h=self.handler('http://192.168.1.70:8792','arrify.robnets.com')
            self.assertFalse(h.valid_origin())

if __name__=='__main__':unittest.main()

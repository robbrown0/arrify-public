import json
import tempfile
import time
import unittest
from pathlib import Path
from app.accounts import Accounts

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.a=Accounts(self.tmp.name)
        self.a.bootstrap('rob','original-password-123', email=str('rob')+ '@example.invalid')
    def tearDown(self):self.a.db.close();self.tmp.cleanup()
    def test_disabled_by_default(self):
        with self.assertRaises(PermissionError):self.a.claim_recovery()
    def test_single_use_reset_preserves_identity_and_revokes_all_sessions(self):
        web=self.a.login('rob','original-password-123')
        native=self.a.login('rob','original-password-123','native')
        owner=self.a.identity(web,'web');self.a.update_profile(owner['id'],'Rob','rob@example.invalid')
        self.a.enable_recovery('rob');token=self.a.claim_recovery()
        self.assertEqual(self.a.check_recovery(token)['username'],'rob')
        saved=(Path(self.tmp.name)/'recovery.json').read_text()
        self.assertNotIn(token,saved)
        self.a.finish_recovery(token,'replacement-password-456')
        for value,kind in [(web,'web'),(native,'native')]:
            with self.assertRaises(PermissionError):self.a.identity(value,kind)
        with self.assertRaises(ValueError):self.a.login('rob','original-password-123')
        user=self.a.identity(self.a.login('rob','replacement-password-456'),'web')
        self.assertEqual(user['id'],owner['id']);self.assertEqual(user['admin'],1)
        self.assertEqual(user['display_name'],'Rob')
        with self.assertRaises(PermissionError):self.a.finish_recovery(token,'another-password-123')
    def test_invalid_token_and_expiry_do_not_change_password(self):
        self.a.enable_recovery('rob');token=self.a.claim_recovery()
        with self.assertRaises(PermissionError):self.a.finish_recovery('x'*43,'replacement-password-456')
        with self.a.recovery_lock():
            grant=self.a.recovery_grant();grant['expires']=time.time()-1;self.a.save_recovery_grant(grant)
        with self.assertRaises(PermissionError):self.a.finish_recovery(token,'replacement-password-456')
        self.a.login('rob','original-password-123')
    def test_reclaim_invalidates_old_link_and_bad_password_leaves_link_usable(self):
        self.a.enable_recovery('rob');old=self.a.claim_recovery();new=self.a.claim_recovery()
        with self.assertRaises(PermissionError):self.a.check_recovery(old)
        with self.assertRaises(ValueError):self.a.finish_recovery(new,'short')
        self.assertEqual(self.a.check_recovery(new)['username'],'rob')

if __name__=='__main__':unittest.main()

class RecoveryHTTPGateTests(unittest.TestCase):
    def test_proxy_cannot_claim_even_with_spoofed_lan_headers(self):
        from app.server import Handler,Error
        from email.message import Message
        from types import SimpleNamespace
        from unittest.mock import Mock,patch
        h=Handler.__new__(Handler);h.server=SimpleNamespace(accounts=Mock(),library=None,player=None)
        h.client_address=('10.66.66.1',1234);h.headers=Message()
        h.headers['Origin']='https://arrify.robnets.com';h.headers['Host']='arrify.robnets.com'
        h.headers['X-Forwarded-For']='192.168.1.50';h.path='/api/v1/auth/recovery-link';h.body=lambda:{}
        with patch('app.server.ORIGINS',frozenset(['https://arrify.robnets.com'])):
            with self.assertRaises(Error):h.post()
        h.server.accounts.claim_recovery.assert_not_called()
    def test_invalid_origin_cannot_claim_on_lan(self):
        from app.server import Handler,Error
        from email.message import Message
        from types import SimpleNamespace
        from unittest.mock import Mock
        h=Handler.__new__(Handler);h.server=SimpleNamespace(accounts=Mock(),library=None,player=None)
        h.client_address=('192.168.1.50',1234);h.headers=Message()
        h.headers['Origin']='https://unrelated.invalid';h.headers['Host']='192.168.1.70:8792'
        h.path='/api/v1/auth/recovery-link';h.body=lambda:{}
        with self.assertRaises(Error):h.post()
        h.server.accounts.claim_recovery.assert_not_called()

class AccountRenameTests(unittest.TestCase):
    def test_rename_preserves_identity_profile_and_administrator(self):
        with tempfile.TemporaryDirectory() as directory:
            a=Accounts(directory)
            try:
                a.bootstrap('rob','original-password-123', email=str('rob')+ '@example.invalid');token=a.login('rob','original-password-123')
                original=a.identity(token,'web');a.update_profile(original['id'],'Rob','rob@example.invalid')
                a.rename_account('rob','admin')
                with self.assertRaises(ValueError):a.login('rob','original-password-123')
                with self.assertRaises(PermissionError):a.identity(token,'web')
                user=a.identity(a.login('admin','original-password-123'),'web')
                self.assertEqual(user['id'],original['id']);self.assertEqual(user['admin'],1)
                self.assertEqual(user['display_name'],'Rob')
                a.enable_recovery('admin');reset=a.claim_recovery()
                self.assertEqual(a.check_recovery(reset)['username'],'admin')
            finally:a.db.close()
    def test_name_collision_does_not_merge_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            a=Accounts(directory)
            try:
                a.bootstrap('rob','original-password-123', email=str('rob')+ '@example.invalid');a.register('admin','different-password-123', email=str('admin')+ '@example.invalid')
                with self.assertRaises(ValueError):a.rename_account('rob','admin')
                self.assertEqual(a.identity(a.login('rob','original-password-123'),'web')['username'],'rob')
            finally:a.db.close()

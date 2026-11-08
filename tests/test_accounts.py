import tempfile
import unittest
from app.accounts import Accounts

class AccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.a=Accounts(self.tmp.name)
    def tearDown(self):self.a.db.close();self.tmp.cleanup()
    def test_approval_and_native_session_separation(self):
        self.a.register('listener','long-test-password', email=str('listener')+ '@example.invalid')
        with self.assertRaises(PermissionError):self.a.login('listener','long-test-password')
        self.a.approve('listener')
        token=self.a.login('listener','long-test-password','native')
        self.assertEqual(self.a.identity(token,'native')['username'],'listener')
        with self.assertRaises(PermissionError):self.a.identity(token,'web')
        self.assertNotIn('long-test-password',self.a.db.execute('SELECT password FROM users').fetchone()[0])
    def test_registration_never_self_promotes(self):
        self.a.register('first','a-strong-password', email=str('first')+ '@example.invalid')
        row=self.a.db.execute('SELECT approved,admin FROM users').fetchone()
        self.assertEqual(tuple(row),(0,0))
    def test_bad_password_and_revocation(self):
        self.a.register('listener','a-strong-password', email=str('listener')+ '@example.invalid');self.a.approve('listener')
        with self.assertRaises(ValueError):self.a.login('listener','a-wrong-password')
        token=self.a.login('listener','a-strong-password')
        self.a.db.execute('DELETE FROM sessions')
        with self.assertRaises(PermissionError):self.a.identity(token,'web')
    def test_rate_limit(self):
        for _ in range(2):self.a.rate_limit('key',2)
        with self.assertRaises(ValueError):self.a.rate_limit('key',2)

if __name__=='__main__':unittest.main()

class RequiredEmailTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.a=Accounts(self.tmp.name)
    def tearDown(self):self.a.db.close();self.tmp.cleanup()
    def test_email_required_normalized_unique_and_never_self_verified(self):
        for value in (None,'','bad','a@b','a\nb@example.com'):
            with self.assertRaises(ValueError):self.a.register('listener','strong-test-password',value)
        self.a.register('listener','strong-test-password',' Person@Example.com ')
        self.assertEqual(self.a.pending_count(),1)
        self.a.approve('listener');token=self.a.login('listener','strong-test-password')
        identity=self.a.identity(token,'web');self.assertEqual(identity['needs_email'],0);self.assertNotIn('email',identity)
        profile=self.a.profile(identity['id']);self.assertEqual(profile['email'],'person@example.com');self.assertFalse(profile['email_verified'])
        with self.assertRaises(ValueError):self.a.register('other','strong-test-password','PERSON@example.com')
        self.assertEqual(self.a.db.execute('SELECT count(*) FROM users').fetchone()[0],1)
    def test_missing_email_cannot_clear_profile_or_change_another_account(self):
        for name in ('first','second'):
            self.a.register(name,'strong-test-password',name+'@example.com');self.a.approve(name)
        uid=self.a.identity(self.a.login('first','strong-test-password'),'web')['id']
        for value in ('','second@example.com'):
            with self.assertRaises(ValueError):self.a.update_profile(uid,'Changed',value)
        self.assertEqual(self.a.profile(uid)['email'],'first@example.com')
        self.assertEqual(self.a.profile(uid)['display_name'],'first')
    def test_pending_accounts_are_first_and_approval_count_updates(self):
        self.a.bootstrap('owner','strong-test-password','owner@example.com')
        self.a.register('waiting','strong-test-password','waiting@example.com')
        users=self.a.admin_users();self.assertEqual(users['pending'],1);self.assertEqual(users['items'][0]['username'],'waiting')
        self.a.approve('waiting');self.assertEqual(self.a.pending_count(),0)
    def test_changed_email_loses_verification_and_duplicate_save_is_atomic(self):
        self.a.register('first','strong-test-password','first@example.com');self.a.approve('first')
        uid=self.a.identity(self.a.login('first','strong-test-password'),'web')['id']
        self.a.db.execute('UPDATE profiles SET email_verified_at=123 WHERE user_id=?',(uid,)) # verified fixture
        self.a.update_profile(uid,'Name','first@example.com');self.assertTrue(self.a.profile(uid)['email_verified'])
        self.a.update_profile(uid,'Name','new@example.com');self.assertFalse(self.a.profile(uid)['email_verified'])

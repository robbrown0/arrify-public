import tempfile
import unittest
from app.accounts import Accounts
from app.account_email import AccountEmail, GENERIC
from app.native_auth import NativeSessions

class FakeMail:
    def __init__(self):self.items=[]
    def send(self,*args):self.items.append(args)
    def token(self):return self.items[-1][2].split('#')[1].split('\n')[0].split(':')[1]

class AccountEmailTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.a=Accounts(self.tmp.name);self.mail=FakeMail()
        self.service=AccountEmail(self.a,'https://music.example',self.mail);self.a.account_email=self.service
        self.password='Three unrelated words 873!'
    def tearDown(self):self.a.db.close();self.tmp.cleanup()
    def create(self):
        self.a.register_public('listener',self.password,'listener@example.com');return self.mail.token()
    def test_verification_and_approval_are_independent(self):
        token=self.create();self.a.approve('listener')
        with self.assertRaises(PermissionError):self.a.login('listener',self.password)
        self.service.finish(token,'verify');self.a.login('listener',self.password)
        with self.assertRaises(ValueError):self.service.finish(token,'verify')
    def test_verification_does_not_approve(self):
        self.service.finish(self.create(),'verify')
        with self.assertRaises(PermissionError):self.a.login('listener',self.password)
    def test_reset_single_use_revokes_web_and_native(self):
        self.service.finish(self.create(),'verify');self.a.approve('listener')
        web=self.a.login('listener',self.password)
        native=NativeSessions(self.a).login({'username':'listener','password':self.password,'device_name':'Phone'})
        self.service.request('listener','reset');token=self.mail.token()
        dump='\n'.join(self.a.db.iterdump());self.assertNotIn(token,dump)
        self.service.finish(token,'reset','Different unrelated words 123!')
        with self.assertRaises(PermissionError):self.a.identity(web,'web')
        from app.native_auth import NativeError
        with self.assertRaises(NativeError):NativeSessions(self.a).refresh(native['refresh_token'])
        with self.assertRaises(ValueError):self.service.finish(token,'reset','Another long password 999')
    def test_expiry_wrong_purpose_and_email_change(self):
        token=self.create()
        with self.assertRaises(ValueError):self.service.finish(token,'reset','Different unrelated words 123!')
        self.a.db.execute('UPDATE email_grants SET expires=0')
        with self.assertRaises(ValueError):self.service.finish(token,'verify')
        self.service.request('listener','verify');token=self.mail.token()
        uid=self.a.db.execute('SELECT id FROM users').fetchone()[0]
        self.a.update_profile(uid,'Listener','new@example.com')
        with self.assertRaises(ValueError):self.service.finish(token,'verify')
    def test_neutral_response_and_frozen_account(self):
        self.create();count=len(self.mail.items)
        for identifier in ['missing','listener']:
            self.assertEqual(self.service.request(identifier,'reset'),{'message':GENERIC})
        self.assertEqual(len(self.mail.items),count)
        self.a.db.execute('UPDATE users SET frozen=1')
        self.service.request('listener','verify');self.assertEqual(len(self.mail.items),count)
    def test_password_policy_preserves_grant_on_rejection(self):
        self.service.finish(self.create(),'verify');self.service.request('listener','reset');token=self.mail.token()
        for password in ['short','123456789012345','password123456789']:
            with self.assertRaises(ValueError):self.service.finish(token,'reset',password)
        self.service.finish(token,'reset','A unique longer phrase 882')
    def test_deleted_account_cannot_verify(self):
        token=self.create();self.a.delete_account(self.password,username='listener')
        with self.assertRaises(ValueError):self.service.finish(token,'verify')

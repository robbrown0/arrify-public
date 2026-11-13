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

class EmailHTTPTests(unittest.TestCase):
    def setUp(self):
        from http.server import ThreadingHTTPServer
        from app.server import Handler
        from types import SimpleNamespace
        import threading
        self.tmp=tempfile.TemporaryDirectory();self.a=Accounts(self.tmp.name);self.mail=FakeMail()
        self.a.account_email=AccountEmail(self.a,'https://music.example',self.mail)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.accounts=self.a;self.server.library=self.server.player=SimpleNamespace()
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.a.register_public('listener','A unique longer password 123','listener@example.com')
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.a.db.close();self.tmp.cleanup()
    def post(self,action,data,cookie='',origin='https://music.example'):
        import http.client,json
        from unittest.mock import patch
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        with patch('app.server.ORIGINS',frozenset(['https://music.example'])):
            c.request('POST','/api/v1/auth/email/'+action,json.dumps(data),{'Content-Type':'application/json','Origin':origin,'Host':'music.example','Cookie':cookie})
            r=c.getresponse();result=r.status,json.loads(r.read()),dict(r.getheaders());c.close();return result
    def test_explicit_verification_cookie_refresh_and_replay(self):
        token=self.mail.token()
        status,_,headers=self.post('open',{'purpose':'verify','token':token})
        self.assertEqual(status,200);cookie=headers['Set-Cookie']
        for flag in ['Secure','HttpOnly','SameSite=Strict','Path=/api/v1/auth/email/']:self.assertIn(flag,cookie)
        self.assertEqual(self.post('status',{},cookie)[0],200)
        self.assertEqual(self.a.db.execute('SELECT email_verified_at FROM profiles').fetchone()[0],0)
        self.assertEqual(self.post('finish',{},cookie)[0],200)
        self.assertEqual(self.post('finish',{},cookie)[0],400)
    def test_cross_origin_rejected(self):
        self.assertEqual(self.post('open',{'purpose':'verify','token':self.mail.token()},origin='https://evil.example')[0],403)
    def test_wrong_cookie_and_neutral_request(self):
        self.assertEqual(self.post('finish',{})[0],400)
        known=self.post('request',{'purpose':'reset','identifier':'listener'})
        unknown=self.post('request',{'purpose':'reset','identifier':'missing'})
        self.assertEqual(known[:2],unknown[:2])

class GrantConcurrencyTests(AccountEmailTests):
    def test_only_one_concurrent_reset_succeeds(self):
        from concurrent.futures import ThreadPoolExecutor
        self.service.finish(self.create(),'verify');self.service.request('listener','reset');token=self.mail.token()
        def attempt():
            try:self.service.finish(token,'reset','New independent words 998');return True
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:attempt(),range(2)))
        self.assertEqual(sum(results),1)

class WebDeletionTests(EmailHTTPTests):
    def test_web_deletion_requires_csrf_password_and_confirmation(self):
        import http.client,json
        from unittest.mock import patch
        self.a.account_email.finish(self.mail.token(),'verify');self.a.approve('listener')
        token=self.a.login('listener','A unique longer password 123')
        def request(password,confirm,csrf):
            connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
            with patch('app.server.ORIGINS',frozenset(['https://music.example'])):
                connection.request('POST','/api/v1/account/delete',json.dumps({'password':password,'confirm':confirm}),{'Content-Type':'application/json','Origin':'https://music.example','Host':'music.example','Cookie':'arrify_session='+token,'X-CSRF-Token':csrf})
                response=connection.getresponse();status=response.status;response.read();connection.close();return status
        csrf=self.a.csrf(token)
        self.assertEqual(request('A unique longer password 123',True,''),403)
        self.assertEqual(request('wrong',True,csrf),403)
        self.assertEqual(request('A unique longer password 123',False,csrf),400)
        self.assertEqual(request('A unique longer password 123',True,csrf),200)
        self.assertIsNone(self.a.db.execute('SELECT 1 FROM users').fetchone())

class EmailChangeTests(AccountEmailTests):
    def test_sensitive_email_change_requires_password(self):
        self.service.finish(self.create(),'verify')
        uid=self.a.db.execute('SELECT id FROM users').fetchone()[0]
        with self.assertRaises(PermissionError):self.a.update_profile_authenticated(uid,'Name','new@example.com')
        self.assertTrue(self.a.profile(uid)['email_verified'])
        self.a.update_profile_authenticated(uid,'Name','new@example.com',password=self.password)
        self.assertFalse(self.a.profile(uid)['email_verified'])
        self.service.finish(self.mail.token(),'verify')
        self.assertTrue(self.a.profile(uid)['email_verified'])

class ExistingEmailMigrationTests(unittest.TestCase):
    def test_owner_opt_in_runs_once_and_never_trusts_changed_or_new_email(self):
        import json,os
        from unittest.mock import patch
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            a=Accounts(directory);a.register('existing','Existing secure password 891','old@example.com');a.db.close()
            config=Path(directory)/'smtp.json';config.write_text(json.dumps({'trust_existing_emails':True}))
            mail=FakeMail()
            with patch.dict(os.environ,{'ARRIFY_SMTP_CONFIG':str(config),'ARRIFY_ORIGIN':'https://music.example'}),patch('app.account_email.Mailer',return_value=mail):
                a=Accounts(directory)
                uid=a.db.execute("SELECT id FROM users WHERE username='existing'").fetchone()[0]
                self.assertTrue(a.profile(uid)['email_verified'])
                a.update_profile(uid,'Name','changed@example.com')
                a.register_public('newuser','A new secure password 189','new@example.com');a.db.close()
                a=Accounts(directory)
                self.assertEqual(a.db.execute('SELECT count(*) FROM profiles WHERE email_verified_at>0').fetchone()[0],0)
                a.db.close()

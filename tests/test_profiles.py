import base64
import tempfile
import unittest
from email.message import Message
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError,URLError
from app.accounts import Accounts
from app.avatars import Avatars
from app.server import Handler,Error

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aFacAAAAASUVORK5CYII=')
class Response:
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,size):return PNG

class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.a=Accounts(self.tmp.name)
        for name in ('listener','other'):
            self.a.register(name,'strong-test-password', email=str(name)+ '@example.invalid');self.a.approve(name)
        self.uid=self.a.db.execute("SELECT id FROM users WHERE username='listener'").fetchone()[0]
        self.other=self.a.db.execute("SELECT id FROM users WHERE username='other'").fetchone()[0]
        self.service=Avatars(self.a);self.open=Mock(return_value=Response());self.service.opener=SimpleNamespace(open=self.open)
    def tearDown(self):self.a.db.close();self.tmp.cleanup()
    def test_documented_hash_and_normalization(self):
        self.assertEqual(self.service.url(' MyEmailAddress@example.com '),'https://gravatar.com/avatar/84059b07d4be67b806386c0aad8070a23f18836bbaae342275dc0a83414c32ee?s=192&d=404&r=g')
    def test_no_email_uses_initials_without_contacting_provider(self):
        self.a.db.execute("UPDATE profiles SET email='' WHERE user_id=?",(self.uid,)) # legacy fixture
        mime,data=self.service.image(self.uid)
        self.assertEqual(mime,'image/svg+xml');self.assertIn(b'>LI</text>',data);self.open.assert_not_called()
    def test_provider_image_is_cached_and_email_is_not_in_identity(self):
        self.a.update_profile(self.uid,'Music Lover',' Person@Example.com ')
        token=self.a.login('listener','strong-test-password');identity=self.a.identity(token,'web')
        self.assertNotIn('email',identity);self.assertNotIn('example.com',identity['avatar_url'])
        self.assertEqual(self.a.profile(self.uid)['email'],'person@example.com')
        self.assertEqual(self.service.image(self.uid),('image/png',PNG));self.service.image(self.uid)
        self.assertEqual(self.open.call_count,1)
        req=self.open.call_args[0][0];self.assertNotIn('person@example.com',req.full_url)
    def test_missing_or_unavailable_provider_uses_cached_fallback(self):
        self.a.update_profile(self.uid,'Music Lover','nobody@example.invalid')
        self.open.side_effect=HTTPError('https://gravatar.com/',404,'Not found',{},None)
        mime,data=self.service.image(self.uid);self.assertEqual(mime,'image/svg+xml');self.assertIn(b'>ML</text>',data)
        self.service.image(self.uid);self.assertEqual(self.open.call_count,1)
        self.a.refresh_avatar(self.uid);self.open.side_effect=URLError('unavailable')
        self.assertEqual(self.service.image(self.uid)[0],'image/svg+xml')
    def test_refresh_and_required_email_preserve_a_valid_photo(self):
        self.a.update_profile(self.uid,'Listener','person@example.com');self.service.image(self.uid)
        old=self.a.profile(self.uid)['avatar_url'];new=self.a.refresh_avatar(self.uid)['avatar_url']
        self.assertNotEqual(old,new);self.service.image(self.uid);self.assertEqual(self.open.call_count,2)
        with self.assertRaises(ValueError):self.a.update_profile(self.uid,'Listener','')
        self.service.image(self.uid);self.assertEqual(self.open.call_count,2)
        self.assertIsNotNone(self.a.db.execute('SELECT avatar_data FROM profiles WHERE user_id=?',(self.uid,)).fetchone()[0])
    def test_profile_change_during_fetch_never_restores_old_photo(self):
        self.a.update_profile(self.uid,'Old','old@example.com')
        def changed(*args,**kwargs):self.a.update_profile(self.uid,'New','new@example.com');return Response()
        self.open.side_effect=changed
        self.assertEqual(self.service.image(self.uid)[0],'image/svg+xml')
        self.assertIsNone(self.a.db.execute('SELECT avatar_data FROM profiles WHERE user_id=?',(self.uid,)).fetchone()[0])
    def test_profile_validation(self):
        for email in ('invalid','a@b','<address>@example.com','a\nb@example.com'):
            with self.assertRaises(ValueError):self.a.update_profile(self.uid,'Listener',email)
        with self.assertRaises(ValueError):self.a.update_profile(self.uid,'bad\nname','')
    def handler(self,path,token,data=None):
        h=Handler.__new__(Handler);h.path=path;h.headers=Message();h.headers['Authorization']='Bearer '+token
        h.server=SimpleNamespace(accounts=self.a,library=None,player=None,avatars=self.service)
        h.json=lambda data,*args,**kwargs:data
        if data is not None:h.body=lambda:data
        return h
    def test_other_users_avatar_is_private_and_profile_updates_are_scoped(self):
        token=self.a.login('listener','strong-test-password','native')
        with self.assertRaises(Error) as denied:self.handler('/api/v1/avatar/'+str(self.other),token).get()
        self.assertEqual(denied.exception.status,403)
        self.handler('/api/v1/profile',token,{'user_id':self.other,'admin':True,'display_name':'My name','email':'listener@example.invalid'}).post()
        self.assertEqual(self.a.profile(self.uid)['display_name'],'My name')
        self.assertEqual(self.a.profile(self.other)['display_name'],'other')
        self.assertEqual(self.a.identity(token,'native')['admin'],0)

if __name__=='__main__':unittest.main()

class TextPreferenceTests(unittest.TestCase):
    def test_text_size_persists_in_profile_and_session_without_changing_other_users(self):
        with tempfile.TemporaryDirectory() as directory:
            a=Accounts(directory)
            try:
                a.bootstrap('owner','owner-password-123', email=str('owner')+ '@example.invalid');token=a.login('owner','owner-password-123')
                uid=a.identity(token,'web')['id'];self.assertEqual(a.profile(uid)['text_size'],'standard')
                a.register('listener','listener-password-123', email=str('listener')+ '@example.invalid');a.approve('listener')
                other=a.identity(a.login('listener','listener-password-123'),'web')['id']
                a.update_profile(uid,'Owner','owner@example.invalid','largest')
                self.assertEqual(a.profile(uid)['text_size'],'largest')
                self.assertEqual(a.identity(token,'web')['text_size'],'largest')
                self.assertEqual(a.profile(other)['text_size'],'standard')
                a.update_profile(uid,'New name','owner@example.invalid')
                self.assertEqual(a.profile(uid)['text_size'],'largest')
                with self.assertRaises(ValueError):a.update_profile(uid,'New name','owner@example.invalid','unexpected')
            finally:a.db.close()

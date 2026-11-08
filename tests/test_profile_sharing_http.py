import json
import unittest
import test_party_http as fixtures


class ProfileSharingHTTPTests(unittest.TestCase):
    setUp=fixtures.PartyHTTPTests.setUp
    tearDown=fixtures.PartyHTTPTests.tearDown
    call=fixtures.PartyHTTPTests.call

    def test_own_preference_is_authenticated_csrf_protected_and_user_scoped(self):
        path='/api/v1/profile/sharing';data={'playlist_shared_default':True,'user_id':99}
        self.assertEqual(self.call(path,data)[0],403)
        self.assertEqual(self.call(path,data,self.hostcookie,'wrong')[0],403)
        code,body,_=self.call(path,data,self.hostcookie,self.a.csrf(self.session))
        self.assertEqual(code,200,body);self.assertTrue(json.loads(body)['playlist_shared_default'])
        self.assertTrue(json.loads(self.call('/api/v1/me',cookie=self.hostcookie)[1])['playlist_shared_default'])
        self.assertTrue(json.loads(self.call('/api/v1/profile',cookie=self.hostcookie)[1])['playlist_shared_default'])
        self.assertFalse(self.a.playlist_shared_default(99))
        self.assertEqual(self.call(path,{'playlist_shared_default':'true'},self.hostcookie,self.a.csrf(self.session))[0],400)

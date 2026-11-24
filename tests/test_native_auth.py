import concurrent.futures
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from app.accounts import Accounts
from app.native_auth import NativeSessions, NativeError
from app.native_http import dispatch


class NativeAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.a = Accounts(self.tmp.name)
        self.a.register('listener', 'long-test-password', 'listener@example.invalid')
        self.a.approve('listener')
        self.service = NativeSessions(self.a)

    def tearDown(self):
        self.a.db.close()
        self.tmp.cleanup()

    def login(self):
        return self.service.login({'username': 'listener', 'password': 'long-test-password', 'device_name': 'Test iPhone'})

    def test_rotation_and_replay_revoke_entire_family(self):
        first = self.login()
        second = self.service.refresh(first['refresh_token'])
        self.assertNotEqual(first['refresh_token'], second['refresh_token'])
        self.assertEqual(second['expires_in'], 3600)
        self.assertEqual(self.a.identity(second['access_token'], 'native')['username'], 'listener')
        with self.assertRaises(NativeError): self.service.refresh(first['refresh_token'])
        with self.assertRaises(NativeError): self.service.refresh(second['refresh_token'])
        for item in (first, second):
            with self.assertRaises(PermissionError): self.a.identity(item['access_token'], 'native')

    def test_expired_access_can_renew_but_inactive_session_cannot(self):
        first = self.login()
        with patch('time.time', return_value=__import__('time').time() + 3601):
            with self.assertRaises(PermissionError): self.a.identity(first['access_token'], 'native')
            second = self.service.refresh(first['refresh_token'])
            self.assertLess(second['refresh_expires_in'], first['refresh_expires_in'])
        with patch('time.time', return_value=__import__('time').time() + 31 * 86400):
            with self.assertRaises(NativeError): self.service.refresh(second['refresh_token'])

    def test_recovery_survives_service_restart_without_storing_secrets(self):
        first = self.login(); attempt = 'a' * 32
        second = self.service.refresh(first['refresh_token'], attempt)
        restarted = NativeSessions(self.a)
        recovered = restarted.refresh(first['refresh_token'], attempt)
        self.assertEqual(second, recovered)
        dump = '\n'.join(self.a.db.iterdump())
        for secret in (first['refresh_token'], second['access_token'], second['refresh_token'], attempt):
            self.assertNotIn(secret, dump)
        restarted.refresh(second['refresh_token'], 'b' * 32)
        with self.assertRaises(NativeError): restarted.refresh(first['refresh_token'], attempt)

    def test_same_attempt_concurrency_recovers_but_different_attempt_revokes(self):
        first = self.login()
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: self.service.refresh(first['refresh_token'], 'a' * 32), range(2)))
        self.assertEqual(results[0], results[1])
        self.a.identity(results[0]['access_token'], 'native')
        with self.assertRaises(NativeError): self.service.refresh(first['refresh_token'], 'b' * 32)
        with self.assertRaises(PermissionError): self.a.identity(results[0]['access_token'], 'native')

    def test_recovery_is_bounded_and_does_not_extend_access_expiration(self):
        first = self.login(); now = __import__('time').time()
        second = self.service.refresh(first['refresh_token'], 'a' * 32)
        with patch('time.time', return_value=now + 4000):
            recovered = self.service.refresh(first['refresh_token'], 'a' * 32)
            self.assertEqual(recovered['expires_in'], 0)
            self.assertEqual(recovered['refresh_token'], second['refresh_token'])
        with patch('time.time', return_value=now + 86401):
            with self.assertRaises(NativeError): self.service.refresh(first['refresh_token'], 'a' * 32)

    def test_foreground_activity_slides_past_90_days_background_refresh_does_not(self):
        first = self.login(); now = __import__('time').time(); current = first
        for day in range(1, 101):
            with patch('time.time', return_value=now + day * 86400):
                current = self.service.refresh(current['refresh_token'])
                self.service.activity(current['access_token'])
        with patch('time.time', return_value=now + 131 * 86400):
            with self.assertRaises(NativeError): self.service.refresh(current['refresh_token'])
        other = self.login()
        for day in range(1, 30):
            with patch('time.time', return_value=now + day * 86400):
                other = self.service.refresh(other['refresh_token'])
        with patch('time.time', return_value=now + 31 * 86400):
            with self.assertRaises(NativeError): self.service.refresh(other['refresh_token'])

    def test_account_revocation_and_session_isolation(self):
        first, second = self.login(), self.login()
        uid = self.a.identity(first['access_token'], 'native')['id']
        self.service.revoke(uid + 1, first['session_id'])
        self.a.identity(first['access_token'], 'native')
        self.service.revoke(uid, first['session_id'])
        with self.assertRaises(PermissionError): self.a.identity(first['access_token'], 'native')
        with self.assertRaises(NativeError): self.service.refresh(first['refresh_token'])
        self.a.identity(second['access_token'], 'native')
        self.a.db.execute('DELETE FROM sessions WHERE user_id=?', (uid,))
        with self.assertRaises(NativeError): self.service.refresh(second['refresh_token'])
        self.assertEqual(self.a.db.execute('SELECT count(*) FROM native_refresh').fetchone()[0], 0)

    def test_two_concurrent_refreshes_fail_closed(self):
        first = self.login()
        def refresh():
            try: return self.service.refresh(first['refresh_token'])
            except NativeError: return None
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: refresh(), range(2)))
        self.assertEqual(sum(r is not None for r in results), 1)
        with self.assertRaises(PermissionError):
            self.a.identity(next(r for r in results if r)['access_token'], 'native')

    def test_identity_persists_and_credentials_are_hashed(self):
        first = self.login()
        server_id = self.service.capabilities()['server_id']
        dump = '\n'.join(self.a.db.iterdump())
        self.assertNotIn(first['access_token'], dump)
        self.assertNotIn(first['refresh_token'], dump)
        with self.assertRaises(PermissionError): self.a.identity(first['refresh_token'], 'native')
        with self.assertRaises(PermissionError): self.a.identity(first['access_token'], 'web')
        other = Accounts(self.tmp.name)
        try: self.assertEqual(NativeSessions(other).capabilities()['server_id'], server_id)
        finally: other.db.close()

    def test_http_contract_login_refresh_error_and_legacy_passthrough(self):
        response = []
        h = SimpleNamespace(server=SimpleNamespace(accounts=self.a), client_address=('127.0.0.1', 1),
                            token=lambda: ('', 'web'), json=lambda body, status=200, extra=None: response.append((body, status, extra)))
        self.assertFalse(dispatch(h, '/api/v1/auth/login', {}))
        dispatch(h, '/api/v1/native/sessions', {'username': 'listener', 'password': 'long-test-password', 'device_name': 'Phone'})
        body, status, headers = response[-1]
        self.assertEqual(status, 200)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        dispatch(h, '/api/v1/native/refresh', {'refresh_token': body['refresh_token']})
        self.assertEqual(response[-1][1], 200)
        dispatch(h, '/api/v1/native/refresh', {'refresh_token': body['refresh_token']})
        body, status, headers = response[-1]
        self.assertEqual(status, 401)
        self.assertEqual(body['error']['code'], 'invalid_refresh')
        self.assertEqual(body['error']['request_id'], headers['X-Request-ID'])
        dispatch(h, '/api/v1/native/sessions')
        self.assertEqual(response[-1][1], 401)
        with patch.object(self.a, 'rate_limit', side_effect=ValueError('limit')):
            dispatch(h, '/api/v1/native/refresh', {})
        self.assertEqual(response[-1][1], 429)
        self.assertEqual(response[-1][2]['Retry-After'], '900')

    def test_freeze_then_unfreeze_does_not_restore_refresh(self):
        first = self.login()
        self.a.register('owner', 'long-test-password', 'owner@example.invalid')
        self.a.approve('owner', admin=True)
        self.a.manage_user(2, {'action':'freeze','id':1})
        self.a.manage_user(2, {'action':'unfreeze','id':1})
        with self.assertRaises(NativeError): self.service.refresh(first['refresh_token'])

    def test_recovery_revokes_refresh_family(self):
        first = self.login()
        self.a.enable_recovery('listener')
        token = self.a.claim_recovery()
        self.a.finish_recovery(token, 'replacement-long-password')
        with self.assertRaises(NativeError): self.service.refresh(first['refresh_token'])

    def test_existing_logout_revokes_family_without_pausing_other_device(self):
        from app.listening import Listening
        from app.library import Player
        from test_player import Library
        first = self.login()
        listening = Listening(Player(self.a, Library()))
        state = listening.command(1, {'action':'play','ids':[1,2], 'device_id':'a'*32,'device_name':'Other output','expected_revision':0})
        listening.logout(1, {'device_id':'b'*32}, self.a.digest(first['access_token']))
        self.assertEqual(listening.state(1)['listening']['owner'], state['listening']['owner'])
        self.assertTrue(listening.state(1)['listening']['playing'])
        with self.assertRaises(NativeError): self.service.refresh(first['refresh_token'])

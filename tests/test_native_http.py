"""Real HTTP integration and checked-in response contract conformance."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from app.accounts import Accounts
from app.server import Handler

CONTRACT = json.loads((Path(__file__).resolve().parents[1] / 'contracts/native-auth.openapi.json').read_text())


def validate(test, value, schema):
    if '$ref' in schema:
        schema = CONTRACT['components']['schemas'][schema['$ref'].split('/')[-1]]
    if value is None and schema.get('nullable'):
        return
    if 'oneOf' in schema:
        matches = 0
        for choice in schema['oneOf']:
            try:
                validate(test, value, choice)
                matches += 1
            except AssertionError:
                pass
        test.assertEqual(matches, 1)
        return
    kind = schema['type']
    test.assertIsInstance(value, {'object': dict, 'array': list, 'string': str, 'integer': int, 'number': (int, float), 'boolean': bool}[kind])
    if kind in ('integer', 'number'): test.assertNotIsInstance(value, bool)
    if 'enum' in schema: test.assertIn(value, schema['enum'])
    if kind == 'object':
        for key in schema['required']: test.assertIn(key, value)
        for key, item in value.items():
            if key in schema['properties']: validate(test, item, schema['properties'][key])
    elif kind == 'array':
        for item in value: validate(test, item, schema['items'])
    elif kind in ('integer', 'number'): test.assertGreaterEqual(value, schema.get('minimum', 0))


class NativeHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.a = Accounts(self.tmp.name)
        from app.account_email import AccountEmail
        from tests.test_account_email import FakeMail
        self.mail = FakeMail()
        self.a.account_email = AccountEmail(self.a,'https://music.example',self.mail)
        self.a.register('listener', 'long-test-password', 'listener@example.invalid')
        self.a.approve('listener')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.accounts = self.a
        self.server.library = self.server.player = SimpleNamespace()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def test_delete_account_verifies_password_and_revokes_all_sessions(self):
        _, pair = self.call('/api/v1/native/sessions', {'username':'listener','password':'long-test-password','device_name':'iPad'})
        path = '/api/v1/native/account/delete'
        token = pair['access_token']
        self.assertEqual(self.call(path, {'password':'wrong','confirm':True}, token)[0], 403)
        self.assertEqual(self.call(path, {'password':'long-test-password','confirm':False}, token)[0], 400)
        self.assertEqual(self.call(path, {'password':'long-test-password','confirm':True,'user_id':99}, token)[0], 400)
        status, result = self.call(path, {'password':'long-test-password','confirm':True}, token)
        self.assertEqual(status, 200); self.assertIn('deleted', result['message']); self.assertNotIn('_erased_id', result)
        self.assertEqual(self.call('/api/v1/native/sessions', token=token)[0], 401)
        self.assertEqual(self.call('/api/v1/native/refresh', {'refresh_token':pair['refresh_token']})[0], 401)
        self.assertEqual(self.a.db.execute('SELECT count(*) FROM users').fetchone()[0], 0)

    def test_pending_account_delete_and_rate_limit(self):
        path = '/api/v1/native/account/delete'
        self.a.register('pending', 'long-test-password', 'pending@example.invalid')
        self.assertEqual(self.call(path, {'username':'pending','password':'long-test-password','confirm':True})[0], 200)
        for _ in range(4):
            self.assertEqual(self.call(path, {'username':'listener','password':'wrong','confirm':True})[0], 403)
        self.assertEqual(self.call(path, {'username':'listener','password':'long-test-password','confirm':True})[0], 429)
        self.assertTrue(self.a.login('listener', 'long-test-password'))

    def test_registration_requires_approval_and_never_issues_session(self):
        status, result = self.call('/api/v1/native/register', {'username':'newlistener','email':'new@example.invalid','password':'long-new-password'})
        self.assertEqual(status, 200)
        self.assertIn('approve', result['message'])
        self.assertNotIn('access_token', result)
        with self.assertRaises(PermissionError): self.a.login('newlistener', 'long-new-password', 'native')
        self.a.approve('newlistener')
        with self.assertRaises(PermissionError):self.a.login('newlistener','long-new-password')
        self.a.account_email.finish(self.mail.token(),'verify')
        self.assertTrue(self.a.login('newlistener', 'long-new-password', 'native'))

    def test_registration_validation_duplicates_and_rate_limit(self):
        path = '/api/v1/native/register'
        valid = {'username':'newlistener','email':'new@example.invalid','password':'long-new-password'}
        self.assertEqual(self.call(path, dict(valid, password='short'))[0], 400)
        self.assertEqual(self.call(path, dict(valid, admin=True))[0], 400)
        self.assertEqual(self.call(path, valid)[0], 200)
        self.assertEqual(self.call(path, valid)[0], 400)
        self.assertEqual(self.call(path, dict(valid, username='another'))[0], 400)
        self.assertEqual(self.call(path, valid)[0], 429)

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.a.db.close(); self.tmp.cleanup()

    def call(self, path, data=None, token=None, raw=None):
        c = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        method = 'POST' if data is not None or raw is not None else 'GET'
        headers = {'Content-Type': 'application/json'}
        if token: headers['Authorization'] = 'Bearer ' + token
        c.request(method, path, raw if raw is not None else json.dumps(data) if data is not None else None, headers)
        r = c.getresponse(); body = json.loads(r.read()); status = r.status; headers = dict(r.getheaders()); c.close()
        response = CONTRACT['paths'][path][method.lower()]['responses'][str(status)]
        if '$ref' in response:
            response = CONTRACT['components']['responses'][response['$ref'].split('/')[-1]]
        schema = response['content']['application/json']['schema']
        validate(self, body, schema)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIn('X-Request-ID', headers)
        return status, body

    def test_complete_session_lifecycle_and_bad_json(self):
        base = '/api/v1/native/'
        self.assertEqual(self.call(base + 'capabilities')[0], 200)
        _, pair = self.call(base + 'sessions', {'username':'listener','password':'long-test-password','device_name':'iPad'})
        token = pair['access_token']
        status, listing = self.call(base + 'sessions', token=token)
        self.assertEqual(status, 200)
        self.assertEqual(listing['items'][0]['id'], pair['session_id'])
        status, renewed = self.call(base + 'refresh', {'refresh_token':pair['refresh_token']})
        self.assertEqual(status, 200)
        self.assertEqual(self.call(base + 'sessions/revoke', {'session_id':pair['session_id']}, renewed['access_token'])[0], 200)
        self.assertEqual(self.call(base + 'sessions', token=token)[0], 401)
        self.assertEqual(self.call(base + 'refresh', {'refresh_token':renewed['refresh_token']})[0], 401)
        self.assertEqual(self.call(base + 'sessions', raw='{bad')[0], 400)

    def test_server_proof_needs_no_account_and_validates_nonce(self):
        _, caps = self.call('/api/v1/native/capabilities')
        self.assertTrue(caps['server_public_key'])
        status, proof = self.call('/api/v1/native/server-proof', {'nonce':'a'*64})
        self.assertEqual(status, 200)
        self.assertEqual(proof['server_id'], caps['server_id'])
        self.assertEqual(proof['nonce'], 'a'*64)
        for body in ({'nonce':'invalid'}, {'nonce':'a'*64,'server_id':'spoof'}, {}):
            self.assertEqual(self.call('/api/v1/native/server-proof', body)[0], 400)

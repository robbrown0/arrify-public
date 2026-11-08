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
        self.a.register('listener', 'long-test-password', 'listener@example.invalid')
        self.a.approve('listener')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.accounts = self.a
        self.server.library = self.server.player = SimpleNamespace()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

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

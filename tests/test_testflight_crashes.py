import contextlib
import io
import unittest
from unittest.mock import patch
from tools import testflight_crashes as crashes


class CrashReaderTests(unittest.TestCase):
    def run_reader(self, status):
        visited = []
        def request(url, token=None):
            path = url.removeprefix(crashes.API).split('?')[0]
            if path == '/v1/apps':
                return {'data': [{'id': 'app'}]}
            if path.endswith('/betaFeedbackCrashSubmissions'):
                return {'data': [{'id': 'old'}, {'id': 'new'}]}
            visited.append(path)
            if '/old/' in path:
                raise RuntimeError(f'GET {path}: HTTP {status} TEST')
            return {'data': {'attributes': {'logText': 'Exception Type: EXC_BAD_ACCESS'}}}
        output = io.StringIO()
        with patch.object(crashes, 'request', side_effect=request), patch.object(crashes, 'signing_token', return_value='fixture'), contextlib.redirect_stdout(output):
            crashes.main()
        return visited, output.getvalue()

    def test_missing_older_log_does_not_hide_newer_crash(self):
        visited, output = self.run_reader(404)
        self.assertEqual(len(visited), 2)
        self.assertIn('EXC_BAD_ACCESS', output)
        self.assertIn('Crash log unavailable', output)

    def test_authorization_failure_is_not_silently_skipped(self):
        with self.assertRaisesRegex(RuntimeError, 'HTTP 403'):
            self.run_reader(403)

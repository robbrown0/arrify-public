import os
import unittest
from unittest.mock import patch
from tools import testflight_internal as internal


class InternalDistributionTests(unittest.TestCase):
    def run_distribution(self, automatic=True, state='IN_BETA_TESTING'):
        writes = []
        def request(url, method='GET', payload=None, token=None):
            path = url.removeprefix(internal.API).split('?')[0]
            if method != 'GET':
                writes.append(path)
                return {}
            if path == '/v1/apps':
                return {'data': [{'id': 'app'}]}
            if path == '/v1/builds':
                return {'data': [{'id': 'build', 'attributes': {'processingState': 'VALID', 'buildAudienceType': 'INTERNAL_ONLY'}, 'relationships': {'preReleaseVersion': {'data': {'id': 'version'}}}}], 'included': [{'id': 'version', 'type': 'preReleaseVersions', 'attributes': {'version': '0.3.0'}}]}
            if path.endswith('/buildBetaDetail'):
                return {'data': {'attributes': {'internalBuildState': state}}}
            if path.endswith('/betaGroups'):
                return {'data': [{'id': 'internal', 'attributes': {'isInternalGroup': True, 'hasAccessToAllBuilds': automatic}}, {'id': 'external', 'attributes': {'isInternalGroup': False}}]}
            if path == '/v1/betaGroups/internal/relationships/builds':
                return {'data': []}
            self.fail('Unexpected endpoint: ' + path)
        with patch.dict(os.environ, BUILD_NUMBER='8', APP_VERSION='0.3.0'), patch.object(internal, 'request', side_effect=request), patch.object(internal, 'signing_token', return_value='fixture'):
            internal.main()
        return writes

    def test_automatic_group_needs_no_manual_assignment(self):
        self.assertEqual(self.run_distribution(), [])

    def test_manual_assignment_never_touches_external_group(self):
        self.assertEqual(self.run_distribution(automatic=False), ['/v1/betaGroups/internal/relationships/builds'])

    def test_pending_state_does_not_report_success(self):
        with self.assertRaisesRegex(RuntimeError, 'not yet testing'):
            self.run_distribution(state='PROCESSING')

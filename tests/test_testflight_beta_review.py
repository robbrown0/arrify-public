import base64
import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch
from tools import testflight_beta_review as review

class BetaReviewTests(unittest.TestCase):
    def test_der_signature_handles_sign_padding_and_rejects_malformed(self):
        r=b'\x00\x80'+b'\x01'*31; s=b'\x01'
        der=b'\x30'+bytes([len(r)+len(s)+4])+b'\x02'+bytes([len(r)])+r+b'\x02'+bytes([len(s)])+s
        raw=review.der_signature_to_raw(der)
        self.assertEqual(len(raw),64);self.assertEqual(raw[:32],r[1:]);self.assertEqual(raw[32:],b'\x00'*31+b'\x01')
        with self.assertRaises(ValueError):review.der_signature_to_raw(b'\x01\x00')

    def test_unrestricted_demo_prevents_apple_submission(self):
        with patch.dict(os.environ,{'BUILD_NUMBER':'5','APP_VERSION':'0.2.1','ASC_BETA_REVIEW_CONFIG':'{}'}), patch.object(review,'request',return_value={'catalog_tracks':2}), patch.object(review,'signing_token') as sign:
            with self.assertRaisesRegex(RuntimeError,'exactly six'):review.main()
            sign.assert_not_called()

    def test_existing_review_and_group_membership_are_not_submitted_twice(self):
        writes=[]
        config=dict(username='review',password='test-only',first_name='Test',last_name='Reviewer',email='test@example.invalid',phone='+15555550100')
        def request(url,method='GET',payload=None,token=None):
            path=url.removeprefix(review.API).split('?')[0]
            if url.startswith(review.DEMO):
                if url.endswith('/health'):return {'catalog_tracks':6}
                if url.endswith('/sessions'):return {'access_token':'fixture','session_id':'session'}
                if url.endswith('/catalog'):return {'total':6,'items':[{'title':t,'artist':'Arrify Studio'} for t in ['First Light','Open Windows','Bright Steps','Quiet Water','Evening Drift','Last Lantern']]}
                if url.endswith('/revoke'):return {}
                self.fail('Unexpected demo request')
            if method!='GET':writes.append((path,method,payload));return {}
            if path=='/v1/apps':return {'data':[{'id':'app'}]}
            if path=='/v1/builds':return {'data':[{'id':'build','attributes':{'processingState':'VALID','buildAudienceType':'APP_STORE_ELIGIBLE'},'relationships':{'preReleaseVersion':{'data':{'id':'version'}}}}], 'included':[{'id':'version','type':'preReleaseVersions','attributes':{'version':'0.2.1'}}]}
            if path.endswith('/betaAppReviewDetail'):return {'data':{'id':'detail'}}
            if path.endswith('/betaAppLocalizations'):return {'data':[{'id':'locale','attributes':{'locale':'en-US'}}]}
            if path.endswith('/betaBuildLocalizations'):return {'data':[{'id':'build-locale','attributes':{'locale':'en-US'}}]}
            if path.endswith('/betaGroups'):return {'data':[{'id':'group','attributes':{'isInternalGroup':False,'name':'Friends & Family'}}]}
            if path.endswith('/relationships/builds'):return {'data':[{'id':'build'}]}
            if path=='/v1/betaAppReviewSubmissions':return {'data':[{'attributes':{'betaReviewState':'WAITING_FOR_REVIEW'}}]}
            self.fail('Unexpected API request '+path)
        with patch.dict(os.environ,{'BUILD_NUMBER':'5','APP_VERSION':'0.2.1','ASC_BETA_REVIEW_CONFIG':json.dumps(config)}), patch.object(review,'request',side_effect=request), patch.object(review,'signing_token',return_value='fixture'), contextlib.redirect_stdout(io.StringIO()) as output:
            review.main()
        self.assertTrue(all(method=='PATCH' for _,method,_ in writes))
        self.assertIn('WAITING_FOR_REVIEW',output.getvalue());self.assertNotIn('test-only',output.getvalue())
        self.assertEqual(writes[0][2]['data']['attributes']['demoAccountName'],'review')


class PendingReviewTests(unittest.TestCase):
    def test_other_pending_review_stops_before_mutations(self):
        calls = []
        def api(path, method='GET', payload=None):
            calls.append(method)
            return {'data': [{'id': 'old', 'attributes': {'version': '7'}, 'relationships': {'betaAppReviewSubmission': {'data': {'id': 'review'}}}}], 'included': [{'id': 'review', 'type': 'betaAppReviewSubmissions', 'attributes': {'betaReviewState': 'WAITING_FOR_REVIEW'}}]}
        with self.assertRaisesRegex(RuntimeError, 'Another build'):
            review.ensure_no_other_review(api, 'app', 'new')
        self.assertEqual(calls, ['GET'])

    def test_selected_review_is_idempotent(self):
        def api(path):
            return {'data': [{'id': 'selected', 'attributes': {'version': '9'}, 'relationships': {'betaAppReviewSubmission': {'data': {'id': 'review'}}}}], 'included': [{'id': 'review', 'type': 'betaAppReviewSubmissions', 'attributes': {'betaReviewState': 'WAITING_FOR_REVIEW'}}]}
        review.ensure_no_other_review(api, 'app', 'selected')

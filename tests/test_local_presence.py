import hashlib
import hmac
import tempfile
import unittest
from unittest.mock import patch
from app.accounts import Accounts
from app.native_auth import NativeSessions, NativeError
from app.local_presence import LocalPresence

class LocalPresenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.a = Accounts(self.tmp.name); self.addCleanup(self.a.db.close)
        self.a.register('listener','long-test-password','test@example.invalid'); self.a.approve('listener')
        auth = NativeSessions(self.a)
        self.owner = auth.login({'username':'listener','password':'long-test-password','device_name':'player'})
        self.remote = auth.login({'username':'listener','password':'long-test-password','device_name':'remote'})
        self.uid = self.a.identity(self.owner['access_token'],'native')['id']
        self.p = LocalPresence(self.a)
        self.status = {'owner':'device','epoch':3}
        self.p.acquired(self.uid, self.owner['access_token'], {}, {'owner_device_id':'device','owner_epoch':3}, {'device_id':'device'})
        self.secret = 'ab'*32
        self.publish()
    def publish(self, login=None):
        return self.p.dispatch(self.uid,(login or self.owner)['access_token'],dict(action='publish',owner_device_id='device',owner_epoch=3,service='cd'*16,secret=self.secret),self.status)
    def challenge(self, purpose='next'):
        return self.p.dispatch(self.uid,self.remote['access_token'],dict(action='challenge',owner_device_id='device',owner_epoch=3,purpose=purpose),self.status)
    def proof(self, nonce):return hmac.new(bytes.fromhex(self.secret),nonce.encode(),hashlib.sha256).hexdigest()
    def consume(self, challenge, **kw):
        values=dict(uid=self.uid,family=self.remote['session_id'],owner='device',epoch=3,purpose='next',nonce=challenge['nonce'],proof=self.proof(challenge['nonce']))
        values.update(kw);return self.p.consume(**values)
    def test_controller_cannot_fetch_or_overwrite_owner_secret(self):
        with self.assertRaises(NativeError): self.publish(self.remote)
        challenge=self.challenge()
        self.assertEqual(set(challenge),{'service','nonce'})
        self.assertNotIn(self.secret,str(challenge))
    def test_one_use_scoped_to_family_account_epoch_owner_and_command(self):
        for wrong in [dict(uid=self.uid+1),dict(family=self.owner['session_id']),dict(owner='other'),dict(epoch=4),dict(purpose='pause')]:
            challenge=self.challenge();self.assertFalse(self.consume(challenge,**wrong));self.assertTrue(self.consume(challenge))
        challenge=self.challenge();self.assertTrue(self.consume(challenge));self.assertFalse(self.consume(challenge))
    def test_invalid_response_burns_challenge(self):
        challenge=self.challenge();self.assertFalse(self.consume(challenge,proof='0'*64));self.assertFalse(self.consume(challenge))
    def test_expiry_and_key_rotation_reject_old_proofs(self):
        with patch('app.local_presence.time.monotonic',return_value=100):self.publish();challenge=self.challenge()
        with patch('app.local_presence.time.monotonic',return_value=113):self.assertFalse(self.consume(challenge))
        self.publish();challenge=self.challenge();self.secret='ef'*32;self.publish();self.assertFalse(self.consume(challenge))
    def test_remote_commands_cannot_rebind_unchanged_output_session(self):
        state={'owner_device_id':'device','owner_epoch':3}
        self.p.acquired(self.uid,self.remote['access_token'],state,state,{'device_id':'device'})
        with self.assertRaises(NativeError):self.publish(self.remote)
    def test_missing_and_spoofed_public_ip_never_grant_proof(self):
        self.assertFalse(self.p.consume(self.uid,self.remote['session_id'],'device',3,'next','192.168.1.5','8.8.8.8'))
    def test_pending_challenges_bounded(self):
        for _ in range(8):self.challenge()
        with self.assertRaises(NativeError):self.challenge()

    def test_revoking_player_session_invalidates_inflight_proof(self):
        challenge=self.challenge()
        NativeSessions(self.a).revoke(self.uid,self.owner['session_id'])
        self.assertFalse(self.consume(challenge))

import tempfile
import time
import unittest
from types import SimpleNamespace
from app.accounts import Accounts
from app.native_auth import NativeSessions, NativeError
from app.live_activity import LiveActivities

class LiveActivityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.a = Accounts(self.tmp.name); self.addCleanup(self.a.db.close)
        self.a.register('listener','long-test-password','listener@example.invalid'); self.a.approve('listener')
        self.auth = NativeSessions(self.a)
        self.login = self.auth.login({'username':'listener','password':'long-test-password','device_name':'iPad'})
        self.uid = self.a.identity(self.login['access_token'],'native')['id']
        self.state = {'track':{'title':'Song','artist':'Artist'},'listening':{'owner':'player','playing':True,'seen':int(time.time()),'output':'Office'}}
        self.calls = []
        self.code = 200; self.reason = ""
        self.service = LiveActivities(self.a, SimpleNamespace(state=lambda uid:self.state), self.tmp.name,
            sender=SimpleNamespace(send=lambda *args:self.calls.append(args) or (self.code, self.reason)), start_worker=False)
        self.addCleanup(self.service.close)
    def register(self, activity='first', token='a'*64, owner='player'):
        return self.service.dispatch(self.uid,self.login['access_token'],{'action':'register','activity_id':activity,'token':token,'owner_device_id':owner})
    def test_push_coalescing_pause_and_owner_change_end(self):
        self.register(); self.service.tick(); self.service.tick()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.calls[0][1]['aps']['content-state']['destination'],'Office')
        self.state['listening']['playing']=False; self.service.tick()
        self.assertEqual(len(self.calls),2)
        self.state['listening']['owner']='other'; self.service.tick()
        self.assertEqual(self.calls[-1][1]['aps']['event'],'end')
        self.assertEqual(self.a.db.execute('SELECT count(*) FROM live_activities').fetchone()[0],0)
    def test_session_revoke_cascades_and_stops_updates(self):
        self.register(); self.auth.revoke(self.uid,self.login['session_id']); self.service.tick()
        self.assertEqual(self.calls,[])
        self.assertEqual(self.a.db.execute('SELECT count(*) FROM live_activities').fetchone()[0],0)
    def test_rejects_commands_wrong_owner_and_invalid_token(self):
        for data in ({'action':'next'},{'action':'register','activity_id':'x','token':'https://evil','owner_device_id':'player'}):
            with self.assertRaises(NativeError): self.service.dispatch(self.uid,self.login['access_token'],data)
        with self.assertRaises(NativeError):self.register(owner='other-account-player')
    def test_stop_scoped_to_session_and_invalid_token_removed(self):
        self.register()
        second=self.auth.login({'username':'listener','password':'long-test-password','device_name':'Phone'})
        self.service.dispatch(self.uid,second['access_token'],{'action':'stop','activity_id':'first'})
        self.assertEqual(self.a.db.execute('SELECT count(*) FROM live_activities').fetchone()[0],1)
        self.code=410;self.service.tick()
        self.assertEqual(self.a.db.execute('SELECT count(*) FROM live_activities').fetchone()[0],0)
    def test_disabled_service_never_accepts_tokens(self):
        disabled=LiveActivities(self.a,self.service.listening,self.tmp.name,start_worker=False)
        self.assertFalse(disabled.dispatch(self.uid,self.login['access_token'],None)['available'])
        with self.assertRaises(NativeError):disabled.dispatch(self.uid,self.login['access_token'],{'action':'register','activity_id':'x','token':'a'*64,'owner_device_id':'player'})
    def test_invalid_optional_config_does_not_break_playback_startup(self):
        from pathlib import Path
        path = Path(self.tmp.name) / 'secrets';path.mkdir()
        (path / 'apns.json').write_text('{broken')
        with self.assertLogs('app.live_activity',level='WARNING'):
            disabled=LiveActivities(self.a,self.service.listening,self.tmp.name,start_worker=False)
        self.assertFalse(disabled.enabled)

    def test_provider_errors_back_off_without_losing_subscription(self):
        self.register(); self.code=400; self.reason='BadTopic'
        self.service.tick(); self.service.tick()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.a.db.execute('SELECT count(*) FROM live_activities').fetchone()[0],1)

    def test_controls_fence_session_owner_epoch_and_network(self):
        from unittest.mock import Mock, patch
        self.state['listening'].update(epoch=3,revision=7)
        peer=SimpleNamespace(client_address=('192.168.1.20',1234),headers={})
        self.service.local_presence.consume=Mock(return_value=True)
        self.service.listening.command=Mock(return_value=self.state)
        self.register()
        data={'action':'control','activity_id':'first','command':'next','owner_device_id':'player','owner_epoch':3,'nonce':'n','proof':'p'}
        with patch('app.live_activity.snapshot',return_value={'ok':True}):
            self.assertEqual(self.service.dispatch(self.uid,self.login['access_token'],data,peer),{'ok':True})
        self.assertEqual(self.service.listening.command.call_args.args[1]['expected_revision'],7)
        self.service.listening.command.reset_mock()
        for change in ({'owner_epoch':4},{'owner_device_id':'moved'},{'command':'takeover'}):
            with self.assertRaises(NativeError):self.service.dispatch(self.uid,self.login['access_token'],dict(data,**change),peer)
        self.service.local_presence.consume.return_value=False
        remote=SimpleNamespace(client_address=('8.8.8.8',1234),headers={})
        with self.assertRaises(NativeError):self.service.dispatch(self.uid,self.login['access_token'],data,remote)
        second=self.auth.login({'username':'listener','password':'long-test-password','device_name':'Other'})
        with self.assertRaises(NativeError):self.service.dispatch(self.uid,second['access_token'],data,peer)
        self.service.listening.command.assert_not_called()

    def test_real_local_proof_controls_once_without_ip_configuration(self):
        import hashlib, hmac
        from unittest.mock import Mock, patch
        owner=self.auth.login({'username':'listener','password':'long-test-password','device_name':'Actual player'})
        self.state['listening'].update(epoch=3,revision=7)
        presence=self.service.local_presence
        presence.acquired(self.uid,owner['access_token'],{}, {'owner_device_id':'player','owner_epoch':3},{'device_id':'player'})
        presence.dispatch(self.uid,owner['access_token'],dict(action='publish',owner_device_id='player',owner_epoch=3,service='12'*16,secret='ab'*32),self.state['listening'])
        nonce=presence.dispatch(self.uid,self.login['access_token'],dict(action='challenge',owner_device_id='player',owner_epoch=3,purpose='next'),self.state['listening'])['nonce']
        proof=hmac.new(bytes.fromhex('ab'*32),nonce.encode(),hashlib.sha256).hexdigest()
        self.register()
        self.service.listening.command=Mock(return_value=self.state)
        data=dict(action='control',activity_id='first',command='next',owner_device_id='player',owner_epoch=3,nonce=nonce,proof=proof)
        with patch('app.live_activity.snapshot',return_value={'ok':True}):
            self.assertEqual(self.service.dispatch(self.uid,self.login['access_token'],data),{'ok':True})
        with self.assertRaises(NativeError):self.service.dispatch(self.uid,self.login['access_token'],data)
        self.service.listening.command.assert_called_once()
        # A matching public/private IP has no bypass path when a proof is absent.
        data.pop('nonce');data.pop('proof')
        with self.assertRaises(NativeError):self.service.dispatch(self.uid,self.login['access_token'],data,SimpleNamespace(client_address=('192.168.1.20',1),headers={}))

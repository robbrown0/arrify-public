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
        self.code = 200
        self.service = LiveActivities(self.a, SimpleNamespace(state=lambda uid:self.state), self.tmp.name,
            sender=SimpleNamespace(send=lambda *args:self.calls.append(args) or self.code), start_worker=False)
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

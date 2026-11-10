import unittest
from types import SimpleNamespace
from unittest.mock import patch
from app.local_controls import LocalControls

class LocalControlTests(unittest.TestCase):
    def handler(self, address, forwarded=None):
        return SimpleNamespace(client_address=(address,1234), headers={} if forwarded is None else {'X-Forwarded-For':forwarded})
    def test_requires_fresh_owner_same_account_epoch_and_explicit_network(self):
        policy=LocalControls('192.168.1.0/24','')
        state={'owner_device_id':'player','owner_epoch':3}
        with patch('app.local_controls.time.monotonic',return_value=100):
            policy.observe(1,{'action':'heartbeat','device_id':'player'},state,self.handler('192.168.1.10'))
            self.assertTrue(policy.allows(1,'player',3,self.handler('192.168.1.20')))
            for uid,owner,epoch,address in [(2,'player',3,'192.168.1.20'),(1,'other',3,'192.168.1.20'),(1,'player',4,'192.168.1.20'),(1,'player',3,'192.168.2.20'),(1,'player',3,'8.8.8.8')]:
                self.assertFalse(policy.allows(uid,owner,epoch,self.handler(address)))
        with patch('app.local_controls.time.monotonic',return_value=146):
            self.assertFalse(policy.allows(1,'player',3,self.handler('192.168.1.20')))
    def test_proxy_headers_cannot_assert_locality_without_trust(self):
        policy=LocalControls('192.168.1.0/24','172.27.0.1/32')
        self.assertIsNone(policy.address(self.handler('8.8.8.8','192.168.1.20')))
        self.assertIsNone(policy.address(self.handler('172.27.0.1')))
        self.assertIsNone(policy.address(self.handler('172.27.0.1','garbage')))
        self.assertEqual(str(policy.address(self.handler('172.27.0.1','192.168.1.20'))),'192.168.1.20')
        self.assertEqual(str(policy.address(self.handler('172.27.0.1','192.168.1.20, 8.8.8.8'))),'8.8.8.8')
    def test_default_without_network_configuration_denies_controls(self):
        policy=LocalControls('','')
        state={'owner_device_id':'player','owner_epoch':1}
        policy.observe(1,{'action':'heartbeat','device_id':'player'},state,self.handler('192.168.1.10'))
        self.assertFalse(policy.allows(1,'player',1,self.handler('192.168.1.20')))

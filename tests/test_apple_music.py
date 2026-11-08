import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from app.accounts import Accounts
from app.apple_music import AppleMusic


class AppleMusicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys=tempfile.TemporaryDirectory();cls.key=Path(cls.keys.name)/'test.p8'
        subprocess.run(['openssl','genpkey','-algorithm','EC','-pkeyopt','ec_paramgen_curve:P-256','-out',str(cls.key)],check=True,stderr=subprocess.DEVNULL);cls.key.chmod(0o600)
    @classmethod
    def tearDownClass(cls):cls.keys.cleanup()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.a=Accounts(self.tmp.name);self.calls=[]
        self.env={'ARRIFY_APPLE_MUSIC_ENABLED':'1','ARRIFY_APPLE_TEAM_ID':'ABCDEFGHIJ','ARRIFY_APPLE_KEY_ID':'KLMNOPQRST','ARRIFY_APPLE_KEY_FILE':str(self.key)}
        self.now=1000000
        def fetch(path,headers):
            self.calls.append((path,dict(headers)))
            if path=='/v1/me/storefront':return {'data':[{'id':'us'}]}
            row={'id':'12345','attributes':{'name':'Song','artistName':'Artist','albumName':'Album','playParams':{'id':'12345'},'durationInMillis':180000}}
            return {'data':[row]} if '/songs/' in path else {'results':{'songs':{'data':[row,{'id':'9','attributes':{'name':'Not playable'}}]}}}
        self.m=AppleMusic(self.a,'https://arrify.example',self.env,fetch=fetch,now=lambda:self.now)
    def tearDown(self):self.a.db.close();self.tmp.cleanup()
    def test_disabled_by_default_and_no_secret_configuration_exposed(self):
        disabled=AppleMusic(self.a,'https://arrify.example',env={})
        self.assertFalse(disabled.config(1)['available'])
        with self.assertRaises(ValueError):disabled.token()
        text=json.dumps(self.m.config(1));self.assertNotIn(str(self.key),text);self.assertNotIn('ABCDEFGHIJ',text)
    def test_token_is_valid_es256_origin_bound_and_cached_then_rotates(self):
        token=self.m.token();head,payload,signature=token.split('.')
        decode=lambda x:base64.urlsafe_b64decode(x+'='*(-len(x)%4))
        self.assertEqual(json.loads(decode(head)),{'alg':'ES256','kid':'KLMNOPQRST'})
        data=json.loads(decode(payload));self.assertEqual(data['iss'],'ABCDEFGHIJ');self.assertEqual(data['origin'],['https://arrify.example']);self.assertEqual(data['exp'],self.now+86400)
        raw=decode(signature);self.assertEqual(len(raw),64)
        def integer(value):
            value=value.lstrip(b'\0') or b'\0'
            if value[0]&128:value=b'\0'+value
            return b'\x02'+bytes([len(value)])+value
        body=integer(raw[:32])+integer(raw[32:]);der=b'\x30'+bytes([len(body)])+body
        pub=Path(self.tmp.name)/'public.pem';sig=Path(self.tmp.name)/'signature';sig.write_bytes(der)
        subprocess.run(['openssl','pkey','-in',str(self.key),'-pubout','-out',str(pub)],check=True,stderr=subprocess.DEVNULL)
        check=subprocess.run(['openssl','dgst','-sha256','-verify',str(pub),'-signature',str(sig)],input=(head+'.'+payload).encode(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.assertEqual(check.returncode,0);self.assertEqual(self.m.token(),token)
        self.now+=50000;self.assertNotEqual(self.m.token(),token)
    def test_user_authorization_verified_but_never_stored_or_shared(self):
        result=self.m.link(1,'fixture-user-token');self.assertTrue(result['linked']);self.assertEqual(self.m.storefront(1),'us')
        self.assertEqual(self.calls[-1][1]['Music-User-Token'],'fixture-user-token')
        self.m.search('us','Some song');self.assertNotIn('Music-User-Token',self.calls[-1][1])
        self.assertNotIn('fixture-user-token','\n'.join(self.a.db.iterdump()))
        with self.assertRaises(ValueError):self.m.storefront(2)
        self.m.disconnect(1);self.assertFalse(self.m.config(1)['linked'])
    def test_catalog_is_canonical_cached_bounded_and_rejects_arbitrary_ids(self):
        self.assertEqual(len(self.m.search('us','Some song')),1);self.m.search('us','Some song');self.assertEqual(len(self.calls),1)
        t=self.m.track('us','12345');self.assertEqual((t['id'],t['duration']),('12345',180))
        for region,sid in [('us','https://other.invalid/song'),('../','12345')]:
            with self.assertRaises(ValueError):self.m.track(region,sid)
        with self.assertRaises(ValueError):self.m.link(1,'token\nheader')
        for i in range(270):self.m.cached('/fixture/'+str(i))
        self.assertEqual(len(self.m.cache),256)
    def test_busy_and_provider_failures_release_admission(self):
        for _ in range(3):self.assertTrue(self.m.slots.acquire(False))
        with self.assertRaisesRegex(ValueError,'busy'):self.m.search('us','Song')
        for _ in range(3):self.m.slots.release()
        self.m.fetch=lambda *args:(_ for _ in ()).throw(TimeoutError())
        with self.assertRaisesRegex(ValueError,'could not be reached'):self.m.search('us','Song')
        self.assertTrue(self.m.slots.acquire(False));self.m.slots.release()
    def test_world_readable_key_is_rejected(self):
        bad=Path(self.tmp.name)/'bad.p8';bad.write_bytes(self.key.read_bytes());bad.chmod(0o644)
        self.m.key=str(bad)
        with self.assertRaisesRegex(ValueError,'private file permissions'):self.m.token()

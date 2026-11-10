import base64
from pathlib import Path
import subprocess
import tempfile
import unittest
from app.server_identity import ServerIdentity
from app.native_auth import NativeError


class ServerIdentityTests(unittest.TestCase):
    def test_persisted_key_and_challenge_binding(self):
        with tempfile.TemporaryDirectory() as folder:
            identity = ServerIdentity(folder)
            self.assertEqual(identity.public_key, ServerIdentity(folder).public_key)
            self.assertEqual(identity.key.stat().st_mode & 0o777, 0o600)
            proof = identity.proof('server-one', 'a' * 64)
            public = Path(folder) / 'public.der'; public.write_bytes(base64.b64decode(identity.public_key))
            signature = Path(folder) / 'signature'; signature.write_bytes(base64.b64decode(proof['signature']))
            for server, nonce, expected in [('server-one', 'a'*64, 0), ('server-two', 'a'*64, 1), ('server-one', 'b'*64, 1)]:
                result = subprocess.run(['openssl', 'dgst', '-sha256', '-verify', str(public), '-keyform', 'DER', '-signature', str(signature)],
                    input=f'arrify-server-proof-v1\n{server}\n{nonce}'.encode(), capture_output=True)
                self.assertEqual(result.returncode, expected)
            with tempfile.TemporaryDirectory() as other:
                self.assertNotEqual(identity.public_key, ServerIdentity(other).public_key)
    def test_rejects_invalid_nonce(self):
        with tempfile.TemporaryDirectory() as folder:
            identity = ServerIdentity(folder)
            for value in [None, [], 'a'*63, 'a'*65, 'A'*64, '\n'+'a'*64]:
                with self.assertRaises(NativeError): identity.proof('server', value)

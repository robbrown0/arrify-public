"""Persistent server signing identity for verifying alternate HTTPS addresses."""
import base64
import os
import re
import secrets
import subprocess
from pathlib import Path
from .native_auth import NativeError


class ServerIdentity:
    def __init__(self, directory):
        folder = Path(directory) / 'secrets'
        folder.mkdir(mode=0o700, exist_ok=True)
        self.key = folder / 'server-identity.pem'
        if not self.key.exists():
            temporary = folder / ('identity-' + secrets.token_hex(12) + '.tmp')
            try:
                value = subprocess.run(['openssl','genpkey','-algorithm','EC','-pkeyopt','ec_paramgen_curve:P-256'],capture_output=True,check=True,timeout=5).stdout
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd,'wb') as f: f.write(value)
                # Never replace an identity concurrently installed by another process.
                try:os.link(temporary,self.key)
                except FileExistsError:pass
            finally:temporary.unlink(missing_ok=True)
        public = subprocess.run(['openssl','pkey','-in',str(self.key),'-pubout','-outform','DER'],capture_output=True,check=True,timeout=5).stdout
        self.public_key = base64.b64encode(public).decode('ascii')

    def proof(self, server_id, nonce):
        if not isinstance(nonce,str) or not re.fullmatch(r'[0-9a-f]{64}',nonce):
            raise NativeError(400,'invalid_request','Provide a fresh server verification nonce.')
        message = ('arrify-server-proof-v1\n'+server_id+'\n'+nonce).encode('utf-8')
        signature = subprocess.run(['openssl','dgst','-sha256','-sign',str(self.key)],input=message,capture_output=True,check=True,timeout=5).stdout
        return {'server_id':server_id,'nonce':nonce,'signature':base64.b64encode(signature).decode('ascii')}


def identity(accounts):
    with accounts.lock:
        if not hasattr(accounts,'server_signing_identity'):
            accounts.server_signing_identity=ServerIdentity(accounts.directory)
        return accounts.server_signing_identity

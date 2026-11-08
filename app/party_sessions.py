"""Private invitation derivation. Only digests enter the accounts database."""
import base64
import hashlib
import hmac
import os
import secrets
import tempfile
from pathlib import Path


class PartyGone(ValueError):
    def __init__(self,state):
        self.state=state
        super().__init__('This party has been deleted.' if state=='deleted' else 'This party has ended. Ask the host for a new invitation.')


class Invitations:
    def __init__(self,directory):
        path=Path(directory)/'party-invitations.key'
        if not path.exists():
            fd,tmp=tempfile.mkstemp(prefix='.party-key-',dir=directory)
            try:
                with os.fdopen(fd,'wb') as out:
                    out.write(secrets.token_bytes(32));out.flush();os.fsync(out.fileno())
                try:os.link(tmp,path)
                except FileExistsError:pass
                directory_fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(directory_fd)
                finally:os.close(directory_fd)
            finally:os.unlink(tmp)
        if path.stat().st_mode&0o077:raise ValueError('Party invitation key requires private file permissions.')
        self.key=path.read_bytes()
        if len(self.key)!=32:raise ValueError('Invalid party invitation key. Restore it from the protected backup.')

    def token(self,pid,kind):
        # Independent domain-separated capabilities, recoverable for QR/share without
        # keeping plaintext invitations in SQLite. The random session ID is never a key.
        return base64.urlsafe_b64encode(hmac.new(self.key,('arrify-party-v1:'+kind+':'+pid).encode(),hashlib.sha256).digest()[:24]).decode()

    @staticmethod
    def digest(token):return hashlib.sha256(token.encode()).hexdigest()

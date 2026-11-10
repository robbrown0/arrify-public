"""One-use local reachability proofs. All methods run under the account lock.

The server never accepts public-IP or client-reported subnet equality as proof.
Only the session that acquired native playback may publish its Bonjour secret.
Secrets/challenges are memory-only, bounded, short-lived, and never logged.
"""
import hashlib
import hmac
import re
import secrets
import time
from .native_auth import NativeError


class LocalPresence:
    def __init__(self, accounts):
        self.a = accounts
        self.owners = {}
        self.challenges = {}
        self.a.db.execute('''CREATE TABLE IF NOT EXISTS native_output_sessions(
            user_id INTEGER PRIMARY KEY REFERENCES users(id), owner TEXT NOT NULL,
            epoch INTEGER NOT NULL, family TEXT NOT NULL REFERENCES native_families(id) ON DELETE CASCADE)''')

    def family(self, uid, access):
        row = self.a.db.execute('SELECT f.id FROM native_access n JOIN native_families f ON f.id=n.family WHERE n.digest=? AND f.user_id=?', (self.a.digest(access), uid)).fetchone()
        if not row: raise NativeError(401, 'unauthenticated', 'Sign in again.')
        return row[0]

    def acquired(self, uid, access, before, after, data):
        owner, epoch = after.get('owner_device_id'), after.get('owner_epoch')
        if not owner or owner != data.get('device_id'): return
        if (owner, epoch) == (before.get('owner_device_id'), before.get('owner_epoch')): return
        family = self.family(uid, access)
        self.a.db.execute('INSERT INTO native_output_sessions VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET owner=excluded.owner,epoch=excluded.epoch,family=excluded.family', (uid, owner, epoch, family))
        self.owners.pop(uid, None)

    def prune(self):
        now = time.monotonic()
        self.owners = {k:v for k,v in self.owners.items() if v['expires'] > now}
        self.challenges = {k:v for k,v in self.challenges.items() if v['expires'] > now}

    def dispatch(self, uid, access, data, status):
        self.prune()
        family = self.family(uid, access)
        if not isinstance(data, dict): raise NativeError(400, 'invalid_request', 'Provide a local verification action.')
        action = data.get('action')
        owner, epoch = status.get('owner'), status.get('epoch')
        if data.get('owner_device_id') != owner or type(data.get('owner_epoch')) is not int or data['owner_epoch'] != epoch:
            raise NativeError(409, 'playback_changed', 'Playback moved. Open Arrify to refresh.')
        if action == 'publish' and set(data) == {'action','owner_device_id','owner_epoch','service','secret'}:
            bound = self.a.db.execute('SELECT owner,epoch,family FROM native_output_sessions WHERE user_id=?', (uid,)).fetchone()
            if not bound or tuple(bound) != (owner, epoch, family):
                raise NativeError(403, 'owner_session_required', 'The playing device must establish its playback session first.')
            if not all(isinstance(data[k], str) and re.fullmatch(r'[0-9a-f]{32}' if k == 'service' else r'[0-9a-f]{64}', data[k]) for k in ('service','secret')):
                raise NativeError(400, 'invalid_request', 'Invalid local verification key.')
            self.owners[uid] = dict(owner=owner, epoch=epoch, family=family, service=data['service'], secret=data['secret'], expires=time.monotonic()+35)
            return {'ok': True}
        if action == 'challenge' and set(data) == {'action','owner_device_id','owner_epoch','purpose'}:
            record = self.owners.get(uid)
            purpose = data['purpose']
            if purpose not in ('status', 'pause', 'resume', 'previous', 'next'):
                raise NativeError(400, 'invalid_request', 'Unsupported local verification purpose.')
            if not record or (record['owner'], record['epoch']) != (owner, epoch) or record['family'] == family:
                raise NativeError(403, 'local_network_required', 'The playing device is not available for local verification.')
            if sum(v['family'] == family for v in self.challenges.values()) >= 8:
                raise NativeError(429, 'rate_limited', 'Retry shortly.')
            nonce = secrets.token_hex(32)
            self.challenges[nonce] = dict(uid=uid, family=family, owner=owner, epoch=epoch, purpose=purpose,
                secret=record['secret'], expires=time.monotonic()+12)
            return {'nonce': nonce, 'service': record['service']}
        raise NativeError(400, 'invalid_request', 'Invalid local verification action.')

    def consume(self, uid, family, owner, epoch, purpose, nonce, proof):
        self.prune()
        if not isinstance(nonce, str) or not isinstance(proof, str): return False
        record = self.challenges.get(nonce)
        if not record or (record['uid'],record['family'],record['owner'],record['epoch'],record['purpose']) != (uid,family,owner,epoch,purpose): return False
        # Consume even a failed response, so a challenge cannot be brute-forced/replayed.
        self.challenges.pop(nonce, None)
        current = self.owners.get(uid)
        if not current or (current['owner'],current['epoch'],current['secret']) != (owner,epoch,record['secret']): return False
        bound = self.a.db.execute('''SELECT 1 FROM native_output_sessions o JOIN native_families f ON f.id=o.family
            JOIN sessions s ON s.digest=f.anchor WHERE o.user_id=? AND o.owner=? AND o.epoch=? AND o.family=?
            AND f.expires>? AND s.expires>?''', (uid, owner, epoch, current['family'], int(time.time()), int(time.time()))).fetchone()
        if not bound: return False
        expected = hmac.new(bytes.fromhex(record['secret']), nonce.encode('ascii'), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, proof)

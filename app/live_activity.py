"""ActivityKit updates and same-account, same-network remote controls.

Subscriptions belong to a native session family and expire within seven hours.
APNs keys stay in the private data mount. Unconfigured servers do not send pushes.
"""
import base64
import json
import logging
from pathlib import Path
import re
import subprocess
import threading
import time
from .native_auth import NativeError
from .local_presence import LocalPresence
from .activity_artwork import ActivityArtwork
from .native_playback import snapshot
import secrets


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=')


def _raw_signature(value):
    # P-256 signatures are a short DER SEQUENCE of two positive INTEGERs.
    if len(value) < 8 or value[0] != 48 or value[1] != len(value) - 2:
        raise ValueError('Invalid signature')
    offset, parts = 2, []
    for _ in range(2):
        if value[offset] != 2: raise ValueError('Invalid signature')
        size = value[offset + 1]; offset += 2
        parts.append(int.from_bytes(value[offset:offset + size], 'big').to_bytes(32, 'big')); offset += size
    if offset != len(value): raise ValueError('Invalid signature')
    return b''.join(parts)


class APNs:
    def __init__(self, directory):
        self.config = None
        self.jwt = ''; self.issued = 0
        path = Path(directory) / 'secrets/apns.json'
        if path.is_file():
            config = json.loads(path.read_text())
            if not isinstance(config, dict): raise ValueError("Invalid APNs configuration")
            if not all(re.fullmatch(r'[A-Z0-9]{10}', config.get(k, '')) for k in ('team_id', 'key_id')):
                raise ValueError('Invalid APNs configuration identifiers')
            if config.get('environment') not in ('production', 'sandbox'):
                raise ValueError('Invalid APNs environment')
            self.key = path.parent / 'apns.p8'
            if not self.key.is_file(): raise ValueError('Missing APNs signing key')
            self.config = config

    def send(self, token, payload, activity):
        now = int(time.time())
        if now - self.issued > 2400:
            message = _b64(json.dumps({'alg': 'ES256', 'kid': self.config['key_id']}).encode()) + b'.' + _b64(json.dumps({'iss': self.config['team_id'], 'iat': now}).encode())
            signature = subprocess.run(['openssl', 'dgst', '-sha256', '-sign', str(self.key)], input=message, capture_output=True, check=True, timeout=5).stdout
            self.jwt = (message + b'.' + _b64(_raw_signature(signature))).decode(); self.issued = now
        host = 'api.push.apple.com' if self.config['environment'] == 'production' else 'api.sandbox.push.apple.com'
        # All sensitive arguments go through stdin, never argv or logs.
        options = {'url': 'https://' + host + '/3/device/' + token, 'request': 'POST',
                   'data': json.dumps(payload, separators=(',', ':'), ensure_ascii=False)}
        lines = [key + ' = ' + json.dumps(value) for key, value in options.items()]
        for header in ['authorization: bearer ' + self.jwt, 'apns-topic: com.robnets.arrify.push-type.liveactivity',
                       'apns-push-type: liveactivity', 'apns-priority: 5', 'apns-expiration: ' + str(now + 90),
                       'apns-collapse-id: ' + activity, 'content-type: application/json']:
            lines.append('header = ' + json.dumps(header))
        result = subprocess.run(['curl', '--http2', '--silent', '--max-time', '10', '--max-filesize', '4096', '--write-out', '\n%{http_code}', '--config', '-'],
                                input='\n'.join(lines).encode(), capture_output=True, timeout=12, check=True)
        body, code = result.stdout.rsplit(b'\n', 1)
        response = json.loads(body) if body else {}
        return int(code), response.get('reason', '')


class LiveActivities:
    def __init__(self, accounts, listening, directory, sender=None, start_worker=True):
        self.a, self.listening = accounts, listening
        self.artwork = ActivityArtwork(getattr(getattr(getattr(listening, "p", None), "lib", None), "music", None))
        with self.a.lock: self.local_presence = LocalPresence(accounts)
        try:self.sender = sender if sender is not None else APNs(directory)
        except (OSError, ValueError, TypeError):
            logging.getLogger(__name__).warning('APNs configuration is invalid; remote Lock Screen updates are disabled.')
            self.sender = None
        self.enabled = self.sender is not None and (sender is not None or self.sender.config is not None)
        self.stopped = threading.Event(); self.sent = {}; self.retry_after = 0
        with self.a.lock:
            self.a.db.execute('''CREATE TABLE IF NOT EXISTS live_activities(
                family TEXT NOT NULL REFERENCES native_families(id) ON DELETE CASCADE,
                activity TEXT NOT NULL, token TEXT NOT NULL UNIQUE, owner TEXT NOT NULL,
                expires INTEGER NOT NULL, PRIMARY KEY(family,activity))''')
        if self.enabled and start_worker:
            self.worker = threading.Thread(target=self.background, daemon=True, name='live-activities'); self.worker.start()

    def dispatch(self, uid, access, data, handler=None):
        if data is None:
            status = self.listening.state(uid)['listening']
            allowed = False  # Legacy clients cannot authorize controls from an IP address.
            return {'available': self.enabled, 'controls_available': bool(self.enabled and allowed), 'read_only': not (self.enabled and allowed)}
        if not isinstance(data, dict) or data.get('action') not in ('register', 'stop', 'control', 'verify'):
            raise NativeError(400, 'invalid_request', 'Choose a Live Activity action.')
        fields = {'action', 'activity_id'} if data['action'] == 'stop' else {'action', 'activity_id', 'token', 'owner_device_id'}
        if data['action'] == 'control': fields = {'action', 'activity_id', 'command', 'owner_device_id', 'owner_epoch', 'nonce', 'proof'}
        if data['action'] == 'verify': fields = {'action', 'activity_id', 'owner_device_id', 'owner_epoch', 'nonce', 'proof'}
        if set(data) != fields: raise NativeError(400, 'invalid_request', 'Invalid activity fields.')
        activity = data.get('activity_id', '')
        if not isinstance(activity, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,64}', activity):
            raise NativeError(400, 'invalid_request', 'Invalid activity identifier.')
        with self.a.lock:
            row = self.a.db.execute('SELECT f.id FROM native_access n JOIN native_families f ON f.id=n.family WHERE n.digest=? AND f.user_id=?', (self.a.digest(access), uid)).fetchone()
            if not row: raise NativeError(401, 'unauthenticated', 'Sign in again.')
            family = row[0]
            if data['action'] == 'verify':
                status = self.listening.state(uid)['listening']
                allowed = (self.enabled and data['owner_device_id'] == status['owner'] and type(data['owner_epoch']) is int and data['owner_epoch'] == status.get('epoch')
                    and self.local_presence.consume(uid, family, status['owner'], status.get('epoch'), 'status', data['nonce'], data['proof']))
                return {'controls_available': bool(allowed)}
            if data['action'] == 'stop':
                self.a.db.execute('DELETE FROM live_activities WHERE family=? AND activity=?', (family, activity))
                return {'ok': True}
            if data['action'] == 'control':
                row = self.a.db.execute('SELECT owner FROM live_activities WHERE family=? AND activity=? AND expires>?', (family, activity, int(time.time()))).fetchone()
                state = self.listening.state(uid); status = state['listening']
                owner, epoch = data.get('owner_device_id'), data.get('owner_epoch')
                if not row or row[0] != owner or status['owner'] != owner or type(epoch) is not int or status.get('epoch') != epoch:
                    raise NativeError(409, 'playback_changed', 'Playback moved. Open Arrify to refresh.')
                if not self.local_presence.consume(uid, family, owner, epoch, data.get('command'), data.get('nonce'), data.get('proof')):
                    raise NativeError(403, 'local_network_required', 'Both devices must be reachable on the same local network.')
                command = data.get('command')
                if command not in ('pause', 'resume', 'previous', 'next'):
                    raise NativeError(400, 'invalid_request', 'Unsupported Lock Screen control.')
                # The account lock fences ownership and revision through the command.
                result = self.listening.command(uid, {'action': command, 'device_id': self.a.digest('lockscreen:' + family),
                    'expected_revision': status['revision'], 'transition_id': secrets.token_hex(16)})
                return snapshot(result)
            if not self.enabled: raise NativeError(503, 'push_unavailable', 'Live Activity updates are not configured on this server.')
            token, owner = data.get('token', ''), data.get('owner_device_id', '')
            if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{64,512}', token):
                raise NativeError(400, 'invalid_request', 'Invalid activity token.')
            current = self.listening.state(uid)['listening']['owner']
            if not isinstance(owner, str) or not owner or owner != current:
                raise NativeError(409, 'playback_changed', 'Playback moved. Open the player again.')
            self.a.db.execute('DELETE FROM live_activities WHERE expires<?', (int(time.time()),))
            count = self.a.db.execute('SELECT count(*) FROM live_activities l JOIN native_families f ON f.id=l.family WHERE f.user_id=? AND NOT(l.family=? AND l.activity=?)', (uid, family, activity)).fetchone()[0]
            if count >= 4: raise NativeError(429, 'too_many_activities', 'Stop a Live Activity on another device first.')
            # A push token cannot be reassigned to another signed-in session.
            old = self.a.db.execute('SELECT family FROM live_activities WHERE token=?', (token,)).fetchone()
            if old and old[0] != family: raise NativeError(409, 'activity_conflict', 'Start a new Live Activity.')
            self.a.db.execute('INSERT INTO live_activities VALUES(?,?,?,?,?) ON CONFLICT(family,activity) DO UPDATE SET token=excluded.token,owner=excluded.owner',
                              (family, activity, token, owner, int(time.time()) + 7 * 3600))
        return {'ok': True}

    def tick(self):
        now = int(time.time())
        if now < self.retry_after: return
        with self.a.lock:
            self.a.db.execute('DELETE FROM live_activities WHERE expires<?', (now,))
            rows = [dict(r) for r in self.a.db.execute('''SELECT l.*,f.user_id FROM live_activities l
                JOIN native_families f ON f.id=l.family JOIN sessions s ON s.digest=f.anchor JOIN users u ON u.id=f.user_id
                WHERE f.expires>? AND s.expires>? AND u.approved=1 AND u.frozen=0 AND u.deleted_at=0''', (now, now))]
        active = {(r['family'], r['activity']) for r in rows}
        self.sent = {k: v for k, v in self.sent.items() if k in active}
        states = {}
        for row in rows:
            uid = row['user_id']; key = (row['family'], row['activity'])
            if uid not in states: states[uid] = self.listening.state(uid)
            state = states[uid]; status = state['listening']; track = state.get('track') or {}
            ended = status['owner'] != row['owner'] or not track or now - status.get('seen', 0) > 120
            content = {'title': str(track.get('title') or 'Playback ended')[:200], 'artist': str(track.get('artist') or '')[:200],
                       'destination': str(status.get('output') or status.get('device_name') or 'Another device')[:100],
                       'playing': bool(status['playing']) and not ended, 'updatedAt': now}
            content['artwork'] = self.artwork.get(track.get('id')) if not ended else None
            # Leave room for attributes and the APNs envelope under ActivityKit's 4 KB limit.
            if len(json.dumps(content, ensure_ascii=False).encode()) > 3000:
                content['artwork'] = None
            fingerprint = json.dumps({k: v for k, v in content.items() if k != 'updatedAt'}, sort_keys=True)
            previous = self.sent.get(key)
            if not ended and previous and previous[0] == fingerprint and now - previous[1] < 60: continue
            aps = {'timestamp': now, 'event': 'end' if ended else 'update', 'content-state': content, 'stale-date': now + 90}
            if ended: aps['dismissal-date'] = now
            try: code, reason = self.sender.send(row['token'], {'aps': aps}, row['activity'])
            except (OSError, ValueError, subprocess.SubprocessError):
                self.retry_after = now + 30; return
            invalid_token = code == 410 or (code == 400 and reason in ('BadDeviceToken', 'DeviceTokenNotForTopic'))
            if code == 200 or invalid_token:
                if ended or invalid_token:
                    with self.a.lock:self.a.db.execute('DELETE FROM live_activities WHERE family=? AND activity=?', key)
                    self.sent.pop(key, None)
                else:self.sent[key] = (fingerprint, now)
            else:
                # BadTopic and other provider errors are not invalid device tokens.
                # Keep subscriptions so correcting configuration can restore updates.
                logging.getLogger(__name__).warning('APNs update deferred (HTTP %s).', code)
                self.retry_after = now + (30 if code == 429 or code >= 500 else 300); return

    def background(self):
        while not self.stopped.wait(5):
            try:self.tick()
            except Exception:
                # Do not log payloads, tokens, account names or signing errors.
                self.retry_after = int(time.time()) + 30

    def close(self):self.stopped.set()

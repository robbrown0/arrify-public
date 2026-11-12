"""Bounded native media preparation and session-family scoped capabilities."""
import base64
from concurrent.futures import ThreadPoolExecutor
import hmac
import json
import os
import threading
import time
from urllib.parse import urlencode
from app.errors import CatalogError
from .native_auth import NativeError


from .media_identity import source_revision


class NativeMedia:
    TTL = 3600

    def __init__(self, server, now=time.time):
        self.s, self.a, self.now = server, server.accounts, now
        self.lock = threading.Lock()
        self.slot = threading.BoundedSemaphore(1)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='native-prepare')
        self.jobs = {}

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=True)

    def window(self, uid: int, device: str, epoch: int) -> list:
        state = self.s.player.load(uid)
        listening = state.get('listening', {})
        if listening.get('owner') != device or listening.get('epoch') != epoch or not device:
            raise NativeError(409, 'output_changed', 'Playback belongs to another output.')
        return list(dict.fromkeys([state.get('current')] + state.get('upcoming', [])[:3]))

    def family(self, uid: int, access: str):
        row = self.a.db.execute('''SELECT f.id,f.expires FROM native_access n
            JOIN native_families f ON f.id=n.family JOIN sessions s ON s.digest=f.anchor
            WHERE n.digest=? AND f.user_id=? AND s.expires>?''',
            (self.a.digest(access), uid, int(self.now()))).fetchone()
        if not row:
            raise NativeError(401, 'native_session_required', 'Sign in with a renewable native session.')
        return row

    @staticmethod
    def encode(value):
        return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')

    def grant(self, uid, family, expires, tid, device, epoch, profile, revision):
        claims = {'v': 2, 'u': uid, 'f': family, 't': tid, 'd': device,
                  'e': epoch, 'p': profile, 'r': revision, 'x': expires}
        body = self.encode(json.dumps(claims, sort_keys=True, separators=(',', ':')).encode())
        return body + '.' + self.encode(hmac.digest(self.s.stream_grants.key, body.encode(), 'sha256'))

    def identity(self, tid: int, q: dict, check_expiry: bool = True, file_stat=None) -> dict:
        try:
            for name in ('native_grant', 'device_id', 'owner_epoch', 'profile', 'level'):
                if len(q.get(name, [])) != 1: raise ValueError()
            token = q['native_grant'][0]
            if len(token) > 1800 or 'grant' in q: raise ValueError()
            body, signature = token.split('.')
            expected = self.encode(hmac.digest(self.s.stream_grants.key, body.encode('ascii'), 'sha256'))
            if not hmac.compare_digest(signature, expected): raise ValueError()
            c = json.loads(base64.b64decode(body + '=' * (-len(body) % 4), altchars=b'-_', validate=True))
            if c['v'] != 2 or c['t'] != tid or c['d'] != q['device_id'][0] or str(c['e']) != q['owner_epoch'][0]: raise ValueError()
            if c['p'] != q['profile'][0] or q['level'][0] != 'off': raise ValueError()
            if check_expiry and self.now() >= c['x']: raise ValueError()
            with self.a.lock:
                row = self.a.db.execute('''SELECT f.id FROM native_families f
                    JOIN sessions s ON s.digest=f.anchor JOIN users u ON u.id=f.user_id
                    WHERE f.id=? AND f.user_id=? AND s.expires>? AND u.approved=1
                    AND u.frozen=0 AND u.deleted_at=0''', (c['f'], c['u'], int(self.now()))).fetchone()
                if not row or tid not in self.window(c['u'], c['d'], c['e']): raise ValueError()
            _, filename = self.s.library.music._track_file(tid)
            if source_revision(filename, file_stat) != c['r']: raise ValueError()
            return {'id': c['u']}
        except (ValueError, TypeError, KeyError, UnicodeError, AttributeError, OverflowError, OSError, NativeError, CatalogError):
            raise PermissionError('Native playback link expired or is no longer authorized.') from None

    def prepare(self, filename: str, profile: str, retry: bool = False) -> str:
        target = self.s.render_cache.target(filename, profile, 0)
        if target.is_file(): return 'ready'
        key = str(target)
        with self.lock:
            previous = self.jobs.get(key)
            if previous:
                if not previous['future'].done(): return 'preparing'
                if previous['future'].exception() is not None:
                    if previous['attempts'] >= 3 and not retry: return 'failed'
                    if not retry and self.now() - previous['completed'] < 30: return 'retrying'
            if not self.slot.acquire(blocking=False): return 'queued'
            # No unbounded executor queue or retained task history.
            for old in list(self.jobs):
                if len(self.jobs) < 128: break
                if self.jobs[old]['future'].done(): del self.jobs[old]
            job = {'future': None, 'completed': None, 'attempts': 1 if retry or not previous or previous['future'].exception() is None else previous['attempts'] + 1}
            def work():
                try:
                    self.s.render_cache.render(filename, profile, 0, self.s.transcodes)
                finally:
                    job['completed'] = self.now()
                    self.slot.release()
            try:
                future = self.pool.submit(work)
            except RuntimeError:
                self.slot.release()
                return 'retrying'
            job['future'] = future
            self.jobs[key] = job
            return 'preparing'

    def manifest(self, uid: int, access: str, data: dict) -> dict:
        device, epoch = data.get('device_id'), data.get('owner_epoch')
        profile = data.get('profile', 'original')
        if not isinstance(device, str) or type(epoch) is not int or profile not in ('original', 'aac', 'mp3'):
            raise NativeError(400, 'invalid_request', 'Provide the output device, epoch and a supported profile.')
        if data.get('level', 'off') != 'off':
            raise NativeError(400, 'unsupported_profile', 'Native preparation currently supports leveling off.')
        with self.a.lock:
            family = self.family(uid, access)
            ids = self.window(uid, device, epoch)
            queue_revision = self.s.player.load(uid).get('listening', {}).get('revision', 0)
        expires = min(int(self.now()) + self.TTL, family['expires'])
        items = []
        for tid in ids:
            if tid is None: continue
            try:
                row, filename = self.s.library.music._track_file(tid)
                revision = source_revision(filename)
                mime = row['mime_type'] if profile == 'original' else 'audio/mp4' if profile == 'aac' else 'audio/mpeg'
                state = 'ready' if profile == 'original' else self.prepare(filename, profile, data.get('retry') is True)
                target = filename if profile == 'original' else self.s.render_cache.target(filename, profile, 0)
                size = os.stat(target).st_size if state == 'ready' else None
                if size is not None and size <= 0: raise ValueError('Empty audio')
                url = None
                if state == 'ready':
                    token = self.grant(uid, family['id'], expires, tid, device, epoch, profile, revision)
                    url = '/api/v1/stream/' + str(tid) + '?' + urlencode({'native_grant': token,
                        'device_id': device, 'owner_epoch': epoch, 'profile': profile, 'level': 'off'})
                items.append({'track_id': tid, 'source_revision': revision, 'profile': profile,
                    'state': state, 'duration_seconds': None, 'mime_type': mime, 'size_bytes': size, 'url': url,
                    'url_expires': expires if url else None, 'range_supported': True})
            except (OSError, ValueError, CatalogError):
                items.append({'track_id': tid, 'state': 'unavailable', 'source_revision': None,
                    'profile': profile, 'duration_seconds': None, 'mime_type': None, 'size_bytes': None,
                    'url': None, 'url_expires': None, 'range_supported': False})
        return {'device_id': device, 'owner_epoch': epoch, 'queue_revision': queue_revision, 'items': items, 'retry_after_seconds': 2}

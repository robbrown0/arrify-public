"""Private, bounded Audiobookshelf adapter. Provider credentials never leave Arrify."""
import json
from collections import OrderedDict
import os
from pathlib import Path
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .spotify import TokenVault


class BookError(ValueError):
    def __init__(self, message, status=503, reason=None):
        super().__init__(message)
        self.status = status
        self.reason = reason


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise BookError('Invalid audiobook identifier.', 400)
    return value


def origin(value):
    value = str(value).strip().rstrip('/')
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ('', '/audiobookshelf')
            or any(c in value for c in '\r\n\\')):
        raise BookError('Enter the Audiobookshelf server address without credentials or query parameters.', 400)
    return value


class AudiobookProvider:
    def __init__(self, accounts, db, db_lock, directory, fetch=None, now=time.time):
        self.a, self.db, self.lock, self.now = accounts, db, db_lock, now
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.vault = TokenVault(self.directory)
        self.fetch = fetch
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.slots = threading.BoundedSemaphore(3)
        self.admin_lock = threading.RLock()
        self.users = [threading.RLock() for _ in range(64)]
        self.cached_config = None
        self.cached_users = OrderedDict()
        with self.lock:
            self.db.execute('CREATE TABLE IF NOT EXISTS provider_config(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL)')
            self.db.execute('''CREATE TABLE IF NOT EXISTS provider_accounts(
                arrify_id INTEGER PRIMARY KEY, payload TEXT NOT NULL)''')
        # A private bootstrap is optional. The database holds an encrypted copy.
        bootstrap = self.directory / 'bootstrap.json'
        if not self.config() and bootstrap.is_file():
            if bootstrap.stat().st_mode & 0o077: raise ValueError('Audiobook bootstrap requires private permissions.')
            value = json.loads(bootstrap.read_text())
            self.save_config({**value, 'origin': origin(value['origin']), 'enabled': bool(value.get('enabled', False))})

    def config(self):
        with self.lock: row = self.db.execute('SELECT payload FROM provider_config WHERE id=1').fetchone()
        if not row: return {}
        with self.lock:
            if self.cached_config is None or self.cached_config[0] != row[0]:
                self.cached_config = row[0], self.vault.open(0, row[0])
            return dict(self.cached_config[1])

    def save_config(self, value):
        sealed = self.vault.seal(0, value)
        with self.lock:
            self.db.execute('INSERT INTO provider_config VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (sealed,))

    def public_config(self):
        value = self.config()
        return {'enabled': bool(value.get('enabled')), 'configured': bool(value.get('origin') and value.get('password')),
                'origin': value.get('origin', ''), 'libraries': value.get('libraries', []),
                'username': value.get('username', '')}

    def available(self):
        value = self.config()
        return bool(value.get('enabled') and value.get('origin') and value.get('password') and value.get('libraries'))

    def require(self):
        if not self.available(): raise BookError('Audiobooks are not available on this server yet.', 404)

    def call(self, method, path, data=None, token=None, headers=None, raw=False, settings=None):
        settings = self.config() if settings is None else settings
        if not settings.get('origin'): raise BookError('The audiobook library is not configured.')
        if not path.startswith('/') or path.startswith('//') or '\\' in path or any(c in path for c in '\r\n'):
            raise BookError('Invalid audiobook resource.', 400)
        request_headers = {'Accept': 'application/json', **(headers or {})}
        if token: request_headers['Authorization'] = 'Bearer ' + token
        body = None
        if data is not None:
            body = json.dumps(data).encode(); request_headers['Content-Type'] = 'application/json'
        if not self.slots.acquire(timeout=1): raise BookError('The audiobook library is busy. We’ll reconnect shortly.')
        try:
            if self.fetch: return self.fetch(method, path, data, request_headers, raw)
            req = urllib.request.Request(settings['origin'] + path, data=body, method=method, headers=request_headers)
            response = self.opener.open(req, timeout=8)
            if raw: return response
            with response:
                content = response.read(8 * 1024 * 1024 + 1)
                if len(content) > 8 * 1024 * 1024: raise BookError('The audiobook library response is too large.')
                return json.loads(content) if content and response.headers.get('Content-Type', '').startswith('application/json') else {}
        except urllib.error.HTTPError as exc:
            status = exc.code
            if raw and status == 416: return exc
            exc.close()
            if status in (401, 403, 404): raise BookError('This audiobook is not available to your account.', status) from None
            raise BookError('The audiobook library is temporarily unavailable.') from None
        except (OSError, ValueError) as exc:
            if isinstance(exc, BookError): raise
            raise BookError('The audiobook library is reconnecting. Your place is saved.', reason=type(exc).__name__) from None
        finally: self.slots.release()

    def login(self, value):
        result = self.call('POST', '/login', {'username': value['username'], 'password': value['password']},
                           headers={'x-return-tokens': 'true'}, settings=value if value.get('origin') else None)
        user = result.get('user', {})
        access = user.get('accessToken')
        if not access or not user.get('id'): raise BookError('The audiobook server returned an unsupported sign-in response.')
        return {**value, 'access': access, 'refresh': user.get('refreshToken'), 'provider_id': user['id'],
                'refresh_pending': False, 'type': user.get('type'), 'permissions': user.get('permissions', {})}

    def authenticated(self, value, save, method, path, data=None, headers=None, raw=False):
        if not value.get('access'):
            value = self.login(value); save(value)
        try: return self.call(method, path, data, value['access'], headers, raw)
        except BookError as exc:
            if exc.status != 401: raise
        # Persist the ambiguous-rotation boundary. After a lost response/restart,
        # authenticate afresh using our managed password; never replay a consumed token.
        if value.get('refresh') and not value.get('refresh_pending'):
            value = {**value, 'refresh_pending': True}; save(value)
            try: result = self.call('POST', '/auth/refresh', {}, headers={'x-refresh-token': value['refresh']})
            except BookError as exc:
                if exc.status != 401: raise
                value = self.login(value); save(value)
                return self.call(method, path, data, value['access'], headers, raw)
            user = result.get('user', {})
            if not user.get('accessToken') or not user.get('refreshToken'):
                raise BookError('The audiobook session could not be renewed.')
            value = {**value, 'access': user['accessToken'], 'refresh': user['refreshToken'], 'refresh_pending': False}
        else: value = self.login(value)
        save(value)
        return self.call(method, path, data, value['access'], headers, raw)

    def admin(self, method, path, data=None):
        with self.admin_lock:
            value = self.config()
            return self.authenticated(value, self.save_config, method, path, data)

    def configure(self, data):
        with self.admin_lock:
            old = self.config()
            value = {**old, 'origin': origin(data.get('origin', old.get('origin', ''))),
                     'username': str(data.get('username', old.get('username', '')))[:120],
                     'password': data.get('password') or old.get('password', ''),
                     'enabled': bool(data.get('enabled', False))}
            if value['origin'] != old.get('origin'):
                with self.lock:
                    if self.db.execute('SELECT 1 FROM provider_accounts LIMIT 1').fetchone():
                        raise BookError('Disconnect managed audiobook accounts before changing servers.', 409)
            if not value['password']: raise BookError('Enter the audiobook server administrator credentials.', 400)
            # Nothing durable or visible changes until the candidate is verified.
            # A timeout or process exit must leave the previous connection intact.
            value = self.login(value)
            if value['type'] not in ('root', 'admin'):
                raise BookError('The server connection requires an administrator account.', 400)
            libraries = self.call('GET', '/api/libraries', token=value['access'], settings=value).get('libraries', [])
            choices = [{'id': identifier(x['id']), 'name': str(x.get('name', 'Audiobooks'))[:120]}
                       for x in libraries if x.get('mediaType') == 'book']
            selected = data.get('libraries', old.get('libraries') or [x['id'] for x in choices])
            if (not isinstance(selected, list) or not selected or len(selected) > 20
                    or not all(isinstance(x, str) for x in selected)
                    or not set(selected) <= {x['id'] for x in choices}):
                raise BookError('Choose at least one audiobook library.', 400)
            self.save_config({**value, 'libraries': selected, 'choices': choices})
            return {**self.public_config(), 'choices': choices}

    def saved_user(self, uid):
        with self.lock: row = self.db.execute('SELECT payload FROM provider_accounts WHERE arrify_id=?', (uid,)).fetchone()
        if not row: return None
        with self.lock:
            cached = self.cached_users.get(uid)
            if cached is None or cached[0] != row[0]:
                cached = row[0], self.vault.open(uid, row[0]); self.cached_users[uid] = cached
            self.cached_users.move_to_end(uid)
            while len(self.cached_users) > 256: self.cached_users.popitem(last=False)
            return dict(cached[1])

    def save_user(self, uid, value):
        sealed = self.vault.seal(uid, value)
        with self.lock:
            self.db.execute('INSERT INTO provider_accounts VALUES(?,?) ON CONFLICT(arrify_id) DO UPDATE SET payload=excluded.payload', (uid, sealed))

    def ensure_user(self, uid):
        self.require()
        self.require_account(uid)
        value = self.saved_user(uid)
        if not value:
            value = {'username': 'arrify_' + secrets.token_hex(16), 'password': secrets.token_urlsafe(40), 'pending': True}
            self.save_user(uid, value)
        settings = self.config()
        permissions = {'download': True, 'update': False, 'delete': False, 'upload': False,
                       'accessAllLibraries': False, 'accessAllTags': True, 'accessExplicitContent': True}
        if value.get('pending'):
            # A lost creation response is recovered by the durable random username.
            # Logging in with our saved password proves ownership of that account.
            users = self.admin('GET', '/api/users').get('users', [])
            found = next((x for x in users if x.get('username') == value['username']), None)
            if not found:
                created = self.admin('POST', '/api/users', {'username': value['username'], 'password': value['password'],
                    'type': 'user', 'isActive': True, 'permissions': permissions, 'librariesAccessible': settings['libraries']})
                found = created.get('user', created)
            value = self.login(value)
            if value['type'] != 'user' or (found.get('id') and value['provider_id'] != found['id']):
                raise BookError('The audiobook account could not be prepared.')
            value.update(pending=False, libraries=settings['libraries'])
            self.require_account(uid)
            self.save_user(uid, value)
        if value.get('libraries') != settings['libraries']:
            self.admin('PATCH', '/api/users/' + identifier(value['provider_id']),
                       {'permissions': permissions, 'librariesAccessible': settings['libraries']})
            value['libraries'] = settings['libraries']; self.save_user(uid, value)
        return value

    def require_account(self, uid):
        with self.a.lock:
            user = self.a.db.execute('SELECT approved,frozen,deleted_at FROM users WHERE id=?', (uid,)).fetchone()
        if not user or not user['approved'] or user['frozen'] or user['deleted_at']:
            raise BookError('This Arrify account is no longer available.', 403)

    def user(self, uid, method, path, data=None, headers=None, raw=False):
        with self.users[uid % len(self.users)]:
            value = self.ensure_user(uid)
            return self.authenticated(value, lambda v: self.save_user(uid, v), method, path, data, headers, raw)

    def cleanup_deleted(self):
        with self.lock: rows = self.db.execute('SELECT arrify_id FROM provider_accounts LIMIT 5000').fetchall()
        for row in rows:
            uid = row[0]
            with self.a.lock:
                account = self.a.db.execute('SELECT deleted_at FROM users WHERE id=?', (uid,)).fetchone()
            if account and not account['deleted_at']: continue
            with self.users[uid % len(self.users)]:
                value = self.saved_user(uid)
                if value and value.get('pending') and not value.get('provider_id'):
                    found = next((x for x in self.admin('GET', '/api/users').get('users', [])
                                  if x.get('username') == value['username']), None)
                    if found:
                        verified = self.login(value)
                        if verified['provider_id'] != found['id'] or verified['type'] != 'user':
                            raise BookError('A managed audiobook account needs administrator attention.')
                        value = verified
                if value and value.get('provider_id'):
                    try: self.admin('DELETE', '/api/users/' + identifier(value['provider_id']))
                    except BookError as exc:
                        if exc.status != 404: raise
                with self.lock:
                    self.db.execute('DELETE FROM provider_accounts WHERE arrify_id=?', (uid,))
                    self.cached_users.pop(uid, None)

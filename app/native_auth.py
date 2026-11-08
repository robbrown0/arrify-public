"""First-party native sessions. This is not an OAuth authorization server."""
import re
import secrets
import time

ACCESS_SECONDS = 900
REFRESH_SECONDS = 30 * 86400


class NativeError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def migrate(db):
    # Additive schema: old servers ignore these tables. Session deletion is the
    # existing account-wide revocation boundary, including freeze and recovery.
    db.executescript('''
        CREATE TABLE IF NOT EXISTS native_families(
          id TEXT PRIMARY KEY, anchor TEXT UNIQUE NOT NULL REFERENCES sessions(digest) ON DELETE CASCADE,
          user_id INTEGER NOT NULL REFERENCES users(id), device_name TEXT NOT NULL,
          created INTEGER NOT NULL, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS native_refresh(
          digest TEXT PRIMARY KEY, family TEXT NOT NULL REFERENCES native_families(id) ON DELETE CASCADE,
          used INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS native_access(
          digest TEXT PRIMARY KEY REFERENCES sessions(digest) ON DELETE CASCADE,
          family TEXT NOT NULL REFERENCES native_families(id) ON DELETE CASCADE);
        CREATE INDEX IF NOT EXISTS native_refresh_family ON native_refresh(family);
        CREATE INDEX IF NOT EXISTS native_access_family ON native_access(family);
        CREATE TRIGGER IF NOT EXISTS native_family_revoke BEFORE DELETE ON native_families
          BEGIN DELETE FROM sessions WHERE digest IN
            (SELECT digest FROM native_access WHERE family=OLD.id); END;
        CREATE TABLE IF NOT EXISTS server_identity(id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
    ''')
    db.execute('INSERT OR IGNORE INTO server_identity VALUES(1,?)', (secrets.token_hex(16),))


class NativeSessions:
    def __init__(self, accounts):
        self.a = accounts

    def capabilities(self):
        with self.a.lock:
            identity = self.a.db.execute('SELECT value FROM server_identity WHERE id=1').fetchone()[0]
        return {'server_id': identity, 'api_version': '1', 'native_auth_version': 1,
                'access_token_seconds': ACCESS_SECONDS, 'refresh_token_seconds': REFRESH_SECONDS,
                'features': ['rotating_refresh', 'session_revocation']}

    def issue(self, family, uid, expires):
        now = int(time.time())
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        lifetime = min(ACCESS_SECONDS, expires - now)
        self.a.db.execute('INSERT INTO sessions VALUES(?,?,?,?)',
                          (self.a.digest(access), uid, now + lifetime, 'native'))
        self.a.db.execute('INSERT INTO native_access VALUES(?,?)', (self.a.digest(access), family))
        self.a.db.execute('INSERT INTO native_refresh(digest,family) VALUES(?,?)',
                          (self.a.digest(refresh), family))
        return {'access_token': access, 'refresh_token': refresh, 'token_type': 'Bearer',
                'expires_in': lifetime, 'refresh_expires_in': expires - now, 'session_id': family}

    def login(self, data):
        name = data.get('device_name')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(ord(c) < 32 for c in name):
            raise NativeError(400, 'invalid_request', 'Provide a device name of 1–80 characters.')
        # Password work precedes the short issuance transaction. Login already
        # rechecks approval/password under the account transaction.
        anchor = self.a.login(data.get('username'), data.get('password'), 'native-refresh')
        digest = self.a.digest(anchor)
        with self.a.transaction():
            user = self.a.identity(anchor, 'native-refresh')
            now = int(time.time()); expires = now + REFRESH_SECONDS
            self.a.db.execute('UPDATE sessions SET expires=? WHERE digest=?', (expires, digest))
            family = secrets.token_hex(16)
            self.a.db.execute('INSERT INTO native_families VALUES(?,?,?,?,?,?)',
                              (family, digest, user['id'], name.strip(), now, expires))
            return self.issue(family, user['id'], expires)

    def refresh(self, token):
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            raise NativeError(401, 'invalid_refresh', 'Sign in again.')
        result = None
        with self.a.transaction():
            row = self.a.db.execute('''SELECT f.*,r.used,u.approved,u.frozen,u.deleted_at
                FROM native_refresh r JOIN native_families f ON f.id=r.family
                JOIN users u ON u.id=f.user_id WHERE r.digest=?''', (self.a.digest(token),)).fetchone()
            if row:
                if row['used'] or row['expires'] <= int(time.time()) or not row['approved'] or row['frozen'] or row['deleted_at']:
                    # Commit replay revocation before raising outside transaction.
                    self.a.db.execute('DELETE FROM sessions WHERE digest=?', (row['anchor'],))
                else:
                    self.a.db.execute('UPDATE native_refresh SET used=1 WHERE digest=?', (self.a.digest(token),))
                    self.a.db.execute('DELETE FROM sessions WHERE expires<=? AND kind=?', (int(time.time()), 'native'))
                    result = self.issue(row['id'], row['user_id'], row['expires'])
        if result is None:
            raise NativeError(401, 'invalid_refresh', 'Sign in again.')
        return result

    def sessions(self, uid):
        with self.a.lock:
            return {'items': [dict(row) for row in self.a.db.execute(
                'SELECT id,device_name,created,expires FROM native_families WHERE user_id=? AND expires>? ORDER BY created,id',
                (uid, int(time.time())))]}

    def revoke(self, uid, session_id):
        if not isinstance(session_id, str):
            raise NativeError(400, 'invalid_request', 'Provide a session ID.')
        with self.a.transaction():
            self.a.db.execute('DELETE FROM sessions WHERE digest IN (SELECT anchor FROM native_families WHERE id=? AND user_id=?)',
                              (session_id, uid))
        return {'ok': True}

"""Arrify account identity and authentication storage."""
import contextlib
import fcntl
import json
import os
import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

class Accounts:
    password_slots=threading.BoundedSemaphore(2)
    def __init__(self, directory):
        self.directory = Path(directory)
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(Path(directory) / 'accounts.db'), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          PRAGMA journal_mode=WAL;
          PRAGMA foreign_keys=ON;
          CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL, approved INTEGER NOT NULL DEFAULT 0, admin INTEGER NOT NULL DEFAULT 0,
            created INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS sessions(digest TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id),
            expires INTEGER NOT NULL, kind TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS favorites(user_id INTEGER, track_id INTEGER, PRIMARY KEY(user_id,track_id));
          CREATE TABLE IF NOT EXISTS personal_playlists(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
            name TEXT NOT NULL, tracks TEXT NOT NULL DEFAULT '[]');
          CREATE TABLE IF NOT EXISTS player(user_id INTEGER PRIMARY KEY, state TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS listening_status(user_id INTEGER PRIMARY KEY, status TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
            track_id INTEGER NOT NULL, played_at INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS music_requests(user_id INTEGER NOT NULL, request_id TEXT NOT NULL,
            created INTEGER NOT NULL, PRIMARY KEY(user_id,request_id));
          CREATE TABLE IF NOT EXISTS station_preferences(user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            items TEXT NOT NULL DEFAULT '[]', revision INTEGER NOT NULL DEFAULT 0);
          CREATE TABLE IF NOT EXISTS search_history(user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            query_key TEXT NOT NULL, query TEXT NOT NULL, searched_at INTEGER NOT NULL, PRIMARY KEY(user_id,query_key));
          CREATE TABLE IF NOT EXISTS preferences(user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            text_size TEXT NOT NULL DEFAULT 'standard');
          CREATE TABLE IF NOT EXISTS profiles(user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            display_name TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '',
            avatar_version INTEGER NOT NULL DEFAULT 1, avatar_data BLOB,
            avatar_mime TEXT NOT NULL DEFAULT '', avatar_checked INTEGER NOT NULL DEFAULT 0,
            email_verified_at INTEGER NOT NULL DEFAULT 0);
        ''')
        if 'playlist_shared_default' not in {r[1] for r in self.db.execute('PRAGMA table_info(preferences)')}:
            self.db.execute('ALTER TABLE preferences ADD COLUMN playlist_shared_default INTEGER NOT NULL DEFAULT 0')
        if 'visibility' not in {r[1] for r in self.db.execute('PRAGMA table_info(personal_playlists)')}:
            self.db.execute("ALTER TABLE personal_playlists ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'")
        columns={r[1] for r in self.db.execute('PRAGMA table_info(users)')}
        for field in ('frozen','deleted_at','last_login','last_seen'):
            if field not in columns:self.db.execute('ALTER TABLE users ADD COLUMN '+field+' INTEGER NOT NULL DEFAULT 0')
        if 'metadata' not in {r[1] for r in self.db.execute('PRAGMA table_info(music_requests)')}:
            self.db.execute("ALTER TABLE music_requests ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
        self.db.executescript('''CREATE TABLE IF NOT EXISTS admin_audit(id INTEGER PRIMARY KEY,actor_id INTEGER NOT NULL,target_id INTEGER NOT NULL,action TEXT NOT NULL,created INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS history_by_user ON history(user_id,played_at);
            CREATE INDEX IF NOT EXISTS playlists_by_user ON personal_playlists(user_id);
            CREATE INDEX IF NOT EXISTS requests_by_date ON music_requests(created);''')
        if 'email_verified_at' not in {r[1] for r in self.db.execute('PRAGMA table_info(profiles)')}:
            self.db.execute('ALTER TABLE profiles ADD COLUMN email_verified_at INTEGER NOT NULL DEFAULT 0')
        # Keep legacy duplicate emails intact; all new writes enforce uniqueness.
        duplicates=self.db.execute("SELECT 1 FROM profiles WHERE trim(email)<>'' GROUP BY lower(trim(email)) HAVING count(*)>1 LIMIT 1").fetchone()
        if not duplicates:self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS profiles_unique_email ON profiles(lower(trim(email))) WHERE trim(email)<>''")
        from .native_auth import migrate
        migrate(self.db)
        self.attempts = {}
        # Equal work for nonexistent usernames.
        self.dummy = self.hash_password(secrets.token_urlsafe(24))

    @staticmethod
    def hash_password(password, salt=None):
        # OWASP-listed 16 MiB setting; keep existing salt:digest hashes valid.
        # Version the salt prefix so verification never guesses work factors.
        salt = salt or 'scrypt5$'+secrets.token_hex(16)
        modern=salt.startswith('scrypt5$')
        raw_salt=salt[8:] if modern else salt
        if not Accounts.password_slots.acquire(blocking=False):
            raise ValueError('Sign-in is busy. Try again in a moment.')
        try:
            value=hashlib.scrypt(password.encode(),salt=raw_salt.encode(),n=16384,r=8,p=5 if modern else 1).hex()
            if not modern:
                # Equalize legacy-user and nonexistent-user verification work.
                hashlib.scrypt(password.encode(),salt=raw_salt.encode(),n=16384,r=8,p=4)
            return salt+':'+value
        finally:Accounts.password_slots.release()

    def rate_limit(self, key, limit=12, window=900):
        now = time.monotonic()
        with self.lock:
            # Each bucket owns its expiry. A 60-second search check must not
            # erase 15-minute sign-in or one-hour party-request limits.
            self.attempts = {k: v for k, v in self.attempts.items() if v['expires'] > now}
            bucket = self.attempts.get(key, {'hits': []})
            hits = [t for t in bucket['hits'] if t > now-window]
            if len(hits) >= limit:
                raise ValueError('Too many attempts. Try again later.')
            self.attempts[key] = {'hits': hits + [now], 'expires': now+window}

    @contextlib.contextmanager
    def transaction(self):
        with self.lock:
            outer=self.db.in_transaction
            if not outer:self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                if not outer:self.db.commit()
            except Exception:
                if not outer:self.db.rollback()
                raise

    @staticmethod
    def normalize_email(email):
        if not isinstance(email,str):raise ValueError('Enter your email address.')
        email=email.strip().lower()
        if not email or len(email)>254 or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+',email) or any(ord(c)<32 or ord(c)==127 for c in email):
            raise ValueError('Enter a valid email address.')
        return email

    def check_email_available(self,email,user_id=0):
        if self.db.execute('SELECT 1 FROM profiles WHERE lower(trim(email))=? AND user_id<>? LIMIT 1',(email,user_id)).fetchone():
            raise ValueError('That email is already linked to an account.')

    def register(self, username, password, email=None):
        username = str(username).strip().lower()
        if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{2,39}', username):
            raise ValueError('Use a username of 3–40 letters, numbers, dots, dashes or underscores.')
        if not isinstance(password, str) or not 12 <= len(password) <= 128:
            raise ValueError('Use a password of 12–128 characters.')
        email=self.normalize_email(email)
        encoded = self.hash_password(password)
        try:
            with self.transaction():
                self.check_email_available(email)
                uid=self.db.execute('INSERT INTO users(username,password,created) VALUES(?,?,?)',(username,encoded,int(time.time()))).lastrowid
                self.update_profile(uid,'',email)
        except sqlite3.IntegrityError:
            raise ValueError('That username or email is already in use.') from None
        return {'message': 'Account created. An administrator must approve it before you can listen.'}

    def login(self, username, password, kind='web'):
        if not isinstance(password, str) or len(password) > 128:
            raise ValueError('Invalid credentials.')
        with self.lock:
            user = self.db.execute('SELECT * FROM users WHERE deleted_at=0 AND username=?', (str(username).strip().lower(),)).fetchone()
        stored = user['password'] if user else self.dummy
        valid = hmac.compare_digest(self.hash_password(password, stored.split(':')[0]), stored)
        if not user or not valid:
            raise ValueError('Invalid credentials.')
        if user['frozen']:raise PermissionError('This account is frozen. Contact the administrator.')
        if not user['approved']:
            raise PermissionError('Your account is awaiting administrator approval.')
        upgraded=self.hash_password(password) if not stored.startswith('scrypt5$') else None
        token = secrets.token_urlsafe(32)
        with self.transaction():
            current=self.db.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone()
            if not current or current['deleted_at'] or current['frozen'] or not current['approved'] or not hmac.compare_digest(current['password'],stored):
                raise PermissionError('Account access changed. Contact the administrator.')
            if upgraded:self.db.execute('UPDATE users SET password=? WHERE id=?',(upgraded,user['id']))
            self.db.execute('UPDATE users SET last_login=?,last_seen=? WHERE id=?',(int(time.time()),int(time.time()),user['id']))
            self.db.execute('DELETE FROM sessions WHERE expires<?', (int(time.time()),))
            self.db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (self.digest(token), user['id'], int(time.time()) + 7*86400, kind))
        return token

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def csrf(token):
        return hashlib.sha256(('arrify-csrf:' + token).encode()).hexdigest()

    def identity(self, token, kind):
        with self.lock:
            user = self.db.execute('''SELECT u.id,u.username,u.admin,u.last_seen _last_seen,
              coalesce(nullif(p.display_name,''),u.username) display_name,
              coalesce(pref.text_size,'standard') text_size,coalesce(pref.playlist_shared_default,0) playlist_shared_default,
              CASE WHEN coalesce(p.email,'')='' THEN 1 ELSE 0 END needs_email,
              '/api/v1/avatar/'||u.id||'?v='||coalesce(p.avatar_version,0) avatar_url
              FROM sessions s JOIN users u ON u.id=s.user_id LEFT JOIN profiles p ON p.user_id=u.id LEFT JOIN preferences pref ON pref.user_id=u.id
              WHERE s.digest=? AND s.expires>? AND s.kind=? AND u.approved=1 AND u.frozen=0 AND u.deleted_at=0''', (self.digest(token), int(time.time()), kind)).fetchone()
        if not user:
            raise PermissionError('Sign in to Arrify.')
        result=dict(user);last=result.pop('_last_seen',0)
        if int(time.time())-last>=60:
            with self.lock:self.db.execute('UPDATE users SET last_seen=? WHERE id=?',(int(time.time()),result['id']))
        return result

    def profile(self,user_id):
        with self.lock:
            row=self.db.execute('''SELECT u.id,u.username,coalesce(nullif(p.display_name,''),u.username) display_name,
                coalesce(p.email,'') email,coalesce(p.email_verified_at,0)>0 email_verified,coalesce(p.avatar_version,0) avatar_version,coalesce(pref.text_size,'standard') text_size,coalesce(pref.playlist_shared_default,0) playlist_shared_default
                FROM users u LEFT JOIN profiles p ON p.user_id=u.id LEFT JOIN preferences pref ON pref.user_id=u.id WHERE u.id=?''',(user_id,)).fetchone()
        if not row:raise ValueError('Account not found.')
        result=dict(row)
        result['avatar_url']=f"/api/v1/avatar/{user_id}?v={result['avatar_version']}"
        return result

    def playlist_shared_default(self,user_id):
        with self.lock:
            row=self.db.execute('SELECT playlist_shared_default FROM preferences WHERE user_id=?',(user_id,)).fetchone()
            return bool(row and row[0])

    def set_playlist_shared_default(self,user_id,value):
        if not isinstance(value,bool):raise ValueError('Choose whether new playlists should be shared.')
        with self.transaction():
            self.db.execute('INSERT INTO preferences(user_id,playlist_shared_default) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET playlist_shared_default=excluded.playlist_shared_default',(user_id,int(value)))
        return {'playlist_shared_default':value}

    def update_profile(self,user_id,display_name,email,text_size=None):
        if not isinstance(display_name,str):raise ValueError('Invalid profile.')
        if text_size is not None and text_size not in ('standard','large','largest'):
            raise ValueError('Choose Standard, Larger, or Largest text.')
        display_name=display_name.strip();email=self.normalize_email(email)
        if len(display_name)>80 or any(ord(c)<32 or ord(c)==127 for c in display_name):raise ValueError('Use a display name of up to 80 characters.')
        with self.transaction():
            self.check_email_available(email,user_id)
            self.db.execute('''INSERT INTO profiles(user_id,display_name,email) VALUES(?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET display_name=excluded.display_name,email=excluded.email,
                email_verified_at=CASE WHEN profiles.email=excluded.email THEN profiles.email_verified_at ELSE 0 END,
                avatar_version=profiles.avatar_version+1,
                avatar_data=CASE WHEN profiles.email=excluded.email THEN profiles.avatar_data ELSE NULL END,
                avatar_mime=CASE WHEN profiles.email=excluded.email THEN profiles.avatar_mime ELSE '' END,
                avatar_checked=CASE WHEN profiles.email=excluded.email THEN profiles.avatar_checked ELSE 0 END''',(user_id,display_name,email))
            if text_size is not None:self.db.execute('INSERT INTO preferences(user_id,text_size) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET text_size=excluded.text_size',(user_id,text_size))
        return self.profile(user_id)

    def admin_users(self):
        with self.lock:
            rows=self.db.execute("""SELECT u.id,u.username,u.approved,u.admin,u.created,u.frozen,u.last_login,u.last_seen,
              coalesce(nullif(p.display_name,''),u.username) display_name,coalesce(p.email,'') email,coalesce(p.email_verified_at,0)>0 email_verified,
              (SELECT count(*) FROM history WHERE user_id=u.id) tracks_started,
              (SELECT count(*) FROM personal_playlists WHERE user_id=u.id) playlists,
              (SELECT count(*) FROM favorites WHERE user_id=u.id) saved_songs,
              (SELECT count(*) FROM music_requests WHERE user_id=u.id) requests
              FROM users u LEFT JOIN profiles p ON p.user_id=u.id WHERE u.deleted_at=0 ORDER BY u.approved ASC,u.frozen DESC,u.created DESC,u.id DESC""").fetchall()
        items=[dict(r) for r in rows]
        return {'items':items,'pending':sum(not u['approved'] and not u['frozen'] for u in items)}

    def pending_count(self):
        with self.lock:return self.db.execute('SELECT count(*) FROM users WHERE approved=0 AND frozen=0 AND deleted_at=0').fetchone()[0]

    def admin_overview(self):
        with self.lock:
            counts=self.db.execute('SELECT count(*),coalesce(sum(approved=1 AND frozen=0),0),coalesce(sum(approved=0 AND frozen=0),0),coalesce(sum(frozen=1),0) FROM users WHERE deleted_at=0').fetchone()
            requests=self.db.execute('SELECT count(*) FROM music_requests').fetchone()[0]
        return dict(zip(('accounts','approved','pending','frozen'),counts)) | {'requests':requests}

    def manage_user(self,actor_id,data):
        action=data.get('action')
        if action not in ('approve','freeze','unfreeze','delete'):raise ValueError('Unknown account action.')
        with self.recovery_lock(),self.transaction():
            actor=self.db.execute('SELECT * FROM users WHERE id=?',(actor_id,)).fetchone()
            if not actor or not actor['admin'] or not actor['approved'] or actor['frozen'] or actor['deleted_at']:raise PermissionError('Administrator access required.')
            target=self.db.execute('SELECT * FROM users WHERE id=? AND deleted_at=0',(int(data.get('id',0)),)).fetchone()
            if not target:raise ValueError('Account not found.')
            uid=target['id']
            if action in ('freeze','delete'):
                if uid==actor_id:raise ValueError('You cannot freeze or delete the account you are using.')
                active=self.db.execute('SELECT count(*) FROM users WHERE admin=1 AND approved=1 AND frozen=0 AND deleted_at=0').fetchone()[0]
                if target['admin'] and target['approved'] and not target['frozen'] and active<=1:raise ValueError('Keep at least one active administrator.')
            if action=='delete':
                if data.get('confirm_username')!=target['username']:raise ValueError('Type the account username to confirm deletion.')
                # Keep a non-login tombstone to prevent user-ID reuse and preserve
                # existing acquisition request references without retaining PII.
                if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='parties'").fetchone():
                    if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='party_session_ids'").fetchone():self.db.execute("UPDATE party_session_ids SET state='deleted',updated=? WHERE owner=?",(int(time.time()),uid))
                    for table in ('party_entries','party_acquisitions','party_guests'):
                        self.db.execute('DELETE FROM '+table+' WHERE party_id IN (SELECT id FROM parties WHERE owner=?)',(uid,))
                    if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='guest_wifi_profiles'").fetchone():self.db.execute("DELETE FROM guest_wifi_profiles WHERE scope IN (SELECT 'party:' || id FROM parties WHERE owner=?)",(uid,))
                    if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='party_wifi'").fetchone():self.db.execute('DELETE FROM party_wifi WHERE party_id IN (SELECT id FROM parties WHERE owner=?)',(uid,))
                    self.db.execute('DELETE FROM parties WHERE owner=?',(uid,))
                if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='guest_wifi_profiles'").fetchone():self.db.execute("DELETE FROM guest_wifi_profiles WHERE scope=?",('user:'+str(uid),))
                for table in ('apple_music_links','apple_party_playback','apple_music_history','spotify_links','spotify_party_playback','spotify_music_history'):
                    if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():self.db.execute('DELETE FROM '+table+' WHERE user_id=?',(uid,))
                for table in ('sessions','favorites','personal_playlists','player','listening_status','history','search_history','preferences','profiles'):
                    self.db.execute('DELETE FROM '+table+' WHERE user_id=?',(uid,))
                for table in ('request_progress','request_events','request_notifications'):
                    if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():self.db.execute('DELETE FROM '+table+' WHERE user_id=?',(uid,))
                if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sound_preferences'").fetchone():self.db.execute('DELETE FROM sound_preferences WHERE user_id=?',(uid,))
                (self.directory/'playback-diagnostics'/f'{uid}.json').unlink(missing_ok=True)
                self.db.execute('UPDATE users SET username=?,password=?,approved=0,admin=0,frozen=1,deleted_at=?,last_login=0,last_seen=0 WHERE id=?',('$deleted$'+str(uid)+'$'+secrets.token_hex(16),'!deleted',int(time.time()),uid))
            elif action=='approve':
                if target['frozen']:raise ValueError('Unfreeze this account before approving it.')
                self.db.execute('UPDATE users SET approved=1 WHERE id=?',(uid,))
            else:
                self.db.execute('UPDATE users SET frozen=? WHERE id=?',(int(action=='freeze'),uid))
                if action=='freeze':self.db.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
            if action in ('freeze','delete'):
                grant=self.directory/'recovery.json'
                try:matches=json.loads(grant.read_text()).get('username')==target['username']
                except (OSError,ValueError):matches=False
                if matches:grant.unlink(missing_ok=True)
            self.db.execute('INSERT INTO admin_audit(actor_id,target_id,action,created) VALUES(?,?,?,?)',(actor_id,uid,action,int(time.time())))
        return {'ok':True}

    def refresh_avatar(self,user_id):
        with self.lock:
            self.db.execute('UPDATE profiles SET avatar_data=NULL,avatar_mime=\'\',avatar_checked=0,avatar_version=avatar_version+1 WHERE user_id=?',(user_id,))
        return self.profile(user_id)

    def approve(self, username, admin=False):
        with self.lock:
            result = self.db.execute('UPDATE users SET approved=1,admin=max(admin,?) WHERE username=?', (int(admin), username))
            if not result.rowcount:
                raise ValueError('Account not found. Register it first.')

    def needs_setup(self):
        with self.lock:
            return not self.db.execute('SELECT 1 FROM users WHERE admin=1 LIMIT 1').fetchone()

    def bootstrap(self, username, password, email=None):
        """Explicit LAN-only owner setup; ordinary signup never self-promotes."""
        if not isinstance(username,str) or not isinstance(password,str) or not 12<=len(password)<=128:
            raise ValueError('Choose a username and a password of 12–128 characters.')
        username=username.strip().lower()
        email=self.normalize_email(email)
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                if not self.needs_setup():raise PermissionError('Initial setup is already complete.')
                existing=self.db.execute('SELECT password FROM users WHERE username=?',(username,)).fetchone()
                if existing:
                    if not hmac.compare_digest(self.hash_password(password,existing['password'].split(':')[0]),existing['password']):
                        raise ValueError('That username is unavailable.')
                    row=self.db.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone()
                    self.update_profile(row['id'],self.profile(row['id'])['display_name'],email)
                else:self.register(username,password,email)
                self.approve(username,admin=True)
                self.db.commit()
            except Exception:
                self.db.rollback();raise
        return {'message':'Administrator account created. Sign in to continue.'}

    @contextlib.contextmanager
    def recovery_lock(self):
        # Coordinate the operator command and HTTP process without exposing a token.
        with self.lock:
            fd=os.open(self.directory/'recovery.lock',os.O_CREAT|os.O_RDWR,0o600)
            with os.fdopen(fd,'a+') as handle:
                fcntl.flock(handle,fcntl.LOCK_EX)
                try:yield
                finally:fcntl.flock(handle,fcntl.LOCK_UN)

    def recovery_grant(self):
        try:
            grant=json.loads((self.directory/'recovery.json').read_text())
            if float(grant['expires'])>time.time():return grant
        except (OSError,ValueError,KeyError,TypeError):pass
        raise PermissionError('This recovery window has closed. Ask the server owner to reopen it.')

    def save_recovery_grant(self,grant):
        filename=self.directory/'recovery.json'
        temporary=self.directory/('recovery-'+secrets.token_hex(8)+'.tmp')
        fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        try:
            with os.fdopen(fd,'w') as handle:json.dump(grant,handle)
            os.replace(temporary,filename)
        finally:temporary.unlink(missing_ok=True)

    def enable_recovery(self,username,seconds=600):
        username=str(username).strip().lower()
        with self.recovery_lock():
            if not self.db.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone():
                raise ValueError('Account not found.')
            self.save_recovery_grant({'username':username,'expires':time.time()+min(600,max(1,seconds))})

    def claim_recovery(self):
        # Only the HTTP handler's actual LAN-peer gate may call this method.
        with self.recovery_lock():
            grant=self.recovery_grant()
            token=secrets.token_urlsafe(32)
            grant['digest']=self.digest(token)
            self.save_recovery_grant(grant)
            return token

    def recovery_identity(self,token):
        if not isinstance(token,str) or not 20<=len(token)<=128:
            raise PermissionError('This reset link is invalid or has expired.')
        grant=self.recovery_grant()
        if not hmac.compare_digest(grant.get('digest',''),self.digest(token)):
            raise PermissionError('This reset link is invalid or has expired.')
        return grant

    def check_recovery(self,token):
        with self.recovery_lock():return {'username':self.recovery_identity(token)['username']}

    def finish_recovery(self,token,password):
        if not isinstance(password,str) or not 12<=len(password)<=128:
            raise ValueError('Use a password of 12–128 characters.')
        with self.recovery_lock(),self.lock:
            grant=self.recovery_identity(token)
            encoded=self.hash_password(password)
            self.db.execute('BEGIN IMMEDIATE')
            try:
                row=self.db.execute('SELECT id FROM users WHERE username=?',(grant['username'],)).fetchone()
                if not row:raise PermissionError('This reset link is invalid or has expired.')
                self.db.execute('UPDATE users SET password=? WHERE id=?',(encoded,row['id']))
                self.db.execute('DELETE FROM sessions WHERE user_id=?',(row['id'],))
                # An interrupted reset must fail closed, never leave a reusable grant.
                (self.directory/'recovery.json').unlink()
                self.db.commit()
            except Exception:
                self.db.rollback();raise
        return {'message':'Password saved. Sign in with the new one.'}

    def rename_account(self,username,new_username):
        """Operator account rename; preserve its ID, roles, and related records."""
        username=str(username).strip().lower();new_username=str(new_username).strip().lower()
        if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{2,39}',new_username):
            raise ValueError('Use a username of 3–40 letters, numbers, dots, dashes or underscores.')
        with self.recovery_lock():
            self.db.execute('BEGIN IMMEDIATE')
            try:
                row=self.db.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone()
                if not row:raise ValueError('Account not found.')
                if new_username!=username and self.db.execute('SELECT 1 FROM users WHERE username=?',(new_username,)).fetchone():
                    raise ValueError('That username is unavailable.')
                self.db.execute('UPDATE users SET username=? WHERE id=?',(new_username,row['id']))
                self.db.execute('DELETE FROM sessions WHERE user_id=?',(row['id'],))
                (self.directory/'recovery.json').unlink(missing_ok=True)
                self.db.commit()
            except Exception:
                self.db.rollback();raise

    def recent_searches(self,user_id):
        with self.lock:
            rows=self.db.execute('SELECT query FROM search_history WHERE user_id=? ORDER BY searched_at DESC,rowid DESC LIMIT 20',(user_id,)).fetchall()
        return {'items':[r['query'] for r in rows]}

    def edit_search_history(self,user_id,data):
        action=data.get('action')
        if action not in ('remember','remove','clear'):raise ValueError('Invalid search history action.')
        query=data.get('query','')
        if not isinstance(query,str):raise ValueError('Invalid search.')
        query=' '.join(query.split())
        if action!='clear' and (not query or len(query)>200 or any(ord(c)<32 or ord(c)==127 for c in query)):
            raise ValueError('Use a search of 1–200 characters.')
        key=query.casefold()
        with self.transaction():
            if action=='remember':
                self.db.execute('INSERT INTO search_history VALUES(?,?,?,?) ON CONFLICT(user_id,query_key) DO UPDATE SET query=excluded.query,searched_at=excluded.searched_at',(user_id,key,query,time.time_ns()))
                self.db.execute('DELETE FROM search_history WHERE user_id=? AND rowid NOT IN (SELECT rowid FROM search_history WHERE user_id=? ORDER BY searched_at DESC,rowid DESC LIMIT 20)',(user_id,user_id))
            elif action=='remove':self.db.execute('DELETE FROM search_history WHERE user_id=? AND query_key=?',(user_id,key))
            else:self.db.execute('DELETE FROM search_history WHERE user_id=?',(user_id,))
        return self.recent_searches(user_id)

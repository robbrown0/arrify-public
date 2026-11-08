import json
import sqlite3
from datetime import datetime, timezone

def now():
    return datetime.now(timezone.utc).isoformat()

class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path,timeout=60)
        self.db.execute("PRAGMA busy_timeout=60000")
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, fetched TEXT, payload TEXT);
        CREATE TABLE IF NOT EXISTS inventory(kind TEXT, id TEXT, fingerprint TEXT, payload TEXT, seen TEXT, PRIMARY KEY(kind,id));
        CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, size INTEGER, mtime INTEGER, payload TEXT, seen TEXT);
        CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, started TEXT, finished TEXT, status TEXT, report TEXT);
        CREATE TABLE IF NOT EXISTS decisions(rg TEXT PRIMARY KEY, artist TEXT, album TEXT, state TEXT, updated TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS requests(rg TEXT PRIMARY KEY, reserved TEXT, week TEXT, state TEXT, lidarr_id INTEGER, detail TEXT);
        CREATE TABLE IF NOT EXISTS evidence(key TEXT PRIMARY KEY, payload TEXT, seen TEXT);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, time TEXT, event TEXT, payload TEXT);
        ''')
        self.db.commit()
    def cached(self, key, ttl=86400*30):
        r = self.db.execute('SELECT fetched,payload FROM cache WHERE key=?', (key,)).fetchone()
        if r and (datetime.now(timezone.utc)-datetime.fromisoformat(r['fetched'])).total_seconds() < ttl:
            return json.loads(r['payload'])
    def cache(self, key, payload):
        self.db.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?)', (key, now(), json.dumps(payload)))
        self.db.commit()
    def audit(self, event, payload):
        self.db.execute('INSERT INTO audit(time,event,payload) VALUES(?,?,?)', (now(),event,json.dumps(payload)))
        self.db.commit()
    def inventory(self, kind, rows):
        import hashlib
        stamp=now()
        for row in rows:
            text=json.dumps(row, sort_keys=True)
            self.db.execute('INSERT INTO inventory VALUES(?,?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET fingerprint=excluded.fingerprint,payload=excluded.payload,seen=excluded.seen',
                            (kind,str(row['id']),hashlib.sha256(text.encode()).hexdigest(),text,stamp))
        self.db.execute('DELETE FROM inventory WHERE kind=? AND seen<>?', (kind,stamp))
        self.db.commit()
    def decision(self, rg, artist, album, state, detail=''):
        if state!='rejected' and self.db.execute("SELECT 1 FROM decisions WHERE rg=? AND state='rejected'",(rg,)).fetchone(): return
        self.db.execute('INSERT OR REPLACE INTO decisions VALUES(?,?,?,?,?,?)', (rg,artist,album,state,now(),detail))
        self.db.commit()
    def reserve(self, rg, limit):
        week=datetime.now(timezone.utc).strftime('%G-W%V')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            exists=self.db.execute('SELECT 1 FROM requests WHERE rg=?',(rg,)).fetchone()
            count=self.db.execute('SELECT count(*) FROM requests WHERE week=?',(week,)).fetchone()[0]
            if exists or count>=limit:
                self.db.rollback(); return False
            self.db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?)',(rg,now(),week,'reserved',None,''))
            self.db.commit(); return True
        except Exception:
            self.db.rollback(); raise
    def request_state(self, rg, state, lidarr_id=None, detail=''):
        self.db.execute('UPDATE requests SET state=?,lidarr_id=coalesce(?,lidarr_id),detail=? WHERE rg=?',(state,lidarr_id,detail,rg))
        self.db.commit()

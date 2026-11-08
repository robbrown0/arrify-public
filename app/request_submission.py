"""Serialize request quotas without blocking playback's account checks."""
import json
import threading
import time
import hashlib
from .radio import recording_key

class RequestLimit(ValueError):
    pass

class RequestSubmissions:
    def __init__(self,accounts,submit):
        self.accounts=accounts
        self.submit=submit
        self.lock=threading.Lock()

    def browse_requests(self,uid):
        # Shared library: publish song-level request markers, never requester identity.
        with self.accounts.lock:rows=self.accounts.db.execute('SELECT metadata FROM music_requests ORDER BY created DESC').fetchall()
        result={}
        for row in rows:
            try:value=json.loads(row['metadata'])
            except (ValueError,TypeError):continue
            if isinstance(value,dict) and value.get('browse_key'):result.setdefault(value['browse_key'],{**{k:value.get(k,'') for k in ('title','artist','album')},'already_requested':True})
        return result

    def existing_browse(self,uid,key):
        # A listener's own subscription and progress remain private.
        with self.accounts.lock:rows=self.accounts.db.execute('SELECT request_id,metadata FROM music_requests WHERE user_id=? ORDER BY created DESC',(uid,)).fetchall()
        for row in rows:
            try:value=json.loads(row['metadata'])
            except (ValueError,TypeError):continue
            if isinstance(value,dict) and value.get('browse_key')==key:return {'id':row['request_id'],**{k:value.get(k,'') for k in ('title','artist','album')},'already_requested':True}
        return None

    def mark_requested(self,items):
        with self.accounts.lock:rows=self.accounts.db.execute('SELECT request_id,metadata FROM music_requests').fetchall()
        ids={row['request_id'] for row in rows};songs=set()
        for row in rows:
            try:value=json.loads(row['metadata'])
            except (ValueError,TypeError):continue
            if isinstance(value,dict) and value.get('artist') and value.get('title'):songs.add(recording_key(value))
        for item in items:
            selection=item.get('selection','')
            rid=hashlib.sha256(selection.encode()).hexdigest()[:32] if isinstance(selection,str) and selection else None
            item['requested']=bool(item.get('requested') or rid in ids or recording_key(item) in songs)
        return items

    def create(self,uid,selection,cap=0,source=None):
        a=self.accounts
        # Only submissions wait for the companion. Streams, auth and controls
        # must remain free to acquire the shared account/database lock.
        with self.lock:
            if source and source.get('browse_key'):
                existing=self.existing_browse(uid,source['browse_key'])
                if existing:return existing
            with a.lock:
                count=a.db.execute('SELECT count(*) FROM music_requests WHERE user_id=? AND created>?',(uid,int(time.time())-86400)).fetchone()[0]
                if cap and count>=cap:raise RequestLimit(f'You can request up to {cap} selections per day.')
            result=self.submit('/requests',{'selection':selection,'user_id':uid})
            with a.lock:
                a.db.execute('INSERT OR IGNORE INTO music_requests(user_id,request_id,created,metadata) VALUES(?,?,?,?)',(uid,result['id'],int(time.time()),json.dumps({**{k:result.get(k,'') for k in ('title','artist','album')},**({'browse_key':source['browse_key']} if source and source.get('browse_key') else {})})))
                if source and source.get('browse_key'):
                    row=a.db.execute('SELECT metadata FROM music_requests WHERE user_id=? AND request_id=?',(uid,result['id'])).fetchone()
                    previous=json.loads(row[0]) if row else {}
                    a.db.execute('UPDATE music_requests SET metadata=? WHERE user_id=? AND request_id=?',(json.dumps({**previous,'browse_key':source['browse_key']}),uid,result['id']))
            return result

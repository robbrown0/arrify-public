"""Private durable request observations; availability is checked against the player index."""
import json
import math
import re
import threading
import time
import unicodedata
from . import requests as acquisition

STAGES={
 'song-search':('Finding your song','Searching for this song as a single or individual track.',1),
 'downloading':('Downloading','Downloading the album.',2),
 'paused':('Download paused','The downloader paused this release. An alternative may be needed.',2),
 'importing':('Importing','Downloaded; waiting for the album to be imported.',3),
 'import-error':('Import needs attention','The download could not be imported. Recovery or an alternative is needed.',3),
 'queued':('Requested','Your request is in line.',0),
 'resolving':('Finding the album','Checking the recording and its official release.',1),
 'priority-queued':('Finding a release','Your request has priority in the acquisition queue.',1),
 'waiting-for-release':('Checking availability','This song isn’t available right now. We’ll keep checking.',1),
 'reserved':('Preparing the album','Download authorization is reserved for this release.',2),
 'adding-artist':('Preparing your request','We’re preparing the album for your song.',1),
 'adding-album':('Preparing your request','We’re preparing the album for your song.',1),
 'monitoring':('Finding a release','The selected album is being monitored.',1),
 'search-pending':('Finding a release','The album search has been submitted. Waiting for confirmation.',1),
 'import-retry':('Import needs attention','The transfer finished but import needs another attempt.',3),
 'prepared':('Ready to download','A suitable release has been selected.',2),
 'requested':('Acquiring the album','The download search has been requested. Waiting for a suitable transfer and import.',2),
 'downloading':('Downloading','The transfer is in progress.',2),
 'importing':('Importing','The transfer is complete. Adding files to the music library.',3),
 'import-error':('Import needs attention','The transfer finished but import needs administrator attention.',3),
 'paused':('Download paused','The transfer is paused. Your request is saved.',2),
 'downloaded':('Adding to the library','Downloaded. Waiting for import and the library scan.',3),
 'imported-awaiting-library-check':('Checking the library','Imported. Waiting for Arrify to find the playable files.',3),
 'already-owned':('Verifying in library','Already in your music library. Arrify is checking playback availability.',3),
 'partial-owned':('Finding the missing song','Some of this album is available. We’re still looking for your requested song.',1),
 'available':('Ready to play','Your requested song is in the library.',4),
 'needs-review':('Needs review','An administrator needs to check the release match.',1),
 'policy-review':('Needs review','An administrator needs to review this request.',1),
 'awaiting-download-allowance':('On hold','Waiting for download authorization.',2),
 'acquisition-disabled':('On hold','Acquisition is paused. Your request is saved.',1),
 'retrying':('Trying again','A temporary problem occurred. The service will try again.',1),
 'rejected':('Couldn’t fulfill','This request could not be fulfilled. Contact an administrator.',1),
 'status-unavailable':('Status unavailable','The request service is temporarily unreachable. Your request is saved.',0)}

def object_value(value):
    try:
        result=json.loads(value or '{}');return result if isinstance(result,dict) else {}
    except (ValueError,TypeError):return {}

def key(value):return ''.join(c for c in unicodedata.normalize('NFKD',str(value)).casefold() if c.isalnum())

def recording_identity(data):
    # Tags may list collaborators separately or put a featured credit in the
    # title. Keep album and recording-version distinctions for safe matching.
    artist=str(data.get('artist','')).split(';',1)[0]
    title=re.sub(r'\s*[\[(](?:feat\.?|ft\.?|featuring)\s+[^\])]+[\])]','',str(data.get('title','')),flags=re.I)
    album=key(data.get('album',''))
    # Explicit same-release alias; never broadly drop subtitles or version names.
    if key(artist)=='stevemartin' and album in {'thecrownewsongsforthe5stringbanjo','thecrownewsongsforthefivestringbanjo'}:
        album='thecrow'
    return key(artist),album,key(title)

class RequestProgress:
    def __init__(self,accounts,library,fetch=acquisition.call,now=time.time):
        self.a=accounts;self.lib=library;self.fetch=fetch;self.now=now;self.refresh_lock=threading.Lock();self.wake=threading.Event();self.unavailable=False
        with self.a.lock:self.a.db.executescript('''
          CREATE TABLE IF NOT EXISTS request_progress(user_id INTEGER NOT NULL,request_id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(user_id,request_id));
          CREATE TABLE IF NOT EXISTS request_events(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,request_id TEXT NOT NULL,state TEXT NOT NULL,created INTEGER NOT NULL);
          CREATE INDEX IF NOT EXISTS request_event_owner ON request_events(user_id,request_id,id);
          CREATE TABLE IF NOT EXISTS request_notifications(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,request_id TEXT NOT NULL,created INTEGER NOT NULL,read_at INTEGER NOT NULL DEFAULT 0,UNIQUE(user_id,request_id));
          CREATE INDEX IF NOT EXISTS request_notice_owner ON request_notifications(user_id,read_at,id);
        ''')
        with self.a.lock:
            if 'dismissed_at' not in {r['name'] for r in self.a.db.execute('PRAGMA table_info(request_notifications)')}:
                self.a.db.execute('ALTER TABLE request_notifications ADD COLUMN dismissed_at INTEGER NOT NULL DEFAULT 0')
    def refresh(self):
        if not self.refresh_lock.acquire(blocking=False):return
        try:
            with self.a.lock:rows=self.a.db.execute('SELECT r.* FROM music_requests r JOIN users u ON u.id=r.user_id WHERE u.deleted_at=0').fetchall()
            if not rows:return
            with self.lib.index_lock:
                index={}
                for t in self.lib.tracks.values():index.setdefault(recording_identity(t),t)
            states={};failed=False
            ids=list(dict.fromkeys(r['request_id'] for r in rows))
            for start in range(0,len(ids),50):
                try:states.update({x['id']:x for x in self.fetch('/statuses?ids='+','.join(ids[start:start+50]))['items']})
                except (ValueError,KeyError,TypeError):failed=True
            self.unavailable=failed
            matches={};verified={}
            # NAS checks happen outside the accounts transaction and once per file.
            for row in rows:
                identity_data={**object_value(row['metadata']),**states.get(row['request_id'],{})}
                identity=recording_identity(identity_data)
                track=index.get(identity) if all(identity) else None
                if track:
                    if track['id'] not in verified:verified[track['id']]=self.lib.request_track_ready(track['id'])
                    if not verified[track['id']]:track=None
                matches[(row['user_id'],row['request_id'])]=track
            for row in rows:
                uid,rid=row['user_id'],row['request_id']
                with self.a.transaction():
                    if not self.a.db.execute('SELECT 1 FROM users WHERE id=? AND deleted_at=0',(uid,)).fetchone():continue
                    prior=self.a.db.execute('SELECT payload FROM request_progress WHERE user_id=? AND request_id=?',(uid,rid)).fetchone()
                    old=object_value(prior[0]) if prior else {}
                    metadata=object_value(row['metadata']);remote=states.get(rid)
                    data={**metadata,**old,**(remote or {})}
                    if remote is not None:
                        for field in ('phase','progress_percent'):
                            if field not in remote:data.pop(field,None)
                    percent=data.get('progress_percent')
                    if type(percent) not in (int,float) or not math.isfinite(percent) or not 0<=percent<=100:data.pop('progress_percent',None)
                    state=data.get('phase') or data.get('state','queued')
                    # Upstream completion alone does not establish player availability.
                    if state=='available':state='imported-awaiting-library-check'
                    track=matches.get((uid,rid))
                    for field in ('track_id','album_id','artwork'):data.pop(field,None)
                    if track:state='available';data.update(track_id=track['id'],album_id=track['album_id'],artwork=track['artwork'])
                    data.update(state=state,created=row['created'],checked_at=int(self.now()),status_stale=remote is None)
                    if remote is not None:data['last_status_at']=int(self.now())
                    data['updated']=old.get('updated',row['created']) if old.get('state')==state else int(self.now())
                    if not prior:self.a.db.execute('INSERT INTO request_events(user_id,request_id,state,created) VALUES(?,?,?,?)',(uid,rid,'queued',row['created']))
                    if old.get('state')!=state and (prior or state!='queued'):self.a.db.execute('INSERT INTO request_events(user_id,request_id,state,created) VALUES(?,?,?,?)',(uid,rid,state,int(self.now())))
                    self.a.db.execute('INSERT INTO request_progress VALUES(?,?,?) ON CONFLICT(user_id,request_id) DO UPDATE SET payload=excluded.payload',(uid,rid,json.dumps(data)))
                    if state=='available':self.a.db.execute('INSERT OR IGNORE INTO request_notifications(user_id,request_id,created) VALUES(?,?,?)',(uid,rid,int(self.now())))
        finally:self.refresh_lock.release()
    def background(self):
        while True:
            try:self.refresh()
            except Exception:
                import logging;logging.exception('Request progress refresh deferred')
            self.wake.wait(30);self.wake.clear()
    def rows(self,uid,offset=0,limit=50):
        # Use saved observations here: page loads never wait on acquisition.
        with self.a.lock:
            total=self.a.db.execute('SELECT count(*) FROM music_requests WHERE user_id=?',(uid,)).fetchone()[0]
            rows=self.a.db.execute('SELECT r.*,p.payload FROM music_requests r LEFT JOIN request_progress p ON p.user_id=r.user_id AND p.request_id=r.request_id WHERE r.user_id=? ORDER BY r.created DESC,r.rowid DESC LIMIT ? OFFSET ?',(uid,limit,offset)).fetchall()
            items=[]
            for row in rows:
                data=object_value(row['payload']) if row['payload'] else {**object_value(row['metadata']),'state':'queued','created':row['created'],'updated':row['created']}
                data.setdefault('state','queued');data.setdefault('created',row['created']);data.setdefault('updated',row['created'])
                label,description,stage=STAGES.get(data['state'],('In progress','We’re working on your request.',1))
                data.update(id=row['request_id'],label=label,description=description,stage=stage)
                # Operational diagnostics stay in private service logs, not listener copy.
                data['detail']=''
                data['message']=description
                notice=self.a.db.execute('SELECT read_at FROM request_notifications WHERE user_id=? AND request_id=? AND dismissed_at=0',(uid,row['request_id'])).fetchone()
                data['unread']=bool(notice and not notice['read_at'])
                data['timeline']=[dict(e) for e in self.a.db.execute('SELECT state,created FROM request_events WHERE user_id=? AND request_id=? ORDER BY id DESC LIMIT 12',(uid,row['request_id']))][::-1]
                for event in data['timeline']:event['label']=STAGES.get(event['state'],('In progress',))[0]
                items.append(data)
        return {'items':items,'total':total,'next_offset':offset+len(items),'status_unavailable':self.unavailable}
    def notifications(self,uid):
        with self.a.lock:
            count=self.a.db.execute('SELECT count(*) FROM request_notifications WHERE user_id=? AND read_at=0 AND dismissed_at=0',(uid,)).fetchone()[0]
            rows=self.a.db.execute('SELECT n.*,p.payload FROM request_notifications n JOIN request_progress p ON p.user_id=n.user_id AND p.request_id=n.request_id WHERE n.user_id=? AND n.dismissed_at=0 ORDER BY n.id DESC LIMIT 50',(uid,)).fetchall()
            items=[]
            for r in rows:
                data=object_value(r['payload']);items.append({'id':r['id'],'request_id':r['request_id'],'created':r['created'],'read':bool(r['read_at']),**{k:data.get(k) for k in ('title','artist','album','track_id','album_id','artwork')}})
        return {'items':items,'unread':count}
    def mark_read(self,uid,ids):
        if not isinstance(ids,list) or len(ids)>50 or any(type(i)!=int for i in ids):raise ValueError('Choose valid notifications.')
        with self.a.transaction():
            for i in ids:self.a.db.execute('UPDATE request_notifications SET read_at=? WHERE id=? AND user_id=? AND read_at=0',(int(self.now()),i,uid))
        return {'ok':True}

    def mark_all_read(self,uid):
        with self.a.transaction():self.a.db.execute('UPDATE request_notifications SET read_at=? WHERE user_id=? AND read_at=0 AND dismissed_at=0',(int(self.now()),uid))
        return {'ok':True}

    def dismiss(self,uid,ids=None,all=False):
        if not all and (not isinstance(ids,list) or len(ids)>50 or any(type(i)!=int for i in ids)):
            raise ValueError('Choose valid notifications.')
        # Keep the unique request row as a tombstone so refresh cannot recreate it.
        with self.a.transaction():
            stamp=max(1,int(self.now()))
            if all:
                self.a.db.execute('UPDATE request_notifications SET dismissed_at=?,read_at=CASE WHEN read_at=0 THEN ? ELSE read_at END WHERE user_id=? AND dismissed_at=0',(stamp,stamp,uid))
            else:
                for ident in ids:self.a.db.execute('UPDATE request_notifications SET dismissed_at=?,read_at=CASE WHEN read_at=0 THEN ? ELSE read_at END WHERE user_id=? AND id=? AND dismissed_at=0',(stamp,stamp,uid,ident))
        return {'ok':True}


_BACKGROUND_MARKERS=(None,0,set())
def mark_background_requested(items,path='/requests/song-routes/background-request-markers.json',now=time.time):
    """Read one small cached snapshot; no indexer calls on the UI path."""
    from pathlib import Path
    global _BACKGROUND_MARKERS
    def identity(item):
        # Preserve version words; normalize punctuation and equivalent &/and credits.
        return key(str(item.get('artist','')).replace('&',' and ')),key(item.get('title',''))
    try:
        p=Path(path);stat=p.stat();stamp=(str(p),stat.st_mtime_ns,stat.st_size)
        if stamp!=_BACKGROUND_MARKERS[0]:
            payload=json.loads(p.read_text())
            songs={identity(x) for x in payload['items'] if x.get('artist') and x.get('title')}
            _BACKGROUND_MARKERS=(stamp,float(payload['updated']),songs)
        _,updated,songs=_BACKGROUND_MARKERS
        if not 0<=now()-updated<3600:return items
        for item in items:
            if identity(item) in songs:item['requested']=True
    except (OSError,ValueError,KeyError,TypeError):pass
    return items

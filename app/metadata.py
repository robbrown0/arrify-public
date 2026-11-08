"""Conservative, reversible catalog research. Media files are never written."""
import collections
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import urllib.parse
from tinytag import TinyTag
from .browse import genres,year,GENRE_NAMES
from .radio import artist_key,recording_key
from .search import normalize

DATE_AUDIT_VERSION=2

MBID=re.compile(r'^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$')

def versions(title):
    return set(re.findall(r'\b(live|remix|acoustic|instrumental|karaoke|demo)\b',normalize(title)))

def credit(recording):
    return ' '.join(x.get('name',x.get('artist',{}).get('name',''))+x.get('joinphrase','') for x in recording.get('artist-credit',[]) if isinstance(x,dict)).strip()

def matched(local,recording):
    return recording_key(local)==recording_key({'artist':credit(recording),'title':recording.get('title','')}) and versions(local['title'])==versions(recording.get('title','')+' '+recording.get('disambiguation','')) and not recording.get('video',False)

def voted_genres(value):
    votes=collections.Counter()
    for row in value.get('genres',[]) or value.get('tags',[]):
        if not isinstance(row,dict) or not isinstance(row.get('count'),int) or row['count']<2:continue
        for g in genres(row.get('name','')):
            if g!='other':votes[g]+=row['count']
    if not votes:return []
    peak=max(votes.values());return sorted(g for g,count in votes.items() if count>=max(2,peak*.4))

class Metadata:
    def __init__(self,directory,library,fetch=None):
        self.path=Path(directory)/'metadata.sqlite3';self.lib=library;self.fetch=fetch;self.lock=threading.RLock();self.wake=threading.Event();self.changed=False;self.generation=0;self.last_publish=0;self.active=None;self.queueing=False;self.last_error=''
        with contextlib.closing(self.connect()) as db:
            self.jobs={tid:json.loads(payload) for tid,payload in db.execute('SELECT id,payload FROM research')}
            self.overrides={tid:json.loads(payload) for tid,payload in db.execute('SELECT id,payload FROM overrides')}
        for job in self.jobs.values():
            if job['state']=='researching':job['state']='pending'

    def connect(self):
        db=sqlite3.connect(self.path);os.chmod(self.path,0o600)
        db.execute('CREATE TABLE IF NOT EXISTS research(id INTEGER PRIMARY KEY,payload TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS overrides(id INTEGER PRIMARY KEY,payload TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS metadata_audit(sequence INTEGER PRIMARY KEY,track_id INTEGER,action TEXT,payload TEXT,created INTEGER)');db.commit();return db

    def start(self):
        if os.environ.get('ARRIFY_METADATA_RESEARCH','1')=='1':threading.Thread(target=self.background,daemon=True,name='arrify-metadata-research').start()

    def snapshot(self):
        with self.lock:return dict(self.overrides)

    def save_job(self,job):
        job={**job,'updated':int(time.time())}
        with contextlib.closing(self.connect()) as db:
            db.execute('INSERT OR REPLACE INTO research VALUES(?,?)',(job['id'],json.dumps(job)));db.commit()
        with self.lock:self.jobs[job['id']]=job
        return job

    def current_files(self):
        with self.lib.music.lock:return {r[0]:r[1] for r in self.lib.music.db.execute('SELECT id,modified_ns FROM tracks WHERE available=1')}

    def enqueue(self,scope='missing'):
        if scope not in ('missing','all'):raise ValueError('Unknown research scope.')
        with self.lib.discovery.lock:tags=dict(self.lib.discovery.tags)
        current=self.current_files();pending=[]
        tracks=getattr(self.lib,'tracks',{})
        def priority(item):
            album=normalize(tracks.get(item[0],{}).get('album',''))
            suspect=bool(re.search(r'\b(complete|compilation|greatest|best of|anthology|collection|anniversary|remaster|deluxe|live at|live in|live from)\b',album))
            return (not suspect,item[0])
        for tid,modified in sorted(current.items(),key=priority):
            info=tags.get(tid)
            if not info or info[0]!=modified:continue  # Wait for the file-tag index.
            # Every recording needs a date audit, including apparently valid release years.
            with self.lock:job=self.jobs.get(tid)
            if job and job['modified']==modified and job.get('date_audit_version')==DATE_AUDIT_VERSION:
                if job['state'] not in ('unmatched','failed') or scope!='all' and time.time()-job.get('updated',0)<(3600 if job['state']=='failed' else 7*86400):continue
            pending.append({'id':tid,'modified':modified,'state':'pending','date_audit_version':DATE_AUDIT_VERSION,'proposal':{},'reason':'Waiting for metadata research.','updated':int(time.time())})
            if scope=='missing' and len(pending)>=500:break
        with self.lock:
            pending=[j for j in pending if not self.jobs.get(j['id']) or self.jobs[j['id']]['modified']!=j['modified'] or self.jobs[j['id']].get('date_audit_version')!=DATE_AUDIT_VERSION or self.jobs[j['id']]['state'] in ('unmatched','failed')]
            with contextlib.closing(self.connect()) as db:
                db.executemany('INSERT OR REPLACE INTO research VALUES(?,?)',[(j['id'],json.dumps(j)) for j in pending]);db.commit()
            self.jobs.update({j['id']:j for j in pending})
        self.wake.set();return len(pending)

    def request_scan(self,scope):
        if scope not in ('missing','all'):raise ValueError('Unknown research scope.')
        with self.lock:
            if self.queueing:return {'queued':True}
            self.queueing=True
        def enqueue():
            try:self.enqueue(scope)
            finally:self.queueing=False
        threading.Thread(target=enqueue,daemon=True,name='arrify-metadata-queue').start();return {'queued':True}

    def lookup(self,track,tag):
        track=dict(track)
        album=normalize(track.get('album',''))
        album_live=bool(re.search(r'\b(live at|live in|live from|in concert|unplugged)\b',album))
        fetch=self.fetch or self.lib.radio.similar.fetch;other=getattr(tag,'other',{}) or {};rid=None
        for key in ('musicbrainz_recordingid','musicbrainz_recording_id','musicbrainz recording id','musicbrainz_trackid','musicbrainz track id'):
            raw=other.get(key,[]);raw=[raw] if isinstance(raw,str) else raw
            rid=next((s for s in raw if isinstance(s,str) and MBID.fullmatch(s)),None)
            if rid:break
        if rid:candidates=[fetch('https://musicbrainz.org/ws/2/recording/'+rid+'?inc=artists+artist-credits+genres+tags+releases&fmt=json')]
        else:
            def quote(s):return '"'+s[:300].replace('\\','\\\\').replace('"','\\"')+'"'
            title=recording_key(track)[1]
            if not title or not artist_key(track['artist']):return None,'The identity is incomplete; an audio fingerprint or manual identification is needed.'
            query='recording:'+quote(title)+' AND artist:'+quote(track['artist'])
            result=fetch('https://musicbrainz.org/ws/2/recording/?'+urllib.parse.urlencode({'query':query,'fmt':'json','limit':25}))
            candidates=result.get('recordings',[])
            if result.get('recording-count',len(candidates))>len(candidates):return None,'Too many plausible recordings; a fingerprint or manual recording identifier is needed.'
        if album_live:
            candidates=[r for r in candidates if 'live' in versions(r.get('title','')+' '+r.get('disambiguation',''))]
        candidates=[r for r in candidates if isinstance(r,dict) and MBID.fullmatch(r.get('id','')) and (matched(track,r) or rid and r.get('id')==rid)]
        # Duration distinguishes multiple performances without uploading audio.
        duration=float(getattr(tag,'duration',0) or 0)*1000
        if duration:
            candidates=[r for r in candidates if not r.get('length') or abs(r['length']-duration)<=3000]
        unique={r['id']:r for r in candidates}
        if len(unique)!=1:return None,'No unique recording match. Manual identification is needed; no correction was applied.'
        selected=next(iter(unique.values()))
        if not rid:selected=fetch('https://musicbrainz.org/ws/2/recording/'+selected['id']+'?inc=artists+artist-credits+genres+tags+releases&fmt=json')
        if selected.get('id') not in unique or not (matched(track,selected) or rid and selected.get('id')==rid):return None,'Recording identity changed during verification.'
        if duration and selected.get('length') and abs(selected['length']-duration)>3000:return None,'Recording duration does not match this file.'
        strong=bool(rid or duration and selected.get('length') and abs(selected['length']-duration)<=2000)
        proposal={};sources=[]
        identity_agrees=matched(track,selected)
        if rid and not identity_agrees:
            proposal.update(title=selected.get('title','')[:500],artist=credit(selected)[:500]);sources.append('Embedded recording identifier; text needs review')
        y=year(selected.get('first-release-date'))
        if y:proposal['year']=y;sources.append('MusicBrainz recording first release')
        gs=voted_genres(selected);genre_level='recording'
        if not gs:
            artists=[c.get('artist',{}) for c in selected.get('artist-credit',[]) if isinstance(c,dict)]
            if len(artists)==1 and MBID.fullmatch(artists[0].get('id','')):
                a=fetch('https://musicbrainz.org/ws/2/artist/'+artists[0]['id']+'?inc=genres+tags&fmt=json')
                if a.get('id')==artists[0]['id'] and artist_key(a.get('name',''))==artist_key(track['artist']):gs=voted_genres(a);genre_level='artist'
        if gs:proposal['genres']=gs;sources.append('MusicBrainz '+genre_level+' genre votes')
        return {'values':proposal,'recording_id':selected['id'],'url':'https://musicbrainz.org/recording/'+selected['id'],'confidence':95 if strong else 75,'auto':strong and identity_agrees and (not gs or genre_level=='recording'),'genre_level':genre_level,'sources':sources},'Verified recording and duration/identifier.' if strong else 'Exact text match, but duration/identifier evidence is insufficient for automatic correction.'

    def research(self,job):
        tid=job['id'];track=self.lib.track(tid);row,filename=self.lib.music._track_file(tid)
        if row['modified_ns']!=job['modified']:return self.save_job({**job,'state':'unmatched','reason':'File changed; wait for the next tag scan.'})
        if os.stat(filename).st_mtime_ns!=job['modified']:return self.save_job({**job,'state':'unmatched','reason':'File changed; wait for the next catalog scan.'})
        tag=TinyTag.get(filename,duration=True,image=False);proposal,reason=self.lookup(track,tag)
        if not proposal or not proposal['values']:return self.save_job({**job,'state':'unmatched','reason':reason if not proposal else 'Recording matched, but the source has no usable genre or original year.'})
        with self.lib.discovery.lock:local=self.lib.discovery.tags.get(tid,(0,{}))[1]
        old={'title':track['title'],'artist':track['artist'],'genres':track.get('catalog_genres',local.get('genres',['other'])),'year':track.get('catalog_year',local.get('year',0))}
        # Preserve source attribution even when a verified original year equals
        # a release tag; this prevents remaster dates being mistaken for proof.
        changes={k:v for k,v in proposal['values'].items() if old.get(k)!=v or k=='year' and track.get('catalog_date_source')!='researched'}
        proposal['values']=changes
        if not changes:return self.save_job({**job,'state':'unchanged','reason':'Available metadata agrees with the catalog.','proposal':proposal,'before':old})
        result=self.save_job({**job,'state':'review','reason':reason+(' Artist-level genre needs review.' if proposal['genre_level']=='artist' else ''),'proposal':proposal,'before':old})
        if not proposal['auto'] and proposal['confidence']>=95 and 'year' in changes and not any(k in changes for k in ('title','artist')):
            date_proposal={**proposal,'values':{'year':changes['year']},'auto':True}
            result=self.save_job({**result,'proposal':date_proposal,'deferred_genres':changes.get('genres'),'reason':'Verified recording date; artist-level genre left unchanged.'})
            return self.edit({'action':'accept','id':tid,'revision':self.revision(result)},automatic=True)
        if proposal['auto']:return self.edit({'action':'accept','id':tid,'revision':self.revision(result)},automatic=True)
        return result

    @staticmethod
    def revision(job):return hashlib.sha256(json.dumps(job,sort_keys=True).encode()).hexdigest()[:24]

    def edit(self,data,automatic=False):
        tid=int(data.get('id',0));action=data.get('action')
        if action not in ('accept','reject','undo'):raise ValueError('Unknown metadata action.')
        with self.lock:
            job=self.jobs.get(tid)
            if not job or data.get('revision')!=self.revision(job):raise ValueError('This result changed. Refresh Metadata and try again.')
            job=dict(job);current=self.overrides.get(tid)
            if action in ('accept','reject') and job['state']!='review' or action=='undo' and job['state']!='applied':raise ValueError('This action is no longer available.')
            if action=='accept':
                if self.current_files().get(tid)!=job['modified']:raise ValueError('The file changed. Research it again before applying corrections.')
                _,filename=self.lib.music._track_file(tid)
                if os.stat(filename).st_mtime_ns!=job['modified']:raise ValueError('The file changed. Wait for a new scan.')
                proposal=job['proposal'];values=proposal['values']
                if not values or any(k not in ('genres','year','title','artist') for k in values):raise ValueError('Invalid metadata correction.')
                result={'modified':job['modified'],'values':{**(current or {}).get('values',{}),**values},'source':proposal['url'],'confidence':proposal['confidence']};job['previous']=current;job['state']='applied'
            elif action=='undo':result=job.get('previous');job['state']='reverted'
            else:result=current;job['state']='rejected'
            job['updated']=int(time.time());job['automatic']=automatic
            with contextlib.closing(self.connect()) as db:
                db.execute('BEGIN IMMEDIATE')
                if result:db.execute('INSERT OR REPLACE INTO overrides VALUES(?,?)',(tid,json.dumps(result)))
                elif action=='undo':db.execute('DELETE FROM overrides WHERE id=?',(tid,))
                db.execute('INSERT OR REPLACE INTO research VALUES(?,?)',(tid,json.dumps(job)))
                db.execute('INSERT INTO metadata_audit(track_id,action,payload,created) VALUES(?,?,?,?)',(tid,('auto-' if automatic else '')+action,json.dumps({'before':current,'after':result}),job['updated']));db.commit()
            self.jobs[tid]=job
            if result:self.overrides[tid]=result
            elif action=='undo':self.overrides.pop(tid,None)
            if action!='reject':self.changed=True;self.generation+=1
        self.wake.set();return {'ok':True,'state':job['state']}

    def public(self,offset=0,limit=50):
        with self.lock:jobs=[dict(j) for j in self.jobs.values()];counts=dict(collections.Counter(j['state'] for j in jobs))
        jobs.sort(key=lambda j:(j['state']!='review',-j.get('updated',0),j['id']))
        items=[]
        for j in jobs[offset:offset+limit]:
            try:track=self.lib.track(j['id'])
            except ValueError:continue
            items.append({**{k:v for k,v in j.items() if k!='previous'},'revision':self.revision(j),'title':track['title'],'artist':track['artist']})
        return {'items':items,'counts':counts,'total':len(jobs),'next_offset':min(len(jobs),offset+limit),'active':self.active,'queueing':self.queueing,'error':self.last_error,'publishing':self.changed}

    def background(self):
        next_queue=0
        while True:
            if self.changed and time.time()-self.last_publish>30:
                try:
                    with self.lock:generation=self.generation
                    with self.lib.refresh_lock:self.lib.reindex()
                    with self.lock:self.changed=self.generation!=generation;self.last_publish=time.time()
                except Exception:self.last_error='Catalog refresh will retry.'
            if time.time()>next_queue:
                try:self.enqueue('missing')
                except Exception:self.last_error='Metadata queue will retry.'
                next_queue=time.time()+60
            with self.lock:job=next((dict(j) for j in self.jobs.values() if j['state']=='pending'),None)
            if job:
                self.active=job['id'];self.save_job({**job,'state':'researching'})
                try:self.research(job);self.last_error=''
                except Exception:
                    self.save_job({**job,'state':'failed','reason':'Metadata provider or file is temporarily unavailable. Research will retry later.'})
                    self.last_error='Research is backing off after a provider/file error.'
                    self.wake.wait(30);self.wake.clear()
                finally:self.active=None
                time.sleep(.2)
            else:self.wake.wait(10);self.wake.clear()

"""Durable, cumulative acquisition accounting; failed transfers are not refunded."""
import json
import math
import re
from datetime import datetime,date,time,timedelta,timezone
from zoneinfo import ZoneInfo
from .store import now
from .matching import norm

def release_signature(label):
    return ''.join(c for c in norm(label) if c.isalnum())

class Budget:
    def __init__(self,store,limit,window_date=None,timezone_name="America/New_York",downloads=False,clock=None):
        self.s=store; self.limit=limit
        self.window_date=window_date; self.zone=ZoneInfo(timezone_name)
        self.downloads=downloads; self.factor=11 if downloads else 22
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        store.db.executescript('''
        CREATE TABLE IF NOT EXISTS transfers(id INTEGER PRIMARY KEY, rg TEXT, guid TEXT, indexer INTEGER,
          payload_bytes INTEGER, charged_bytes INTEGER, quality_tier INTEGER, state TEXT, created TEXT, updated TEXT,
          download_id TEXT, detail TEXT, UNIQUE(guid,indexer));
        CREATE TABLE IF NOT EXISTS expansion_state(key TEXT PRIMARY KEY,value TEXT);
        '''); store.db.commit()
    @classmethod
    def for_config(cls,store,config):
        if getattr(config,'arrify_request_rg',None):
            return RequestBudget(store,config.arrify_request_rg,config)
        return cls(store,config.budget_bytes,config.budget_date,config.budget_timezone,True)
    def active(self):
        return self.window_date=='daily' or not self.window_date or self.clock().astimezone(self.zone).date().isoformat()==self.window_date
    def bounds(self):
        day=self.clock().astimezone(self.zone).date() if self.window_date=='daily' else date.fromisoformat(self.window_date)
        start=datetime.combine(day,time.min,self.zone)
        return start.astimezone(timezone.utc).isoformat(),(start+timedelta(days=1)).astimezone(timezone.utc).isoformat()
    def spent(self):
        column='(payload_bytes*11+9)/10' if self.downloads else 'charged_bytes'
        sql='SELECT coalesce(sum('+column+'),0) FROM transfers'
        return self.s.db.execute(sql+' WHERE created>=? AND created<?',self.bounds()).fetchone()[0] if self.window_date else self.s.db.execute(sql).fetchone()[0]
    def remaining(self): return max(0,self.limit-self.spent()) if self.active() else 0
    def reserve(self,rg,guid,indexer,size,tier):
        if not isinstance(size,int) or size<=0: return None
        charge=(size*self.factor+9)//10
        self.s.db.execute('BEGIN IMMEDIATE')
        try:
            if self.limit<=0 or charge>self.remaining(): self.s.db.rollback(); return None
            cur=self.s.db.execute('INSERT OR IGNORE INTO transfers(rg,guid,indexer,payload_bytes,charged_bytes,quality_tier,state,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',
                (rg,guid,indexer,size,charge,tier,'reserved',self.clock().astimezone(timezone.utc).isoformat(),now()))
            if not cur.rowcount: self.s.db.rollback(); return None
            self.s.db.commit(); return cur.lastrowid
        except Exception: self.s.db.rollback(); raise
    def state(self,tid,state,download_id=None,detail=''):
        self.s.db.execute('UPDATE transfers SET state=?,updated=?,download_id=coalesce(?,download_id),detail=? WHERE id=?',
                          (state,now(),download_id,detail,tid)); self.s.db.commit()
    def get(self,key,default=None):
        r=self.s.db.execute('SELECT value FROM expansion_state WHERE key=?',(key,)).fetchone()
        return json.loads(r[0]) if r else default
    def put(self,key,value):
        self.s.db.execute('INSERT OR REPLACE INTO expansion_state VALUES(?,?)',(key,json.dumps(value))); self.s.db.commit()
    def report(self):
        return {'limit_bytes':self.limit,'charged_bytes':self.spent(),'remaining_bytes':self.remaining(),
                'accounting':('Download payload plus 10% transfer reserve; failed transfers count; imported copies and storage do not count.' if self.downloads else 'Legacy storage reservation'),
                'authorized_date':self.clock().astimezone(self.zone).date().isoformat() if self.window_date=='daily' else self.window_date,'timezone':str(self.zone),'allowance_active':self.active(),
                'automatically_renews':self.window_date=='daily',
                'expires_utc':self.bounds()[1] if self.window_date else None,
                'transfer_states':dict(self.s.db.execute('SELECT state,count(*) FROM transfers GROUP BY state'))}


class RequestBudget(Budget):
    """Operator-authorized incremental funding, scoped to one requested album.

    Selection is conditional on a verified, sized, eligible release. The exact
    grant and transfer reservation commit atomically before any grab. No global
    byte limit or date is changed. Failed attempts stay charged; existing quality
    fallback and ambiguous-write holds still control subsequent attempts.
    """
    def __init__(self,store,rg,config=None):
        self.daily_cap=config is not None and config.budget_date=='daily'
        super().__init__(store,config.budget_bytes if self.daily_cap else 0,config.budget_date if self.daily_cap else None,config.budget_timezone if self.daily_cap else 'America/New_York',downloads=True)
        self.rg=rg
        self.s.db.executescript('''CREATE TABLE IF NOT EXISTS arrify_request_grants(
            transfer_id INTEGER PRIMARY KEY, rg TEXT NOT NULL, request_id TEXT NOT NULL,
            payload_bytes INTEGER NOT NULL, authorized_bytes INTEGER NOT NULL, created TEXT NOT NULL);''')
        self.s.db.commit()
    def authorization(self):
        return self.s.cached('arrify-authorization:'+self.rg,ttl=86400*3650)
    def active(self):return bool(self.authorization())
    def remaining(self):
        # Conditional purchasing power, never a recorded unlimited byte grant.
        # reserve() below authorizes only the exact eligible release payload.
        return (max(0,self.limit-Budget.spent(self)) if self.daily_cap else 2**63-1) if self.active() else 0
    def spent(self):
        return self.s.db.execute('SELECT coalesce(sum(authorized_bytes),0) FROM arrify_request_grants WHERE rg=?',(self.rg,)).fetchone()[0]
    def reserve(self,rg,guid,indexer,size,tier):
        if rg!=self.rg or type(size) is not int or size<=0 or not 0<tier<=4:return None
        self.s.db.execute('BEGIN IMMEDIATE')
        try:
            auth=self.authorization()
            if not auth:self.s.db.rollback();return None
            charge=(size*11+9)//10
            if self.daily_cap and charge>self.remaining():self.s.db.rollback();return None
            stamp=now()
            cur=self.s.db.execute('INSERT OR IGNORE INTO transfers(rg,guid,indexer,payload_bytes,charged_bytes,quality_tier,state,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',
                (rg,guid,indexer,size,charge,tier,'reserved',stamp,stamp))
            if not cur.rowcount:self.s.db.rollback();return None
            self.s.db.execute('INSERT INTO arrify_request_grants VALUES(?,?,?,?,?,?)',(cur.lastrowid,rg,auth['request_id'],size,charge,stamp))
            self.s.db.commit()
            self.s.audit('arrify-request-allowance-increased',{'rg':rg,'request_id':auth['request_id'],'transfer_id':cur.lastrowid,'payload_bytes':size,'authorized_bytes':charge})
            return cur.lastrowid
        except Exception:
            self.s.db.rollback();raise
    def report(self):
        return {'mode':'explicit-user-request','release_group_id':self.rg,'charged_bytes':self.spent(),
            'limit_bytes':None,'remaining_bytes':None,'allowance_active':self.active(),
            'accounting':'Exact verified requested-album release size plus 10%; failed attempts remain charged.',
            'background_allowance_changed':False}


def tier(release):
    q=release.get('quality',{}).get('quality',{})
    qid=q.get('id'); name=q.get('name','').upper()
    if qid in (6,7,21,35,36,37) or any(x in name for x in ('FLAC','ALAC','WAVPACK','APE')): return 4
    if qid in (4,11,9,12,14) or '320' in name: return 3
    if qid in (3,10,8,16,17,18) or '256' in name: return 2
    if qid in (1,5,19,22,33) or '192' in name or '128' in name or '160' in name: return 1
    return 0


def choose_release(releases,ceiling,remaining,seen,reserve_factor=22,blocked_signatures=None,indexer_priorities=None):
    candidates=[]
    for r in releases:
        if release_signature(r.get('title','')) in (blocked_signatures or set()): continue
        t=tier(r); size=r.get('size',0)
        label=(r.get('title') or '').strip().rstrip('\\"\' ')
        if re.search(r'\.(?:flac|mp3|m4a|aac|ogg|wav|ape|wma)$',label,re.I): continue
        if not 0<t<=ceiling or not isinstance(size,int) or size<=0 or (size*reserve_factor+9)//10>remaining: continue
        rejections=r.get('rejections') or []
        quality_only=bool(rejections) and all(isinstance(x,str) and re.fullmatch(r'.+ is not wanted in profile',x) for x in rejections)
        # Explicit user authorization permits lossy fallback without editing an existing artist profile.
        if r.get('discography') or not r.get('downloadAllowed',False): continue
        if (r.get('rejected') or not r.get('approved',False)) and not quality_only: continue
        if (r.get('guid'),r.get('indexerId')) in seen: continue
        candidates.append(r)
    return max(candidates,key=lambda r:(tier(r),r.get('qualityWeight',0),r.get('customFormatScore',0),-(indexer_priorities or {}).get(r.get('indexerId'),25),-r['size']),default=None)

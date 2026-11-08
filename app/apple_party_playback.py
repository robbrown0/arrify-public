"""Fenced host-only MusicKit playback; queue credits follow confirmed transitions."""
import json
import math
import re
import time
from .library import PlaybackConflict


class ApplePartyPlayback:
    def __init__(self,parties,now=time.time,provider='apple'):
        if provider not in ('apple','spotify'):raise ValueError('Unknown music provider.')
        self.provider=provider;self.field=provider+'_playback';self.table=provider+'_party_playback';self.history=provider+'_music_history';self.label='Spotify' if provider=='spotify' else 'Apple Music'
        self.s=parties;self.a=parties.a;self.now=now
        with self.a.lock:
            self.a.db.execute('CREATE TABLE IF NOT EXISTS '+self.table+'(user_id INTEGER PRIMARY KEY,payload TEXT NOT NULL)')
            self.a.db.execute('CREATE TABLE IF NOT EXISTS '+self.history+'(user_id INTEGER,party_id TEXT,entry_id INTEGER,title TEXT,artist TEXT,started INTEGER,PRIMARY KEY(user_id,party_id,entry_id))')
    def load(self,uid):
        row=self.a.db.execute('SELECT payload FROM '+self.table+' WHERE user_id=?',(uid,)).fetchone()
        return json.loads(row[0]) if row else {'party_id':None,'device_id':'','device_name':'','epoch':0,'revision':0,'entry_id':None,'observed':'paused','desired_playing':False,'ever_playing':False,'seen':0,'sequence':0,'position':0,'duration':0,'error':''}
    def save(self,uid,d):self.a.db.execute('INSERT INTO '+self.table+' VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET payload=excluded.payload',(uid,json.dumps(d)))
    def online(self,d):return bool(d['device_id'] and 0<=self.now()-d['seen']<20)
    def stop(self,uid,pid=None):
        d=self.load(uid)
        if not d['party_id'] or pid is not None and d['party_id']!=pid:return
        old=d['party_id'];d.update(party_id=None,entry_id=None,observed='paused',desired_playing=False,epoch=d['epoch']+1,revision=d['revision']+1,seen=0,error='');self.save(uid,d);self.s.touch(old)
    def view(self,p,host=False):
        d=self.load(p['owner']);active=d['party_id']==p['id'];online=active and self.online(d)
        result={'active':active,'playing':bool(online and d['observed']=='playing'),'playback_state':d['observed'] if online else 'offline'}
        if host:result[self.field]={**d,'online':online}
        return result
    def select(self,p,d):
        row=self.a.db.execute("SELECT id,metadata FROM party_entries WHERE party_id=? AND status IN ('playing','queued') ORDER BY status='playing' DESC,position,id LIMIT 1",(p['id'],)).fetchone()
        d.update(entry_id=row['id'] if row else None,ever_playing=False,sequence=0,position=0,duration=0,error='',observed='buffering' if row and d['desired_playing'] else 'paused',revision=d['revision']+1)
        if row:self.a.db.execute("UPDATE party_entries SET status='playing' WHERE id=?",(row['id'],))
    def queue_changed(self,p):
        d=self.load(p['owner'])
        if d['party_id']==p['id'] and not d['entry_id'] and d['desired_playing']:
            self.select(p,d);self.save(p['owner'],d)
    def pause_local(self,uid):
        s=self.s.p.load(uid);d=s.get('listening')
        if d:
            d.update(playing=False,observed='paused',action='pause',epoch=d['epoch']+1,intent=d['intent']+1,revision=d['revision']+1)
            self.s.p.save(uid,s)
    def command(self,uid,pid,data):
        with self.a.transaction():
            p=self.s.row(pid,uid)
            if p.get('source')!=self.provider:raise ValueError('This party uses the Arrify library.')
            if p['ended']:raise PermissionError('This party has ended.')
            if not getattr(self.s,self.provider,None):raise ValueError(self.label+' setup is not complete.')
            try:storefront=getattr(self.s,self.provider).storefront(uid)
            except ValueError:raise PermissionError('Reconnect '+self.label+' in Music services.') from None
            if storefront!=p['storefront']:raise ValueError('Your music region changed. Create a new party for this region.')
            device=data.get('device_id','')
            if not isinstance(device,str) or not re.fullmatch('[a-f0-9-]{16,64}',device):raise ValueError('Invalid playback device.')
            action=data.get('action');d=self.load(uid)
            if action=='start':
                if self.provider=='spotify':
                    external=data.get('spotify_device_id','')
                    if not isinstance(external,str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}',external):raise ValueError('Spotify playback device is unavailable.')
                    d['spotify_device_id']=external
                if d['party_id'] and d['device_id']!=device and self.online(d) and not data.get('takeover'):raise PlaybackConflict(self.label+' is playing on another device. Choose Play here to move it.')
                same=d['party_id']==pid
                d.update(party_id=pid,device_id=device,device_name=str(data.get('device_name','This device'))[:60],epoch=d['epoch']+1,revision=d['revision']+1,sequence=0,desired_playing=True,user_paused=False,observed='buffering',error='',seen=self.now())
                if not same or not d['entry_id']:self.select(p,d)
                self.pause_local(uid)
                other=getattr(self.s,'spotify_playback' if self.provider=='apple' else 'apple_playback',None)
                if other:other.stop(uid)
            else:
                if d['party_id']!=pid or d['device_id']!=device or type(data.get('epoch')) is not int or data['epoch']!=d['epoch']:raise PlaybackConflict(self.label+' playback moved. Refresh the party controls.')
                if action=='heartbeat':
                    seq=data.get('sequence',0)
                    if type(seq) is not int or seq<=d['sequence']:return self.view(p,host=True) | {'_partial':True,'id':pid,'source':self.provider}
                    if data.get('entry_id')!=d['entry_id']:raise PlaybackConflict('The playing song changed. Refresh the party controls.')
                    if data.get('revision')!=d['revision']:raise PlaybackConflict('Playback controls changed. Read the current state before reporting.')
                    observed=data.get('observed')
                    if observed not in ('playing','paused','buffering','needs-gesture','error'):raise ValueError('Invalid music playback state.')
                    values=[data.get('position',0),data.get('duration',0)]
                    if not all(type(x) in (int,float) and math.isfinite(x) and 0<=x<=86400 for x in values):raise ValueError('Invalid playback position.')
                    d.update(sequence=seq,seen=self.now(),position=values[0],duration=values[1],observed=observed)
                    at_end=values[1]>0 and values[0]>=values[1]-.75 and d['ever_playing']
                    if d['entry_id'] and observed in ('playing','paused') and not d.get('user_paused') and not (observed=='paused' and at_end):d['desired_playing']=observed=='playing'
                    if observed=='playing' and d['entry_id']:
                        d['ever_playing']=True
                        row=self.a.db.execute('SELECT metadata FROM party_entries WHERE id=? AND party_id=?',(d['entry_id'],pid)).fetchone()
                        if row:
                            t=json.loads(row[0]);self.a.db.execute('INSERT OR IGNORE INTO '+self.history+' VALUES(?,?,?,?,?,?)',(uid,pid,d['entry_id'],t['title'],t['artist'],int(self.now())))
                    d['error']=str(data.get('error','')) if data.get('error') in ('authorization','subscription','playback','needs-gesture') else ''
                elif action in ('pause','resume'):
                    d.update(desired_playing=action=='resume',user_paused=action=='pause',observed='buffering' if action=='resume' else 'paused',revision=d['revision']+1,error='')
                    if action=='resume' and not d['entry_id']:self.select(p,d)
                elif action=='next':
                    if data.get('entry_id')!=d['entry_id'] or not d['entry_id']:raise PlaybackConflict('The queue has already advanced.')
                    if data.get('automatic') and data.get('revision',d['revision'])!=d['revision']:raise PlaybackConflict('Playback controls changed. Refresh before advancing.')
                    if data.get('automatic') and d.get('user_paused'):raise PlaybackConflict('Playback is paused. Resume before continuing the queue.')
                    if data.get('automatic') and not d['ever_playing']:raise ValueError('Playback was not confirmed. Retry this song or skip it explicitly.')
                    if data.get('automatic') and self.provider=='apple':
                        row=self.a.db.execute('SELECT metadata FROM party_entries WHERE id=? AND party_id=?',(d['entry_id'],pid)).fetchone()
                        catalog=float(json.loads(row[0]).get('duration',0)) if row else 0
                        actual=float(d.get('duration',0))
                        if 0<actual<=90 and catalog>actual+15 and actual<catalog*.75:
                            raise ValueError('Apple Music returned a short preview. The queue entry was preserved; retry full playback or use Safari.')
                    if data.get('automatic'):d['desired_playing']=True
                    self.a.db.execute("UPDATE party_entries SET status=? WHERE id=? AND party_id=? AND status='playing'",('played' if data.get('automatic') else 'skipped',d['entry_id'],pid))
                    self.select(p,d)
                elif action=='previous':
                    if data.get('entry_id')!=d['entry_id'] or not d['entry_id']:raise PlaybackConflict('The current song changed. Refresh the party.')
                    current=self.a.db.execute('SELECT * FROM party_entries WHERE id=? AND party_id=?',(d['entry_id'],pid)).fetchone()
                    previous=self.a.db.execute(f"SELECT e.* FROM {self.history} h JOIN party_entries e ON e.id=h.entry_id AND e.party_id=h.party_id WHERE h.user_id=? AND h.party_id=? AND e.status IN ('played','skipped') AND e.song_key!=? ORDER BY h.started DESC,h.entry_id DESC LIMIT 1",(uid,pid,current['song_key'])).fetchone()
                    if not previous:raise ValueError('There is no previous song in this party yet.')
                    if self.a.db.execute("SELECT count(*) FROM party_entries WHERE party_id=? AND status IN ('queued','playing')",(pid,)).fetchone()[0]>=500:raise ValueError('The party queue is full.')
                    # Replay is host-initiated and uncharged. Keep the interrupted
                    # guest request queued, without returning or spending credits.
                    if self.a.db.execute("SELECT 1 FROM party_entries WHERE party_id=? AND song_key=? AND status='queued'",(pid,previous['song_key'])).fetchone():raise ValueError('That song is already queued. Use Play next in the party queue.')
                    entry=self.a.db.execute("INSERT INTO party_entries(party_id,track_id,song_key,status,position,charged,created,catalog_id,metadata) VALUES(?,?,?,'playing',?,0,?,?,?)",(pid,0,previous['song_key'],current['position']-1,int(self.now()),previous['catalog_id'],previous['metadata'])).lastrowid
                    self.a.db.execute("UPDATE party_entries SET status='queued' WHERE id=?",(current['id'],))
                    d.update(entry_id=entry,ever_playing=False,sequence=0,position=0,duration=0,error='',observed='buffering' if d['desired_playing'] else 'paused',revision=d['revision']+1)
                else:raise ValueError('Unknown music playback action.')
            self.save(uid,d)
            if action!='heartbeat':self.s.touch(pid)
            result=(self.view(p,host=True) | {'_partial':True,'id':pid,'source':self.provider}) if action=='heartbeat' else self.s.view(self.s.row(pid,uid),host=True)
        if action!='heartbeat':self.s.notify(uid)
        return result

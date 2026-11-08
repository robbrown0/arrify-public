"""Bounded private playback events; never tokens, URLs, email, or audio content."""
import json
import math
import os
import secrets
import threading
import time
from pathlib import Path

class Diagnostics:
    EVENTS={'audio-session','startup-timeout','waiting','stalled','pause','visibility','route-change','advance-failed','airplay-end','load','playing','ended','seeked','seek-request','quality','error','retry','progress','clock-reset','metadata','stream-auth-denied','seek-unsupported','seek-fallback','seek-failed'}
    APPLE_EVENTS={'apple-playing','apple-preview-blocked','apple-playback-error'}
    SPOTIFY_EVENTS={'spotify-ready','spotify-not-ready','spotify-initialization-error','spotify-authentication-error','spotify-account-error','spotify-playback-error','spotify-autoplay-failed','spotify-api-error','spotify-state','spotify-output-request','spotify-visibility','spotify-audio-session'}
    SPOTIFY_REASONS={'unknown','protection','network','decode','unsupported','autoplay','interrupted','authorization','device-unavailable','timeout','playback'}
    NUMBERS={'audio_volume','buffered_ahead','network_state','http_status','attempt','wall_ms','track_id','next_id','position','checkpoint','duration','metadata_duration','stream_offset','ready_state','error_code','audio_track_count'}
    def __init__(self,directory):self.root=Path(directory)/'playback-diagnostics';self.lock=threading.Lock();self.last_denial=float('-inf')
    def write(self,uid,data):
        source=data.get('source','native')
        if source not in ('native','spotify','apple'):raise ValueError('Invalid diagnostic source.')
        allowed=self.SPOTIFY_EVENTS if source=='spotify' else self.APPLE_EVENTS if source=='apple' else self.EVENTS
        name=(source+'-' if source in ('spotify','apple') else '')+str(int(uid))
        events=data.get('events')
        if not isinstance(events,list) or len(events)>60:raise ValueError('Invalid playback events.')
        cleaned=[]
        for event in events:
            if not isinstance(event,dict) or not isinstance(event.get('event'),str) or event['event'] not in allowed:continue
            item={'event':event['event']}
            for key in self.NUMBERS:
                value=event.get(key)
                if isinstance(value,(int,float)) and not isinstance(value,bool) and abs(value)<1e15 and math.isfinite(value):item[key]=value
            for key in ('muted','paused','wireless','hidden','loading','intent'):
                if isinstance(event.get(key),bool):item[key]=event[key]
            if source=='apple':
                for key in ('authorized','preview_only','installed'):
                    if isinstance(event.get(key),bool):item[key]=event[key]
            if event.get('audio_session_state') in ('active','inactive','interrupted'):item['audio_session_state']=event['audio_session_state']
            if event.get('profile') in ('original','aac','mp3'):item['profile']=event['profile']
            if source=='spotify' and isinstance(event.get('reason'),str) and event['reason'] in self.SPOTIFY_REASONS:item['reason']=event['reason']
            cleaned.append(item)
        payload=json.dumps({'events':cleaned[-40:]},separators=(',',':')).encode()
        if len(payload)>32768:raise ValueError('Playback events too large.')
        with self.lock:
            self.root.mkdir(mode=0o700,exist_ok=True)
            temporary=self.root/(name+'.'+secrets.token_hex(8)+'.tmp')
            fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            try:
                with os.fdopen(fd,'wb') as handle:handle.write(payload)
                os.replace(temporary,self.root/(name+'.json'))
            finally:temporary.unlink(missing_ok=True)
        return {'ok':True}

    def denied_stream(self,tid):
        # Diagnose detached receiver auth failures without recording cookies,
        # addresses, tokens, or request URLs. One fixed-size system record.
        now=time.monotonic()
        if now-self.last_denial<10:return
        self.last_denial=now
        try:self.write(0,{'events':[{'event':'stream-auth-denied','track_id':tid,'wall_ms':int(time.time()*1000)}]})
        except OSError:pass

    def artwork_response(self,tid,status,size,granted,agent):
        # Coarse client family only: no raw headers, URLs, IPs or credentials.
        agent=agent.lower()
        client='receiver' if any(x in agent for x in ('applecoremedia','appletv','airplay')) else 'browser' if 'safari' in agent or 'chrome' in agent else 'other'
        event={'track_id':int(tid),'status':int(status),'bytes':int(size),'grant_present':bool(granted),'client':client,'at':int(time.time())}
        with self.lock:
            rows=getattr(self,'artwork_events',[])
            signature=lambda x:(x['track_id'],x['status'],x['grant_present'],x['client'])
            if any(signature(x)==signature(event) and event['at']-x['at']<10 for x in rows):return
            self.artwork_events=(rows+[event])[-60:]
            try:
                self.root.mkdir(mode=0o700,exist_ok=True)
                path=self.root/'artwork-responses.json'
                temporary=self.root/('artwork-'+secrets.token_hex(8)+'.tmp')
                fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                try:
                    with os.fdopen(fd,'w') as handle:json.dump({'events':self.artwork_events},handle)
                    os.replace(temporary,path)
                finally:temporary.unlink(missing_ok=True)
            except OSError:pass

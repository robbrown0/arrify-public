from .buffer_status import clean_buffer_report
"""Account-scoped single-output listening sessions with fenced device ownership."""
import json
import math
import re
import time
from .library import PlaybackConflict

class Listening:
    def __init__(self,player,now=time.time,events=None):self.p=player;self.a=player.a;self.now=now;self.events=events
    def session(self,s):
        d=s.setdefault('listening',{'owner':None,'device_name':'','epoch':0,'revision':0,'intent':0,'action':'pause','playing':False,'observed':'paused','seen':0,'ack':0,'duration':0,'quality':'','profile':'original','output':''})
        d.setdefault('play_id',0);d.setdefault('seek_intent',0);d.setdefault('seek_target',s.get('position',0));return d
    def load(self,uid):
        s=self.p.load(uid);d=self.session(s)
        row=self.a.db.execute('SELECT status FROM listening_status WHERE user_id=?',(uid,)).fetchone()
        if row:
            status=json.loads(row[0])
            if status.get('owner')==d['owner'] and status.get('epoch')==d['epoch']:
                d['seen']=max(d['seen'],status.get('seen',0))
                if status.get('intent')==d['intent'] and status.get('current')==s['current']:
                    s['position']=status['position']
                    for key in ('ack','observed','duration','quality','profile','output'):d[key]=status.get(key,d[key])
                    if d['observed'] in ('playing','paused','needs-gesture'):d['playing']=d['observed']=='playing'
        return s
    def device(self,data):
        device=str(data.get('device_id',''))
        if not re.fullmatch('[a-f0-9-]{16,64}',device):raise ValueError('Invalid playback device.')
        return device
    def position(self,s):
        d=self.session(s);age=self.now()-d['seen'];pos=s.get('position',0)
        if d['playing'] and d['observed']=='playing' and d['ack']==d['intent'] and 0<=age<=15:pos+=age
        return min(pos,d['duration']) if d['duration']>0 else pos
    def public(self,s,compact=False):
        result={'_partial':True,'current':s['current'],'position':s['position']} if compact else self.p.public(s);d=dict(self.session(s));d.update(online=bool(d['owner'] and self.now()-d['seen']<20),pending=d['ack']<d['intent'])
        result.update(listening=d,server_time=self.now())
        return result
    def state(self,uid,revision=None):
        with self.a.lock:
            s=self.load(uid);d=self.session(s)
            before=list(s['upcoming']);self.p.preview(s)
            if before!=s['upcoming']:
                d['revision']+=1;self.p.save(uid,s)
            return self.public(s,compact=revision is not None and str(revision)==str(s['listening']['revision']))
    def stream_allowed(self,uid,device,epoch):
        with self.a.lock:
            d=self.session(self.p.load(uid))
            return (not d['owner'] and not device and not epoch) or (bool(d['owner']) and d['owner']==device and str(d['epoch'])==str(epoch))
    def command(self,uid,data):
        with self.a.transaction():result=self._command(uid,data)
        if self.events:self.events.publish(uid)
        return result
    def _command(self,uid,data):
        device=self.device(data);action=data.get('action')
        with self.a.transaction():
            s=self.load(uid);d=self.session(s)
            if action=='heartbeat':
                if d['owner']!=device or d['epoch']!=int(data.get('owner_epoch',-1)):raise PlaybackConflict('Playback moved to another device.')
                prior=self.a.db.execute('SELECT status FROM listening_status WHERE user_id=?',(uid,)).fetchone()
                previous=json.loads(prior[0]) if prior else {}
                report_seq=int(data.get('report_seq',0))
                if report_seq and previous.get('epoch')==d['epoch'] and report_seq<=previous.get('report_seq',0):return self.public(s,compact=True)
                recorded=previous.get('history_play_id',-1)
                d['seen']=self.now()
                if data.get('expected_current')==s['current'] and int(data.get('intent',-1))==d['intent']:
                    pos=float(data.get('position',s['position']));duration=float(data.get('duration',0))
                    if not all(math.isfinite(v) and 0<=v<=86400 for v in (pos,duration)):raise ValueError('Invalid playback position.')
                    observed=data.get('observed','paused')
                    if observed not in ('playing','paused','buffering','interrupted','recovering','failed','needs-gesture'):raise ValueError('Invalid playback status.')
                    s['position']=pos;d.update(ack=d['intent'],observed=observed,duration=duration)
                    if observed in ('playing','paused','needs-gesture'):d['playing']=observed=='playing'
                    for key in ('quality','profile','output'):d[key]=str(data.get(key,''))[:80]
                    if observed=='playing' and s['current'] and recorded!=d['play_id']:
                        self.a.db.execute('INSERT INTO history(user_id,track_id,played_at) VALUES(?,?,?)',(uid,s['current'],int(self.now())))
                        recorded=d['play_id']
                status={'report_seq':report_seq,'history_play_id':recorded,'owner':d['owner'],'epoch':d['epoch'],'seen':d['seen'],'intent':d['ack'],'current':s['current'],'position':s['position'],**{key:d[key] for key in ('ack','observed','duration','quality','profile','output')}}
                if data.get('expected_current')==s['current'] and int(data.get('intent',-1))==d['intent']:
                    report=clean_buffer_report(data.get('buffer'))
                    if report is not None:
                        status['buffer']=report;status['buffer_at']=d['seen']-report.pop('sample_age_seconds')
                    elif all(previous.get(k)==status.get(k) for k in ('owner','epoch','intent','current')):
                        for key in ('buffer','buffer_at'):
                            if key in previous:status[key]=previous[key]
                # Frequent observations touch one small row, never the queue JSON.
                self.a.db.execute('INSERT INTO listening_status VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET status=excluded.status',(uid,json.dumps(status)))
                return self.public(s,compact=True)
            if action=='position':raise ValueError('Use a playback heartbeat.')
            if action=='reattach':
                if d['owner']!=device or d['epoch']!=int(data.get('owner_epoch',-1)):raise PlaybackConflict('Playback moved to another device.')
                if int(data.get('expected_revision',-1))!=d['revision']:raise PlaybackConflict('The listening session changed during reload.')
                # Resume from the last confirmed cursor, never projected elapsed reload time.
                d.update(epoch=d['epoch']+1,revision=d['revision']+1,intent=d['intent']+1,action='resume' if d['playing'] else 'pause',observed='buffering' if d['playing'] else 'paused',seen=self.now(),seek_intent=d['seek_intent']+1,seek_target=s['position'])
                self.p.save(uid,s);return self.public(s)
            # An explicit selection may recover a disconnected output in the same
            # transaction. Recheck its heartbeat here so a live device keeps control.
            recover_offline=(action=='play' and not data.get('automatic') and data.get('takeover_if_offline') is True
                             and d['owner']!=device and self.now()-d['seen']>=20)
            if action=='takeover' or recover_offline or (not d['owner'] and action in ('play','resume')):
                pos=self.position(s)
                if d['owner']!=device:d['epoch']+=1
                d.update(owner=device,device_name=str(data.get('device_name','Browser'))[:60],seen=self.now(),observed='paused',duration=0,quality='',output='')
                s['position']=pos;d['seek_intent']+=1;d['seek_target']=pos
                if action=='takeover':action='resume'
            if data.get('automatic'):
                if d['owner']!=device or d['epoch']!=int(data.get('owner_epoch',-1)):raise PlaybackConflict('Playback moved to another device.')
            if action=='next' and data.get('transition_id') and data['transition_id']==s.get('last_transition'):return self.public(s)
            if int(data.get('expected_revision',-1))!=d['revision']:raise PlaybackConflict('The listening session changed. Read its latest state before sending another command.')
            if action in ('play','resume','takeover','next','previous') and not data.get('automatic') and getattr(self.p,'parties',None):
                self.p.parties.apple_playback.stop(uid)
                self.p.parties.spotify_playback.stop(uid)
            if action in ('pause','resume','seek'):
                if not s['current']:raise ValueError('Choose a song first.')
                if not d['owner']:raise ValueError('Choose a playback device first.')
                if action=='seek':
                    if 'expected_current' in data and data['expected_current']!=s['current']:raise PlaybackConflict('The song changed before this seek arrived.')
                    if device==d['owner'] and isinstance(data.get('playing'),bool):d['playing']=data['playing']
                    pos=float(data.get('position',0))
                    if not math.isfinite(pos) or not 0<=pos<=86400:raise ValueError('Invalid playback position.')
                    s['position']=min(pos,d['duration']) if d['duration'] else pos;d['seek_intent']+=1;d['seek_target']=s['position']
                elif action=='pause':s['position']=self.position(s);d['playing']=False
                else:d['playing']=True
                d['action']=action;d['intent']+=1;d['revision']+=1
            else:
                if action not in ('play','next','previous','queue','remove','reorder','clear','shuffle','repeat','queue_edit','queue_jump'):raise ValueError('Unknown listening action.')
                if not d['owner'] and action in ('next','previous'):raise ValueError('Start playback on a device first.')
                # Existing channel rotation, history and transition-id rules remain authoritative.
                s['listening']=d;self.p.save(uid,s)
                result=self.p.command(uid,{**data,'action':action},record_history=False);s=self.p.load(uid);d=self.session(s)
                d['revision']+=1
                if action in ('play','next','previous','queue_jump'):
                    d.update(action='play',playing=bool(s['current']),duration=0,quality='',seek_target=s['position']);d['seek_intent']+=1;d['intent']+=1;d['play_id']+=1
            self.p.save(uid,s);return self.public(s)

    def logout(self,uid,data,digest):
        changed=False
        with self.a.transaction():
            s=self.load(uid);d=self.session(s)
            # Signing out a controller must not stop a different device's output.
            if data.get('device_id')==d['owner'] and d['owner'] and data.get('owner_epoch')==d['epoch']:
                if data.get('expected_current')==s['current'] and data.get('intent')==d['intent']:
                    pos=data.get('position')
                    if isinstance(pos,(int,float)) and math.isfinite(pos) and 0<=pos<=86400:s['position']=min(pos,d['duration']) if d['duration'] else pos
                d.update(owner=None,device_name='',epoch=d['epoch']+1,revision=d['revision']+1,intent=d['intent']+1,playing=False,observed='paused',action='pause',seen=0,output='')
                d.update(ack=d['intent'],seek_intent=d['seek_intent']+1,seek_target=s['position'])
                self.p.save(uid,s);changed=True
            self.a.db.execute('DELETE FROM sessions WHERE digest IN (SELECT f.anchor FROM native_families f JOIN native_access n ON n.family=f.id WHERE n.digest=? AND f.user_id=?)',(digest,uid))
            self.a.db.execute('DELETE FROM sessions WHERE digest=?',(digest,))
        if changed and self.events:self.events.publish(uid)

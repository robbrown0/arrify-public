"""Read-only admin activity snapshot; reuses existing auth and player heartbeats."""
import json
import time


def connected_users(accounts,library,actor,now=None,streams=None):
    if not actor.get('admin'):raise PermissionError('Administrator access required.')
    now=int(time.time() if now is None else now)
    with accounts.lock:
        rows=accounts.db.execute('''SELECT u.id,u.username,u.last_seen,u.last_login,
            coalesce(nullif(p.display_name,''),u.username) display_name,
            s.sessions,coalesce(pl.state,'{}') player_state,coalesce(ls.status,'{}') player_status
            FROM users u JOIN (SELECT user_id,count(*) sessions FROM sessions WHERE expires>? GROUP BY user_id) s ON s.user_id=u.id
            LEFT JOIN profiles p ON p.user_id=u.id LEFT JOIN player pl ON pl.user_id=u.id
            LEFT JOIN listening_status ls ON ls.user_id=u.id
            WHERE u.approved=1 AND u.frozen=0 AND u.deleted_at=0''',(now,)).fetchall()
        history={r[0]:(r[1],r[2]) for r in accounts.db.execute('SELECT user_id,count(*),sum(played_at>=?) FROM history GROUP BY user_id',(now-86400,))}
        requests={r[0]:r[1] for r in accounts.db.execute('SELECT user_id,count(*) FROM music_requests GROUP BY user_id')}
        apple={};apple_history={}
        if accounts.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='apple_party_playback'").fetchone():
            for r in accounts.db.execute('SELECT user_id,payload FROM apple_party_playback'):
                d=json.loads(r[1]);t=None
                if d.get('party_id') and d.get('entry_id'):
                    row=accounts.db.execute('SELECT metadata FROM party_entries WHERE id=? AND party_id=?',(d['entry_id'],d['party_id'])).fetchone()
                    if row:t=json.loads(row[0])
                apple[r[0]]=(d,t)
            apple_history={r[0]:(r[1],r[2]) for r in accounts.db.execute('SELECT user_id,count(*),sum(started>=?) FROM apple_music_history GROUP BY user_id',(now-86400,))}
    with library.index_lock:tracks=library.tracks
    transfers=streams.snapshot() if streams else {'items':[],'started':0,'finished':0}
    items=[]
    for row in rows:
        u=dict(row)
        try:state=json.loads(u.pop('player_state'));status=json.loads(u.pop('player_status'))
        except (ValueError,TypeError):state={};status={}
        if not isinstance(state,dict):state={}
        if not isinstance(status,dict):status={}
        d=state.get('listening',{})
        if not isinstance(d,dict):d={}
        buffer=None;buffer_at=0
        # Never let an old device heartbeat revive a replaced owner/track/intent.
        if status.get('owner')==d.get('owner') and status.get('epoch')==d.get('epoch'):
            seen=max(d.get('seen',0),status.get('seen',0))
            if status.get('intent')==d.get('intent') and status.get('current')==state.get('current'):
                buffer=status.get('buffer');buffer_at=status.get('buffer_at',0)
                d={**d,**{k:status[k] for k in ('observed','ack','quality','output') if k in status}}
        else:seen=d.get('seen',0)
        player_online=bool(d.get('owner') and 0<=now-seen<20)
        online=0<=now-u['last_seen']<120 or player_online
        observed=d.get('observed','paused')
        playback='offline' if not player_online else observed if observed in ('playing','paused','buffering','needs-gesture') else 'paused'
        if playback=='playing' and d.get('ack',0)!=d.get('intent',0):playback='buffering'
        track=tracks.get(state.get('current')) if player_online else None
        plays,day=history.get(u['id'],(0,0))
        u.update(audio_requests=sum(t['user_id']==u['id'] for t in transfers['items']),online=online,playback=playback,device=d.get('device_name',''),buffer=buffer,buffer_at=buffer_at,player_seen=seen,
            output=d.get('output','') if player_online else '',quality=d.get('quality','') if player_online else '',
            track={k:track.get(k) for k in ('id','title','artist')} if track else None,
            queue_count=len(state.get('upcoming',[])) if player_online else 0,
            tracks_started=plays,tracks_started_24h=day,requests=requests.get(u['id'],0))
        extra,extra_day=apple_history.get(u['id'],(0,0));u['tracks_started']+=extra;u['tracks_started_24h']+=extra_day
        apple_status,apple_track=apple.get(u['id'],({},None))
        if apple_status.get('party_id') and apple_status.get('device_id') and 0<=now-apple_status.get('seen',0)<20:
            with accounts.lock:queued=accounts.db.execute("SELECT count(*) FROM party_entries WHERE party_id=? AND status='queued'",(apple_status['party_id'],)).fetchone()[0]
            observed=apple_status.get('observed','paused')
            u.update(buffer=None,buffer_at=0,online=True,playback=observed if observed in ('playing','paused','buffering','needs-gesture') else 'paused',device=apple_status.get('device_name',''),track={k:apple_track.get(k) for k in ('id','title','artist')} if apple_track else None,quality='Apple Music',output='',queue_count=queued)
        items.append(u)
    items.sort(key=lambda u:(not u['online'],u['playback']!='playing',-u['last_seen'],u['id']))
    return {'items':items,'transfers':transfers,'server_time':now,'online_window_seconds':120,
        'summary':{'audio_requests':len(transfers['items']),'buffering':sum(u['playback']=='buffering' for u in items),'online':sum(u['online'] for u in items),'playing':sum(u['playback']=='playing' for u in items),
        'signed_in':len(items),'sessions':sum(u['sessions'] for u in items),'tracks_started_24h':sum(u['tracks_started_24h'] for u in items)}}


class ActiveStreams:
    """Bounded by HTTP admission; transient counters contain no credentials."""
    def __init__(self,now=time.time):
        import threading
        self.lock=threading.Lock();self.items={};self.sequence=0;self.started=0;self.completed=0;self.now=now
    def open(self,uid,tid,profile):
        with self.lock:
            self.sequence+=1;key=self.sequence;self.started+=1
            self.items[key]={'user_id':uid,'track_id':tid,'profile':profile,'started':self.now(),'phase':'preparing'}
            return key
    def sending(self,key):
        with self.lock:
            if key in self.items:self.items[key]['phase']='sending'
    def close(self,key):
        with self.lock:
            if self.items.pop(key,None) is not None:self.completed+=1
    def snapshot(self):
        with self.lock:return {'items':[dict(x) for x in self.items.values()],'started':self.started,'finished':self.completed}

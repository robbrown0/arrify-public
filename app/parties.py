"""Account-owned parties, scoped guest capabilities and durable queue credits."""
import hashlib
import hmac
import json
import secrets
import time
import threading
import logging
import os
import ipaddress
from urllib.parse import urlparse
from .radio import recording_key
from ._qrcodegen import QrCode
from .party_sessions import Invitations,PartyGone
from .guest_wifi import GuestWiFi


def song_key(track):
    return '|'.join(recording_key(track))


def qr_svg(value):
    qr=QrCode.encode_text(value,QrCode.Ecc.MEDIUM);size=qr.get_size();border=4
    path=' '.join(f'M{x+border},{y+border}h1v1h-1z' for y in range(size) for x in range(size) if qr.get_module(x,y))
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size+8} {size+8}" role="img" aria-label="Scan to join the party"><rect width="100%" height="100%" fill="white"/><path d="{path}" fill="black"/></svg>'.encode()


class Parties:
    def __init__(self,accounts,player,events=None,origin='',apple=None,spotify=None):
        self.a,self.p,self.lib,self.events,self.origin=accounts,player,player.lib,events,(os.environ.get('ARRIFY_PARTY_PUBLIC_ORIGIN') or origin).rstrip('/')
        self.wifi=GuestWiFi(accounts)
        parsed=urlparse(self.origin)
        if parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:raise ValueError('Configure a valid PartyQueue public origin without a path or credentials.')
        if parsed.scheme=='http':
            host=parsed.hostname
            try:local=ipaddress.ip_address(host).is_private
            except ValueError:local='.' not in host or host.endswith('.local')
            if not local:raise ValueError('Remote PartyQueue invitations require HTTPS. LAN-only HTTP remains available.')
        self.apple=apple;self.spotify=spotify
        self.invites=Invitations(accounts.directory)
        with self.a.lock:
            self.a.db.executescript('''
            CREATE TABLE IF NOT EXISTS parties(id TEXT PRIMARY KEY,owner INTEGER NOT NULL,name TEXT NOT NULL,join_token TEXT UNIQUE NOT NULL,display_token TEXT UNIQUE NOT NULL,created INTEGER NOT NULL,open INTEGER NOT NULL DEFAULT 1,ended INTEGER NOT NULL DEFAULT 0,slot_limit INTEGER NOT NULL DEFAULT 3,refill INTEGER NOT NULL DEFAULT 2,revision INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS party_guests(id TEXT PRIMARY KEY,party_id TEXT NOT NULL,name TEXT NOT NULL,digest TEXT UNIQUE NOT NULL,csrf TEXT NOT NULL,created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS party_entries(id INTEGER PRIMARY KEY,party_id TEXT NOT NULL,guest_id TEXT,track_id INTEGER NOT NULL,song_key TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',position INTEGER NOT NULL,charged INTEGER NOT NULL DEFAULT 1,created INTEGER NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS party_pending_song ON party_entries(party_id,song_key) WHERE status IN ('queued','playing');
            CREATE INDEX IF NOT EXISTS party_entry_order ON party_entries(party_id,status,position,id);
            CREATE TABLE IF NOT EXISTS party_acquisitions(id INTEGER PRIMARY KEY,party_id TEXT NOT NULL,guest_id TEXT NOT NULL,selection TEXT NOT NULL,request_id TEXT,metadata TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'submitting',created INTEGER NOT NULL,UNIQUE(party_id,selection));
            CREATE INDEX IF NOT EXISTS party_acquisition_pending ON party_acquisitions(state,party_id);
            CREATE TABLE IF NOT EXISTS party_session_ids(id TEXT PRIMARY KEY,owner INTEGER NOT NULL,join_digest TEXT UNIQUE NOT NULL,display_digest TEXT UNIQUE NOT NULL,state TEXT NOT NULL,updated INTEGER NOT NULL);
            ''')
        with self.a.lock:
            for table,columns in [('parties',[('source',"TEXT NOT NULL DEFAULT 'arrify'"),('storefront',"TEXT NOT NULL DEFAULT ''"),('invitation_version','INTEGER NOT NULL DEFAULT 0')]),('party_entries',[('catalog_id',"TEXT NOT NULL DEFAULT ''"),('metadata',"TEXT NOT NULL DEFAULT '{}'")])]:
                existing={r[1] for r in self.a.db.execute('PRAGMA table_info('+table+')')}
                for name,definition in columns:
                    if name not in existing:self.a.db.execute('ALTER TABLE '+table+' ADD COLUMN '+name+' '+definition)
        from .apple_party_playback import ApplePartyPlayback
        self.apple_playback=ApplePartyPlayback(self)
        self.spotify_playback=ApplePartyPlayback(self,provider='spotify')
        self.p.parties=self
        # Legacy invitations cannot safely be recovered from hashes. Expire rather
        # than silently reusing them; retain the host's history and settings.
        with self.a.transaction():
            for row in self.a.db.execute('SELECT * FROM parties WHERE invitation_version=0').fetchall():
                p=dict(row);self._end(p)
                self.a.db.execute('UPDATE parties SET join_token=?,display_token=?,invitation_version=1 WHERE id=?',(self.invites.digest(p['join_token']),self.invites.digest(p['display_token']),p['id']))
            for row in self.a.db.execute('SELECT * FROM parties WHERE ended=0').fetchall():
                p=dict(row)
                if not hmac.compare_digest(p['join_token'],self.invites.digest(self.invites.token(p['id'],'join'))):self._end(p)
            self.a.db.execute("INSERT OR IGNORE INTO party_session_ids SELECT id,owner,join_token,display_token,CASE WHEN ended=1 THEN 'ended' ELSE 'active' END,created FROM parties")

    def row(self,pid=None,uid=None,token=None,display=False):
        if token is not None:
            if not isinstance(token,str) or len(token)>80:raise ValueError('Party not found.')
            digest=self.invites.digest(token)
            row=self.a.db.execute('SELECT p.* FROM parties p JOIN users u ON u.id=p.owner WHERE p.'+('display_token' if display else 'join_token')+'=? AND u.approved=1 AND u.frozen=0 AND u.deleted_at=0',(digest,)).fetchone()
            if not row:
                gone=self.a.db.execute('SELECT state FROM party_session_ids WHERE '+('display_digest' if display else 'join_digest')+"=? AND state='deleted'",(digest,)).fetchone()
                if gone:raise PartyGone('deleted')
        else:row=self.a.db.execute('SELECT * FROM parties WHERE id=? AND owner=?',(pid,uid)).fetchone()
        if not row:raise ValueError('Party not found.')
        return dict(row)

    def notify(self,uid):
        if self.events:self.events.publish(uid)

    def guest(self,party,secret,csrf=None):
        live=self.a.db.execute('SELECT ended FROM parties WHERE id=?',(party['id'],)).fetchone()
        if not live or live['ended']:raise PartyGone('ended')
        digest=hashlib.sha256(str(secret).encode()).hexdigest()
        row=self.a.db.execute('SELECT * FROM party_guests WHERE party_id=? AND digest=?',(party['id'],digest)).fetchone()
        if not row:raise PermissionError('Join this party first.')
        if csrf is not None and not hmac.compare_digest(str(csrf),row['csrf']):raise PermissionError('Refresh the party page and try again.')
        return dict(row)

    def join(self,token,name,secret=''):
        name=str(name).strip()
        if not 1<=len(name)<=50 or any(ord(c)<32 for c in name):raise ValueError('Enter a name between 1 and 50 characters.')
        with self.a.transaction():
            party=self.row(token=token)
            if party['ended']:raise PartyGone('ended')
            if not party['open']:raise ValueError('The host has closed requests.')
            try:g=self.guest(party,secret);return party,g,None
            except PermissionError:pass
            count=self.a.db.execute('SELECT count(*) FROM party_guests WHERE party_id=?',(party['id'],)).fetchone()[0]
            if count>=500:raise ValueError('This party has reached its guest limit.')
            secret=secrets.token_urlsafe(32);gid=secrets.token_hex(16);csrf=secrets.token_urlsafe(24)
            self.a.db.execute('INSERT INTO party_guests VALUES(?,?,?,?,?,?)',(gid,party['id'],name,hashlib.sha256(secret.encode()).hexdigest(),csrf,int(time.time())))
            self.touch(party['id']);self.notify(party['owner'])
            return party,self.guest(party,secret),secret

    def guest_url(self,p):return self.origin+'/party/join#'+self.invites.token(p['id'],'join')

    def terminal(self,p):return {'id':p['id'],'name':p.get('name',''),'state':'ended' if p.get('ended') else 'deleted','ended':True,'open':False,'revision':p.get('revision',0),'items':[],'total':0,'playing':False}

    def _end(self,p):
        pid,uid=p['id'],p['owner']
        self.apple_playback.stop(uid,pid)
        self.spotify_playback.stop(uid,pid)
        self.a.db.execute('UPDATE parties SET ended=1,open=0,revision=revision+1 WHERE id=?',(pid,))
        self.a.db.execute("UPDATE party_session_ids SET state='ended',updated=? WHERE id=?",(int(time.time()),pid))
        self.a.db.execute('DELETE FROM party_guests WHERE party_id=?',(pid,))
        self.a.db.execute("UPDATE party_entries SET status=CASE WHEN status='playing' THEN 'skipped' ELSE 'removed' END WHERE party_id=? AND status IN ('playing','queued')",(pid,))
        s=self.p.load(uid)
        if s.get('party_id')==pid:
            s.pop('party_id',None);s['upcoming']=[]
            if s.get('listening'):
                d=s['listening'];d.update(action='pause',playing=False,observed='paused',intent=d['intent']+1,revision=d['revision']+1)
            self.p.save(uid,s)

    def delete(self,uid,pid,confirmed=False):
        with self.a.transaction():
            record=self.a.db.execute('SELECT * FROM party_session_ids WHERE id=? AND owner=?',(pid,uid)).fetchone()
            if record and record['state']=='deleted':return {'id':pid,'state':'deleted','deleted':True}
            p=self.row(pid,uid)
            if not p['ended'] and confirmed is not True:raise ValueError('Confirm ending and deleting this active party.')
            if not p['ended']:self._end(p)
            self.wifi.delete_party(pid)
            for table in ('party_entries','party_guests','party_acquisitions','apple_music_history','spotify_music_history'):
                self.a.db.execute('DELETE FROM '+table+' WHERE party_id=?',(pid,))
            self.a.db.execute('DELETE FROM parties WHERE id=? AND owner=?',(pid,uid))
            self.a.db.execute("UPDATE party_session_ids SET state='deleted',updated=? WHERE id=?",(int(time.time()),pid))
        self.notify(uid);return {'id':pid,'state':'deleted','deleted':True}

    def quota(self,party,gid):
        r=self.a.db.execute("SELECT count(*) issued,coalesce(sum(status IN ('played','skipped','removed')),0) returned,coalesce(sum(status IN ('queued','playing')),0) pending FROM party_entries WHERE party_id=? AND guest_id=? AND charged=1",(party['id'],gid)).fetchone()
        available=max(0,min(party['slot_limit']-r['pending'],party['slot_limit']+(r['returned']//party['refill'])*party['refill']-r['issued']))
        return {'available':available,'pending':r['pending'],'limit':party['slot_limit'],'refill':party['refill']}

    def track(self,tid):
        t=self.lib.track(int(tid))
        return {k:t.get(k,'') for k in ('id','title','artist','album')}

    def touch(self,pid):self.a.db.execute('UPDATE parties SET revision=revision+1 WHERE id=?',(pid,))

    def entries(self,pid):return self.a.db.execute("SELECT e.*,g.name guest FROM party_entries e LEFT JOIN party_guests g ON g.id=e.guest_id WHERE e.party_id=? AND e.status IN ('queued','playing') ORDER BY e.status='playing' DESC,e.position,e.id",(pid,)).fetchall()

    def _add(self,party,tid,gid=None,charged=True):
        if party.get('source','arrify')!='arrify':raise ValueError('Search within this party to add songs from its music service.')
        track=self.lib.track(int(tid));key=song_key(track)
        return self._insert(party,int(tid),key,gid,charged)

    def add_apple(self,uid,pid,track,token=None,secret='',csrf=''):
        return self.add_service(uid,pid,track,'apple',token,secret,csrf)

    def add_service(self,uid,pid,track,provider,token=None,secret='',csrf=''):
        if provider not in ('apple','spotify'):raise ValueError('Choose a music service.')
        with self.a.transaction():
            p=self.row(token=token) if token else self.row(pid,uid)
            if p['source']!=provider:raise ValueError('That song belongs to a different music source.')
            gid=None
            if token:
                gid=self.guest(p,secret,csrf)['id']
                if not p['open']:raise ValueError('The host has closed requests.')
            if p['ended']:raise ValueError('This party has ended.')
            key=provider+':'+(track.get('isrc') or track['id'])
            self._insert(p,0,key,gid,True,track)
            result=self.view(p,gid,host=not bool(token))
        self.notify(p['owner']);return result

    def _insert(self,party,tid,key,gid=None,charged=True,metadata=None,sync=True):
        if self.a.db.execute("SELECT 1 FROM party_entries WHERE party_id=? AND song_key=? AND status IN ('queued','playing')",(party['id'],key)).fetchone():raise ValueError('Already in queue.')
        if self.a.db.execute("SELECT count(*) FROM party_entries WHERE party_id=? AND status IN ('queued','playing')",(party['id'],)).fetchone()[0]>=500:raise ValueError('The party queue is full.')
        if gid and charged and not self.quota(party,gid)['available']:raise ValueError('Your requests are queued. Wait for more of your songs to play.')
        pos=self.a.db.execute('SELECT coalesce(max(position),0)+1 FROM party_entries WHERE party_id=?',(party['id'],)).fetchone()[0]
        entry=self.a.db.execute('INSERT INTO party_entries(party_id,guest_id,track_id,song_key,position,charged,created,catalog_id,metadata) VALUES(?,?,?,?,?,?,?,?,?)',(party['id'],gid,int(tid),key,pos,int(charged and bool(gid)),int(time.time()),metadata['id'] if metadata else '',json.dumps(metadata or {}))).lastrowid
        if sync:self.touch(party['id']);self.sync(party)
        return entry

    def sync(self,party):
        if party.get('source','arrify') in ('apple','spotify'):
            getattr(self,party['source']+'_playback').queue_changed(party);return
        s=self.p.load(party['owner'])
        if s.get('party_id')!=party['id']:return
        rows=self.entries(party['id']);s['upcoming']=[r['track_id'] for r in rows if r['status']=='queued']
        d=s.get('listening')
        if not s['current'] and s['upcoming'] and d and d.get('owner'):
            s['current']=s['upcoming'].pop(0);s['position']=0
            self.a.db.execute("UPDATE party_entries SET status='playing' WHERE party_id=? AND track_id=? AND status='queued'",(party['id'],s['current']))
            d.update(action='play',playing=True,intent=d['intent']+1,play_id=d.get('play_id',0)+1,duration=0,quality='',seek_target=0,seek_intent=d.get('seek_intent',0)+1)
        if d:d['revision']+=1
        self.p.save(party['owner'],s)

    def reconcile(self,party):
        if party.get('source','arrify')!='arrify':return
        # Acquisition is separate from queue credits. Only playable, verified arrivals join.
        if not self.a.db.execute("SELECT 1 FROM party_acquisitions WHERE party_id=? AND state='requested'",(party['id'],)).fetchone():return
        rows=self.a.db.execute("SELECT x.*,p.payload FROM party_acquisitions x LEFT JOIN request_progress p ON p.user_id=? AND p.request_id=x.request_id WHERE x.party_id=? AND x.state='requested'",(party['owner'],party['id'])).fetchall()
        for r in rows:
            data=json.loads(r['payload'] or '{}');tid=data.get('track_id')
            if data.get('state')!='available' or not tid or party['ended']:continue
            try:self._add(party,tid,r['guest_id'],charged=False)
            except ValueError as exc:
                if str(exc)!='Already in queue.':continue
            self.a.db.execute("UPDATE party_acquisitions SET state='queued' WHERE id=?",(r['id'],))
            self.touch(party['id']);self.notify(party['owner'])

    def refresh_arrivals(self):
        with self.a.transaction():
            rows=self.a.db.execute("SELECT DISTINCT p.* FROM parties p JOIN party_acquisitions x ON x.party_id=p.id JOIN users u ON u.id=p.owner WHERE p.ended=0 AND x.state='requested' AND u.approved=1 AND u.frozen=0 AND u.deleted_at=0").fetchall()
            for row in rows:self.reconcile(dict(row))

    def background(self):
        pause=threading.Event()
        while True:
            try:self.refresh_arrivals()
            except Exception:logging.exception('Party arrivals refresh deferred')
            pause.wait(10)

    def view(self,party,gid=None,host=False,display=False):
        fresh=self.a.db.execute('SELECT * FROM parties WHERE id=?',(party['id'],)).fetchone()
        if not fresh:raise PartyGone('deleted')
        party=dict(fresh)
        if party['ended']:return self.terminal(party) | ({'source':party['source'],'slot_limit':party['slot_limit'],'refill':party['refill'],'active':False,'pending_music':[]} if host else {})
        self.reconcile(party)
        rows=self.entries(party['id']);items=[]
        for r in rows:
            try:t=json.loads(r['metadata']) if party.get('source','arrify')!='arrify' else self.track(r['track_id'])
            except ValueError:continue
            t.update(entry_id=r['id'],status=r['status'],mine=bool(gid and r['guest_id']==gid))
            if display:t['requested_by']=('Requested by '+(r['guest'] or 'Guest')) if r['guest_id'] else 'Added by host'
            if host:t.update(guest=r['guest'] or 'Host',guest_id=r['guest_id'],acquired=not bool(r['charged']))
            items.append(t)
        s=self.p.load(party['owner']);active=s.get('party_id')==party['id']
        result={k:party[k] for k in ('id','name','open','ended','slot_limit','refill','revision')};result.update(items=items if host else items[:11 if display else 30],total=len(items),active=active,playing=active and bool(s.get('listening',{}).get('playing')))
        self.add_wifi(result,party,host=host,display=display)
        result['source']=party.get('source','arrify')
        if result['source'] in ('apple','spotify'):result.update(getattr(self,result['source']+'_playback').view(party,host=host))
        if gid:
            result['quota']=self.quota(party,gid)
            result['requests']=[{'id':r['id'],'state':r['state'],**json.loads(r['metadata'])} for r in self.a.db.execute('SELECT id,state,metadata FROM party_acquisitions WHERE party_id=? AND guest_id=? ORDER BY id DESC LIMIT 30',(party['id'],gid))]
        if host:
            result['pending_music']=[{'title':json.loads(r['metadata']).get('title','Request in progress'),'artist':json.loads(r['metadata']).get('artist',''),'guest':r['guest'],'state':r['state']} for r in self.a.db.execute("SELECT x.metadata,x.state,g.name guest FROM party_acquisitions x JOIN party_guests g ON g.id=x.guest_id WHERE x.party_id=? AND x.state!='queued' ORDER BY x.created DESC LIMIT 50",(party['id'],))]
            result.update(guest_url=self.guest_url(party),display_url=self.origin+'/party-display#'+self.invites.token(party['id'],'display'),qr_url='/api/v1/parties/'+party['id']+'/qr.svg')
        elif display:result.update(guest_url=self.guest_url(party),qr_url='/api/v1/party-display/'+self.invites.token(party['id'],'display')+'/qr.svg')
        result['state']='active';result['guest_count']=self.a.db.execute('SELECT count(*) FROM party_guests WHERE party_id=?',(party['id'],)).fetchone()[0]
        if display:
            from .listening import Listening
            listening=Listening(self.p);native=listening.load(party['owner']);d=native.get('listening',{});now=time.time()
            if party['source'] in ('apple','spotify'):
                transport=getattr(self,party['source']+'_playback');d=transport.load(party['owner']);position=d.get('position',0);online=transport.online(d) and d.get('party_id')==party['id'];playing=online and d.get('observed')=='playing'
            else:
                position=native.get('position',0);online=active and 0<=now-d.get('seen',0)<20;playing=online and d.get('observed')=='playing'
            result['playing']=bool(playing);result['playback']={'position':position,'duration':d.get('duration',0) if online else 0,'observed_at':d.get('seen',now),'server_time':now,'playing':bool(playing),'online':bool(online)}
            for t in items:
                if party['source']=='arrify':t['artwork_url']='/api/v1/party-display/'+self.invites.token(party['id'],'display')+'/artwork/'+str(t['entry_id'])
        return result

    def add_wifi(self,result,party,host=False,display=False):
        settings=self.wifi.settings(party['id']);profile=settings['profile']
        if host:result['wifi_settings']=settings
        if profile:
            token=self.invites.token(party['id'],'display' if display else 'join')
            result['wifi']={**profile,'include_print':settings['include_print'],'include_display':settings['include_display'],'qr_url':'/api/v1/party-'+('display' if display else 'public')+'/'+token+'/wifi.svg'}

    def list(self,uid):
        with self.a.transaction():return [self.view(dict(r),host=True) for r in self.a.db.execute('SELECT * FROM parties WHERE owner=? ORDER BY created DESC,rowid DESC LIMIT 100',(uid,)).fetchall()]

    def host(self,uid,pid=None,data=None):
        with self.a.transaction():
            if data is not None and pid is None:
                name=str(data.get('name','')).strip()
                if not 1<=len(name)<=100:raise ValueError('Give your party a name (up to 100 characters).')
                if self.a.db.execute('SELECT count(*) FROM parties WHERE owner=? AND ended=0',(uid,)).fetchone()[0]>=10:raise ValueError('End an existing party before creating another.')
                source=data.get('source','arrify')
                if source not in ('arrify','apple','spotify'):raise ValueError('Choose Arrify library, Apple Music, or Spotify.')
                storefront=''
                if source=='apple':
                    if not self.apple:raise ValueError('Apple Music setup is not complete.')
                    storefront=self.apple.storefront(uid)
                elif source=='spotify':
                    if not self.spotify:raise ValueError('Spotify setup is not complete.')
                    storefront=self.spotify.storefront(uid)
                pid=secrets.token_hex(16)
                while self.a.db.execute('SELECT 1 FROM party_session_ids WHERE id=?',(pid,)).fetchone():pid=secrets.token_hex(16)
                join_digest=self.invites.digest(self.invites.token(pid,'join'));display_digest=self.invites.digest(self.invites.token(pid,'display'));now=int(time.time())
                self.a.db.execute('INSERT INTO parties(id,owner,name,join_token,display_token,created,source,storefront,invitation_version) VALUES(?,?,?,?,?,?,?,?,1)',(pid,uid,name,join_digest,display_digest,now,source,storefront))
                self.a.db.execute("INSERT INTO party_session_ids VALUES(?,?,?,?,?,?)",(pid,uid,join_digest,display_digest,'active',now))
            p=self.row(pid,uid)
            if data is not None:
                if data.get('source',p['source'])!=p['source']:raise ValueError('A party’s music source cannot be changed. Create a new party instead.')
                action=data.get('action','')
                if p['ended'] and action=='end':return self.view(p,host=True)
                if p['ended'] and action:raise ValueError('This party has ended.')
                if action=='settings':
                    limit,refill=data.get('slot_limit',p['slot_limit']),data.get('refill',p['refill'])
                    if type(limit) is not int or type(refill) is not int or not 1<=refill<=limit<=20:raise ValueError('Choose 1–20 slots and a refill no greater than the slot limit.')
                    name=str(data.get('name',p['name'])).strip()
                    if not 1<=len(name)<=100:raise ValueError('Give your party a name.')
                    self.a.db.execute('UPDATE parties SET name=?,slot_limit=?,refill=? WHERE id=?',(name,limit,refill,pid))
                elif action=='toggle':self.a.db.execute('UPDATE parties SET open=1-open WHERE id=?',(pid,))
                elif action=='end':self._end(p)
                elif action=='add':self._add(p,data.get('track_id'))
                elif action in ('remove','move','play_next'):
                    r=self.a.db.execute("SELECT * FROM party_entries WHERE id=? AND party_id=? AND status='queued'",(int(data.get('entry_id',0)),pid)).fetchone()
                    if not r:raise ValueError('That queue entry has changed. Refresh and try again.')
                    if action=='remove':self.a.db.execute("UPDATE party_entries SET status='removed' WHERE id=?",(r['id'],))
                    else:
                        rows=[dict(x) for x in self.entries(pid) if x['status']=='queued'];src=next(i for i,x in enumerate(rows) if x['id']==r['id']);dst=0 if action=='play_next' else src+int(data.get('direction',0))
                        if not 0<=dst<len(rows):raise ValueError('Already at the edge of the queue.')
                        rows.insert(dst,rows.pop(src))
                        for i,x in enumerate(rows):self.a.db.execute('UPDATE party_entries SET position=? WHERE id=?',(i+1,x['id']))
                    self.sync(p)
                elif action:raise ValueError('Unknown party action.')
                self.touch(pid);p=self.row(pid,uid)
            result=self.view(p,host=True)
        if data is not None:self.notify(uid)
        return result

    def add(self,token,secret,csrf,tid):
        with self.a.transaction():
            p=self.row(token=token);g=self.guest(p,secret,csrf)
            if p['ended'] or not p['open']:raise ValueError('The host has closed requests.')
            self._add(p,tid,g['id']);result=self.view(p,g['id'])
        self.notify(p['owner']);return result

    def request(self,token,secret,csrf,selection,submissions,cap=0):
        # Never hold the accounts transaction across companion network calls.
        if not isinstance(selection,str) or len(selection)>80:raise ValueError('Choose a catalog result first.')
        with self.a.transaction():
            p=self.row(token=token);g=self.guest(p,secret,csrf)
            if p.get('source','arrify')!='arrify':raise ValueError('Music service parties search their provider directly; downloads are not requested.')
            if p['ended'] or not p['open']:raise ValueError('The host has closed requests.')
            old=self.a.db.execute('SELECT state,created FROM party_acquisitions WHERE party_id=? AND selection=?',(p['id'],selection)).fetchone()
            if old and old['state']!='submitting':return {'message':'Already requested. It will join when available.'}
            if old and time.time()-old['created']<120:raise ValueError('This request is still being submitted. Check back in a moment.')
            self.a.rate_limit(('party-acquisition',p['id']),30,3600);self.a.rate_limit(('guest-acquisition',g['id']),6,900)
            if old:self.a.db.execute('UPDATE party_acquisitions SET created=? WHERE party_id=? AND selection=?',(int(time.time()),p['id'],selection))
            else:self.a.db.execute('INSERT INTO party_acquisitions(party_id,guest_id,selection,metadata,created) VALUES(?,?,?,?,?)',(p['id'],g['id'],selection,'{}',int(time.time())))
        try:result=submissions.create(p['owner'],selection,cap)
        except Exception:
            with self.a.transaction():self.a.db.execute("DELETE FROM party_acquisitions WHERE party_id=? AND selection=? AND state='submitting'",(p['id'],selection))
            raise
        with self.a.transaction():
            live=self.row(p['id'],p['owner']);self.guest(live,secret,csrf)
            metadata={k:result.get(k,'') for k in ('title','artist','album')}
            self.a.db.execute("UPDATE party_acquisitions SET request_id=?,metadata=?,state='requested' WHERE party_id=? AND selection=?",(result['id'],json.dumps(metadata),p['id'],selection));self.touch(p['id'])
        return {'message':'Requested. It will join when available, without using a queue slot.'}

    def before_command(self,uid,data,s):
        action=data.get('action');pid=s.get('party_id')
        if action=='play' and data.get('party_id'):
            party=self.row(str(data['party_id']),uid)
            if party['ended']:raise ValueError('This party has ended.')
            if party.get('source','arrify')!='arrify':raise ValueError('Use this party’s music service controls.')
            rows=self.entries(party['id'])
            if not rows:raise ValueError('Add a song to the party first.')
            data=dict(data,ids=[r['track_id'] for r in rows],track_id=rows[0]['track_id'],channel=None,collection=None,radio=None,browse=None)
        if pid:
            # Standard queue controls also edit the party; guest credits cannot be bypassed.
            party=self.row(pid,uid)
            if party['ended']:raise PartyGone('ended')
            rows=self.entries(pid);queued=[r for r in rows if r['status']=='queued']
            if action in ('previous','shuffle'):raise ValueError('Manage the order from PartyQueue while hosting.')
            if action=='queue':
                for tid in data.get('ids',[]):self._add(party,tid)
                s.update(self.p.load(uid));data=dict(data,ids=[])
            if action in ('remove','reorder','clear'):
                if action=='clear':
                    self.a.db.execute("UPDATE party_entries SET status='removed' WHERE party_id=? AND status='queued'",(pid,))
                elif action=='remove':
                    ix=int(data.get('index',-1))
                    if not 0<=ix<len(queued):raise ValueError('Queue entry changed.')
                    self.a.db.execute("UPDATE party_entries SET status='removed' WHERE id=?",(queued[ix]['id'],))
                else:
                    src,dst=int(data.get('from',-1)),int(data.get('to',-1))
                    if not 0<=src<len(queued) or not 0<=dst<len(queued):raise ValueError('Queue entry changed.')
                    queued.insert(dst,queued.pop(src))
                    for i,r in enumerate(queued):self.a.db.execute('UPDATE party_entries SET position=? WHERE id=?',(i+1,r['id']))
                self.touch(pid)
            if action in ('next','play'):
                self.a.db.execute("UPDATE party_entries SET status=? WHERE party_id=? AND status='playing'",('played' if action=='next' and data.get('automatic') else 'skipped',pid));self.touch(pid)
                if action=='play':s.pop('party_id',None)
        return data

    def after_command(self,uid,data,s):
        if data.get('action')=='play' and data.get('party_id'):s['party_id']=str(data['party_id'])
        pid=s.get('party_id')
        if pid and data.get('action') in ('play','next'):
            self.a.db.execute("UPDATE party_entries SET status='playing' WHERE party_id=? AND track_id=? AND status='queued'",(pid,s['current']));self.touch(pid)

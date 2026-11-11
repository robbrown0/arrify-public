"""Optional per-account Spotify OAuth/PKCE and bounded catalog access.

Only Spotify receives passwords. Tokens are encrypted at rest with OpenSSL's
salted AES-256-CBC/PBKDF2 and authenticated before decryption with HMAC-SHA256.
The independent high-entropy vault key stays outside the source and database.
"""
import base64
import collections
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


ID=re.compile(r'[A-Za-z0-9]{22}')
SCOPES='streaming user-read-private user-read-email user-read-playback-state user-modify-playback-state playlist-read-private playlist-read-collaborative'


class TokenVault:
    def __init__(self,directory):
        self.path=Path(directory)/'spotify-tokens.key'
        try:
            fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        except FileExistsError:pass
        else:
            with os.fdopen(fd,'wb') as out:out.write(secrets.token_hex(32).encode()+b'\n');out.flush();os.fsync(out.fileno())
            directory_fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(directory_fd)
            finally:os.close(directory_fd)
        key=self.path.read_bytes()
        if self.path.stat().st_mode&0o077 or not re.fullmatch(b'[a-f0-9]{64}\n',key):raise ValueError('Spotify token key requires private permissions and a valid protected key.')
        self.mac=hmac.new(key,b'arrify-spotify-token-auth-v1',hashlib.sha256).digest()
    def crypt(self,value,decrypt=False):
        args=['openssl','enc','-aes-256-cbc','-pbkdf2','-iter','100000','-pass','file:'+str(self.path)]
        if decrypt:args.append('-d')
        try:return subprocess.run(args,input=value,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,check=True,timeout=5).stdout
        except (OSError,subprocess.SubprocessError):raise ValueError('Spotify token storage is unavailable.') from None
    def seal(self,uid,value):
        cipher=self.crypt(json.dumps(value,separators=(',',':')).encode())
        tag=hmac.new(self.mac,b'v1:'+str(uid).encode()+b':'+cipher,hashlib.sha256).digest()
        return base64.b64encode(tag+cipher).decode()
    def open(self,uid,value):
        try:
            raw=base64.b64decode(value,validate=True);tag,cipher=raw[:32],raw[32:]
            expected=hmac.new(self.mac,b'v1:'+str(uid).encode()+b':'+cipher,hashlib.sha256).digest()
            if len(cipher)<32 or not hmac.compare_digest(tag,expected):raise ValueError()
            result=json.loads(self.crypt(cipher,True))
            if not isinstance(result,dict):raise ValueError()
            return result
        except (ValueError,TypeError):raise ValueError('Spotify authorization could not be read. Reconnect Spotify.') from None


class SpotifyNotFound(ValueError):
    pass


class Spotify:
    def __init__(self,accounts,origin,env=None,fetch=None,now=time.time):
        env=os.environ if env is None else env
        self.a,self.origin,self.fetch,self.now=accounts,origin.rstrip('/'),fetch,now
        self.enabled=env.get('ARRIFY_SPOTIFY_ENABLED','0')=='1'
        self.client=env.get('ARRIFY_SPOTIFY_CLIENT_ID','')
        self.redirect=env.get('ARRIFY_SPOTIFY_REDIRECT_URI',self.origin+'/api/v1/spotify/callback')
        self.lock=threading.RLock();self.pending={};self.generations={};self.cache=collections.OrderedDict();self.cooldown=0
        self.slots=threading.BoundedSemaphore(3);self.vault=None;self.vault_error=False
        with self.a.lock:self.a.db.execute('CREATE TABLE IF NOT EXISTS spotify_links(user_id INTEGER PRIMARY KEY,payload TEXT NOT NULL,linked_at INTEGER NOT NULL)')
        if self.ready():
            try:self.vault=TokenVault(self.a.directory)
            except (ValueError,OSError):self.vault_error=True
    def ready(self):
        parsed=urllib.parse.urlparse(self.origin)
        secure=parsed.scheme=='https' or parsed.scheme=='http' and parsed.hostname in ('127.0.0.1','[::1]','::1')
        return self.enabled and not self.vault_error and bool(re.fullmatch('[a-f0-9]{32}',self.client)) and secure and self.redirect==self.origin+'/api/v1/spotify/callback'
    def require(self):
        if not self.ready():raise ValueError('Spotify setup is not complete. Ask an Arrify administrator to configure it.')
    def saved(self,uid):
        with self.a.lock:row=self.a.db.execute('SELECT payload FROM spotify_links WHERE user_id=?',(uid,)).fetchone()
        return self.vault.open(uid,row[0]) if row and self.vault else None
    def config(self,uid):
        try:value=self.saved(uid) if self.ready() else None
        except ValueError:value=None
        return dict(enabled=self.enabled,available=self.ready(),linked=bool(value),display_name=value.get('display_name','') if value else '',premium=value.get('premium') if value else None)
    def save(self,uid,value):
        sealed=self.vault.seal(uid,value)
        with self.a.lock:self.a.db.execute('INSERT INTO spotify_links VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET payload=excluded.payload,linked_at=excluded.linked_at',(uid,sealed,int(self.now())))
    def disconnect(self,uid):
        with self.lock:
            self.generations[uid]=self.generations.get(uid,0)+1
            with self.a.lock:self.a.db.execute('DELETE FROM spotify_links WHERE user_id=?',(uid,))
            self.pending={k:v for k,v in self.pending.items() if v['uid']!=uid}
            self.cache=collections.OrderedDict((k,v) for k,v in self.cache.items() if k[0]!=uid)
        return self.config(uid)
    def begin(self,uid,session):
        self.require();state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(64)
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        with self.lock:
            self.pending={k:v for k,v in self.pending.items() if v['expires']>self.now() and v['uid']!=uid}
            if len(self.pending)>=256:raise ValueError('Spotify sign-in is busy. Try again shortly.')
            self.generations[uid]=self.generations.get(uid,0)+1
            self.pending[hashlib.sha256(state.encode()).digest()]=dict(generation=self.generations[uid],uid=uid,session=hashlib.sha256(session.encode()).digest(),verifier=verifier,expires=self.now()+600)
        return {'url':'https://accounts.spotify.com/authorize?'+urllib.parse.urlencode(dict(client_id=self.client,response_type='code',redirect_uri=self.redirect,scope=SCOPES,state=state,code_challenge_method='S256',code_challenge=challenge,show_dialog='true'))}
    def raw(self,url,method='GET',data=None,token=None,form=False):
        parsed=urllib.parse.urlparse(url)
        if parsed.scheme!='https' or parsed.hostname not in ('accounts.spotify.com','api.spotify.com') or parsed.username or parsed.password or parsed.port not in (None,443):raise ValueError('Invalid Spotify endpoint.')
        if self.now()<self.cooldown:raise ValueError('Spotify is rate limited. Try again shortly.')
        if not self.slots.acquire(blocking=False):raise ValueError('Spotify is busy. Try again shortly.')
        try:
            headers={'Accept':'application/json'}
            if token:headers['Authorization']='Bearer '+token
            if data is not None:
                data=(urllib.parse.urlencode(data) if form else json.dumps(data)).encode();headers['Content-Type']='application/x-www-form-urlencoded' if form else 'application/json'
            if self.fetch:return self.fetch(url,method,data,headers)
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self,*args,**kwargs):return None
            req=urllib.request.Request(url,data=data,headers=headers,method=method)
            with urllib.request.build_opener(NoRedirect).open(req,timeout=15 if '/v1/me/player' in url and method=='PUT' else 8) as response:
                if urllib.parse.urlparse(response.url).hostname!=urllib.parse.urlparse(url).hostname:raise ValueError('Unexpected Spotify response.')
                body=response.read(2*1024*1024+1)
            if len(body)>2*1024*1024:raise ValueError('Spotify response was too large.')
            return json.loads(body) if body.strip() else {}
        except urllib.error.HTTPError as exc:
            if exc.code==429:
                try:delay=max(1,min(300,int(exc.headers.get('Retry-After','30'))))
                except (ValueError,TypeError):delay=30
                self.cooldown=self.now()+delay
                raise ValueError('Spotify is rate limited. Try again shortly.') from None
            if exc.code in (400,401):raise PermissionError('Reconnect Spotify to continue.') from None
            if exc.code==403:raise ValueError('Spotify denied access. Check Premium, the app user allowlist, and playlist access.') from None
            if exc.code==404:raise SpotifyNotFound('This Spotify item or playback device is unavailable.') from None
            raise ValueError('Spotify is temporarily unavailable.') from None
        except (OSError,TimeoutError,json.JSONDecodeError) as error:
            category='player' if '/v1/me/player' in url else 'authorization' if 'accounts.spotify.com' in url else 'catalog'
            logging.warning('Spotify %s %s failed (%s)',method,category,type(error).__name__)
            raise ValueError('Spotify could not be reached. Try again shortly.') from None
        finally:self.slots.release()
    def complete(self,uid,session,state,code,error='',authorize=None):
        self.require()
        if not isinstance(state,str) or not re.fullmatch('[A-Za-z0-9_-]{43}',state):raise PermissionError('Spotify sign-in expired. Try connecting again.')
        with self.lock:pending=self.pending.pop(hashlib.sha256(state.encode()).digest(),None)
        if not pending or pending['uid']!=uid or pending['expires']<self.now() or not hmac.compare_digest(pending['session'],hashlib.sha256(session.encode()).digest()):raise PermissionError('Spotify sign-in expired. Try connecting again.')
        if error:raise ValueError('Spotify connection was cancelled.')
        if not isinstance(code,str) or not 1<=len(code)<=4096:raise ValueError('Spotify did not return an authorization code.')
        tokens=self.raw('https://accounts.spotify.com/api/token','POST',dict(grant_type='authorization_code',code=code,redirect_uri=self.redirect,client_id=self.client,code_verifier=pending['verifier']),form=True)
        if not self.valid_token(tokens.get('access_token')) or not self.valid_token(tokens.get('refresh_token')):raise ValueError('Spotify did not authorize this connection.')
        granted=set(tokens.get('scope','').split())
        if not {'streaming','user-modify-playback-state','user-read-playback-state'}.issubset(granted):raise ValueError('Spotify playback permissions were not granted.')
        profile=self.raw('https://api.spotify.com/v1/me',token=tokens['access_token'])
        if not isinstance(profile.get('id'),str) or not 1<=len(profile['id'])<=128:raise ValueError('Spotify account could not be verified. Connect again.')
        value=dict(access_token=tokens['access_token'],refresh_token=tokens['refresh_token'],expires=self.now()+max(30,min(86400,int(tokens.get('expires_in',3600)))),scope=tokens.get('scope',''),spotify_id=str(profile.get('id',''))[:128],display_name=str(profile.get('display_name') or 'Spotify')[:100],country=str(profile.get('country') or 'from_token'),premium=profile.get('product')=='premium' if profile.get('product') else None)
        # Recheck that the account still exists before storing newly issued tokens.
        with self.lock,self.a.transaction():
            if authorize is not None and not authorize():raise PermissionError('Sign in to Arrify again.')
            if self.generations.get(uid)!=pending['generation']:raise PermissionError('Spotify sign-in was cancelled. Connect again.')
            row=self.a.db.execute('SELECT approved,frozen,deleted_at FROM users WHERE id=?',(uid,)).fetchone()
            if not row or not row['approved'] or row['frozen'] or row['deleted_at']:raise PermissionError('Sign in to Arrify again.')
            self.save(uid,value)
        return self.config(uid)
    @staticmethod
    def valid_token(value):return isinstance(value,str) and 1<=len(value)<=8192 and all(33<=ord(c)<=126 for c in value)
    def token(self,uid):
        self.require()
        with self.lock:
            value=self.saved(uid)
            if not value:raise ValueError('Connect Spotify in Music services first.')
            if value['expires']<self.now()+60:
                tokens=self.raw('https://accounts.spotify.com/api/token','POST',dict(grant_type='refresh_token',refresh_token=value['refresh_token'],client_id=self.client),form=True)
                if not self.valid_token(tokens.get('access_token')) or tokens.get('refresh_token') is not None and not self.valid_token(tokens.get('refresh_token')):raise PermissionError('Reconnect Spotify to continue.')
                value.update(access_token=tokens['access_token'],refresh_token=tokens.get('refresh_token',value['refresh_token']),expires=self.now()+max(30,min(86400,int(tokens.get('expires_in',3600)))))
                self.save(uid,value)
            return {'access_token':value['access_token'],'expires_in':max(0,int(value['expires']-self.now()))}
    def storefront(self,uid):
        self.require();value=self.saved(uid)
        if not value:raise ValueError('Connect Spotify in Music services first.')
        if value.get('premium') is False:raise ValueError('Spotify playback requires Premium.')
        return value['country']
    def request(self,uid,path,method='GET',data=None,cached=False):
        if not path.startswith('/v1/') or any(c in path for c in ('\r','\n','#')):raise ValueError('Invalid Spotify request.')
        key=(uid,path)
        with self.lock:generation=self.generations.get(uid,0)
        if cached:
            with self.lock:
                found=self.cache.get(key)
                if found and self.now()-found[0]<120:self.cache.move_to_end(key);return found[1]
        value=self.raw('https://api.spotify.com'+path,method,data,self.token(uid)['access_token'])
        if cached:
            with self.lock:
                if generation!=self.generations.get(uid,0):raise PermissionError('Spotify was disconnected.')
                self.cache[key]=(self.now(),value);self.cache.move_to_end(key)
                while len(self.cache)>128:self.cache.popitem(last=False)
        return value
    @staticmethod
    def image(images):
        url=str((images or [{}])[0].get('url',''));parsed=urllib.parse.urlparse(url)
        return url if parsed.scheme=='https' and (parsed.hostname or '').endswith('.scdn.co') and not parsed.username and not parsed.password else ''
    @classmethod
    def song(cls,row):
        if not isinstance(row,dict) or not ID.fullmatch(str(row.get('id',''))) or row.get('type','track')!='track' or row.get('is_local') or row.get('is_playable') is False or row.get('restrictions') or not row.get('name') or not row.get('artists'):return None
        return dict(id=row['id'],title=str(row['name'])[:500],artist='; '.join(str(a.get('name','')) for a in row['artists'])[:500],album=str(row.get('album',{}).get('name',''))[:500],artwork=cls.image(row.get('album',{}).get('images')),duration=max(0,min(86400,float(row.get('duration_ms',0))/1000)),isrc=str(row.get('external_ids',{}).get('isrc',''))[:40],provider='spotify',url='https://open.spotify.com/track/'+row['id'])
    def search(self,uid,query):
        query=str(query).strip()
        if not 2<=len(query)<=200:raise ValueError('Enter between 2 and 200 characters.')
        value=self.request(uid,'/v1/search?'+urllib.parse.urlencode(dict(q=query,type='track',limit=10)),cached=True)
        return [t for row in value.get('tracks',{}).get('items',[]) if (t:=self.song(row))]
    def track(self,uid,identifier):
        if not ID.fullmatch(str(identifier)):raise ValueError('Choose a Spotify song.')
        t=self.song(self.request(uid,'/v1/tracks/'+identifier,cached=True))
        if not t:raise ValueError('This song is unavailable in Spotify.')
        return t
    def playlists(self,uid,query='',offset=0,catalog=False):
        offset=max(0,min(10000,int(offset)));query=str(query).strip()[:200]
        match=re.fullmatch(r'(?:https://open\.spotify\.com/(?:intl-[a-z]{2}/)?playlist/|spotify:playlist:)([A-Za-z0-9]{22})(?:\?[^#]*)?',query)
        if match:return {'items':[self.playlist(self.request(uid,'/v1/playlists/'+match[1],cached=True))],'more':False,'next_offset':0}
        if catalog:
            if len(query)<2:return {'items':[],'more':False,'next_offset':0}
            data=self.request(uid,'/v1/search?'+urllib.parse.urlencode(dict(q=query,type='playlist',limit=10,offset=offset)),cached=True).get('playlists',{})
        else:data=self.request(uid,'/v1/me/playlists?'+urllib.parse.urlencode(dict(limit=50,offset=offset)),cached=True)
        rows=[self.playlist(p) for p in data.get('items',[]) if isinstance(p,dict) and ID.fullmatch(str(p.get('id','')))]
        return {'items':rows,'more':bool(data.get('next')),'next_offset':offset+len(data.get('items',[]))}
    def playlist(self,row):
        return dict(id=str(row.get('id','')),kind='spotify',name=str(row.get('name','Playlist'))[:500],owner=str(row.get('owner',{}).get('display_name') or 'Spotify')[:100],count=(row.get('items') or row.get('tracks') or {}).get('total'),artwork=self.image(row.get('images')))
    def playlist_tracks(self,uid,identifier):
        if not ID.fullmatch(str(identifier)):raise ValueError('Choose a Spotify playlist.')
        info=self.request(uid,'/v1/playlists/'+identifier,cached=True);tracks=[];total=0;fetched=0
        for offset in range(0,500,50):
            data=self.request(uid,'/v1/playlists/'+identifier+'/items?'+urllib.parse.urlencode(dict(limit=50,offset=offset)),cached=True);total=int(data.get('total',0))
            for item in data.get('items',[]):
                row=item.get('item') or item.get('track')
                if t:=self.song(row):tracks.append(t)
            fetched+=len(data.get('items',[]))
            if not data.get('next'):break
        return dict(name=str(info.get('name','Playlist'))[:500],tracks=tracks,total=total,truncated=total>fetched)
    def player(self,uid,device,action,track=None,position=0):
        try:return self._player(uid,device,action,track,position)
        except SpotifyNotFound:
            # Device IDs can expire without an SDK not_ready event. Do not retry
            # an old ID indefinitely or mislabel a missing device as a bad song.
            devices=self.request(uid,'/v1/me/player/devices').get('devices',[])
            if not any(d.get('id')==device for d in devices if isinstance(d,dict)):
                raise ValueError('Spotify playback device disconnected. Tap Play to reconnect.') from None
            raise ValueError('Spotify could not start this song. Try Play again or choose Next.') from None
    def _player(self,uid,device,action,track=None,position=0):
        if not re.fullmatch('[A-Za-z0-9_-]{1,128}',str(device)):raise ValueError('Spotify playback device is unavailable.')
        query='?'+urllib.parse.urlencode({'device_id':device})
        if action=='play':
            if not ID.fullmatch(str(track)):raise ValueError('Choose a Spotify song.')
            return self.request(uid,'/v1/me/player/play'+query,'PUT',{'uris':['spotify:track:'+track],'position_ms':max(0,min(86400000,int(position*1000)))})
        if action in ('pause','resume'):return self.request(uid,'/v1/me/player/'+('pause' if action=='pause' else 'play')+query,'PUT',{} if action=='resume' else None)
        if action=='seek':return self.request(uid,'/v1/me/player/seek'+query+'&position_ms='+str(max(0,min(86400000,int(position*1000)))),'PUT')
        raise ValueError('Choose a Spotify playback action.')

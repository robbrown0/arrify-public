"""Optional MusicKit configuration and bounded Apple catalog access. No stored user tokens."""
import base64
import collections
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


def b64(value):return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def jose_signature(der):
    # OpenSSL emits ASN.1 ECDSA integers; ES256 JWT requires fixed-width R || S.
    if len(der)<8 or der[0]!=48 or der[1]!=len(der)-2:raise ValueError('Invalid MusicKit signing key.')
    values=[];at=2
    for _ in range(2):
        if at+2>len(der) or der[at]!=2:raise ValueError('Invalid MusicKit signature.')
        size=der[at+1];at+=2;value=der[at:at+size];at+=size
        if not value or len(value)>33 or value[0]&128:raise ValueError('Invalid MusicKit signature.')
        value=value.lstrip(b'\0')
        if len(value)>32:raise ValueError('Use a P-256 MusicKit key.')
        values.append(value.rjust(32,b'\0'))
    if at!=len(der):raise ValueError('Invalid MusicKit signature.')
    return b''.join(values)


class AppleMusic:
    def __init__(self,accounts,origin,env=None,fetch=None,now=time.time):
        env=os.environ if env is None else env
        self.a,self.origin,self.now,self.fetch=accounts,origin.rstrip('/'),now,fetch
        self.enabled=env.get('ARRIFY_APPLE_MUSIC_ENABLED','0')=='1'
        self.team=env.get('ARRIFY_APPLE_TEAM_ID','');self.kid=env.get('ARRIFY_APPLE_KEY_ID','');self.key=env.get('ARRIFY_APPLE_KEY_FILE','/run/secrets/arrify-musickit.p8')
        self.lock=threading.RLock();self.slots=threading.BoundedSemaphore(3);self.cached_token=None;self.cache=collections.OrderedDict()
        with self.a.lock:self.a.db.execute('CREATE TABLE IF NOT EXISTS apple_music_links(user_id INTEGER PRIMARY KEY,storefront TEXT NOT NULL,linked_at INTEGER NOT NULL)')
    def ready(self):
        return self.enabled and bool(re.fullmatch(r'[A-Z0-9]{10}',self.team) and re.fullmatch(r'[A-Z0-9]{10}',self.kid)) and Path(self.key).is_absolute() and Path(self.key).is_file()
    def require(self):
        if not self.ready():raise ValueError('Apple Music setup is not complete. Arrify library parties are available.')
    def config(self,uid):
        with self.a.lock:row=self.a.db.execute('SELECT storefront,linked_at FROM apple_music_links WHERE user_id=?',(uid,)).fetchone()
        return {'enabled':self.enabled,'available':self.ready(),'linked':bool(row),'storefront':row['storefront'] if row else None,'authorization':'this-device'}
    def token(self):
        self.require()
        with self.lock:
            if self.cached_token and self.cached_token[1]-self.now()>43200:return self.cached_token[0]
            key=Path(self.key);info=key.stat()
            if info.st_mode&0o077 or info.st_size>16384:raise ValueError('MusicKit signing key needs private file permissions.')
            now=int(self.now());header=b64(json.dumps({'alg':'ES256','kid':self.kid},separators=(',',':')).encode());payload=b64(json.dumps({'iss':self.team,'iat':now-30,'exp':now+86400,'origin':[self.origin]},separators=(',',':')).encode());message=(header+'.'+payload).encode()
            try:
                signed=subprocess.run(['openssl','dgst','-sha256','-sign',str(key)],input=message,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=5,check=True).stdout
                token=header+'.'+payload+'.'+b64(jose_signature(signed))
            except (OSError,subprocess.SubprocessError):raise ValueError('MusicKit signing is unavailable.') from None
            self.cached_token=(token,now+86400);return token
    def request(self,path,user_token=None):
        self.require()
        if not self.slots.acquire(blocking=False):raise ValueError('Apple Music search is busy. Try again shortly.')
        try:
            headers={'Authorization':'Bearer '+self.token(),'Origin':self.origin,'Accept':'application/json'}
            if user_token:headers['Music-User-Token']=user_token
            if self.fetch:return self.fetch(path,headers)
            req=urllib.request.Request('https://api.music.apple.com'+path,headers=headers)
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self,*args,**kwargs):return None
            with urllib.request.build_opener(NoRedirect).open(req,timeout=6) as response:
                # Fixed Apple endpoint paths only; never accept a provider next URL.
                if urllib.parse.urlparse(response.url).hostname!='api.music.apple.com':raise ValueError('Unexpected Apple Music response.')
                data=response.read(1024*1024+1)
            if len(data)>1024*1024:raise ValueError('Apple Music response was too large.')
            return json.loads(data)
        except urllib.error.HTTPError as exc:
            if exc.code in (401,403):raise ValueError('Apple Music authorization needs attention. Reconnect your account or check the server setup.') from None
            if exc.code==429:raise ValueError('Apple Music is busy. Try again shortly.') from None
            raise ValueError('Apple Music is temporarily unavailable.') from None
        except (OSError,TimeoutError,json.JSONDecodeError):raise ValueError('Apple Music could not be reached. Try again shortly.') from None
        finally:self.slots.release()
    def link(self,uid,token):
        if not isinstance(token,str) or not 1<=len(token)<=8192 or any(ord(c)<33 or ord(c)>126 for c in token):raise ValueError('Authorize with Apple Music first.')
        data=self.request('/v1/me/storefront',token)
        rows=data.get('data',[]);storefront=rows[0].get('id','') if rows else ''
        if not re.fullmatch('[a-z]{2}',storefront):raise ValueError('Apple Music storefront could not be verified.')
        # The Music User Token is deliberately never persisted or returned.
        with self.a.lock:self.a.db.execute('INSERT INTO apple_music_links VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET storefront=excluded.storefront,linked_at=excluded.linked_at',(uid,storefront,int(self.now())))
        return self.config(uid)
    def disconnect(self,uid):
        with self.a.lock:self.a.db.execute('DELETE FROM apple_music_links WHERE user_id=?',(uid,))
        return self.config(uid)
    def storefront(self,uid):
        self.require()
        with self.a.lock:row=self.a.db.execute('SELECT storefront FROM apple_music_links WHERE user_id=?',(uid,)).fetchone()
        if not row:raise ValueError('Connect Apple Music in your profile first.')
        return row['storefront']
    def cached(self,path):
        with self.lock:
            value=self.cache.get(path)
            if value and self.now()-value[0]<300:self.cache.move_to_end(path);return value[1]
        data=self.request(path)
        with self.lock:
            self.cache[path]=(self.now(),data);self.cache.move_to_end(path)
            while len(self.cache)>256:self.cache.popitem(last=False)
        return data
    @staticmethod
    def song(row):
        attrs=row.get('attributes',{});sid=str(row.get('id',''))
        if not re.fullmatch(r'[0-9]{1,20}',sid) or not attrs.get('playParams') or not attrs.get('name') or not attrs.get('artistName'):return None
        artwork=str(attrs.get('artwork',{}).get('url','')).replace('{w}','512').replace('{h}','512').replace('{f}','jpg')
        parsed=urllib.parse.urlparse(artwork)
        if parsed.scheme!='https' or not (parsed.hostname or '').endswith('.mzstatic.com') or parsed.username or parsed.password:artwork=''
        return {'id':sid,'title':str(attrs['name'])[:500],'artist':str(attrs['artistName'])[:500],'album':str(attrs.get('albumName',''))[:500],'artwork':artwork,'isrc':str(attrs.get('isrc',''))[:40],'duration':max(0,min(86400,float(attrs.get('durationInMillis',0))/1000)),'provider':'apple'}
    def search(self,storefront,query):
        if not re.fullmatch('[a-z]{2}',storefront):raise ValueError('Invalid Apple Music storefront.')
        query=str(query).strip()
        if not 2<=len(query)<=200:raise ValueError('Enter between 2 and 200 characters.')
        path='/v1/catalog/'+storefront+'/search?'+urllib.parse.urlencode({'term':query,'types':'songs','limit':25})
        return [t for row in self.cached(path).get('results',{}).get('songs',{}).get('data',[])[:25] if (t:=self.song(row))]
    def tracks(self,storefront,ids):
        if not re.fullmatch('[a-z]{2}',storefront) or any(not re.fullmatch('[0-9]{1,20}',str(s)) for s in ids):raise ValueError('Choose Apple Music catalog songs.')
        ids=[str(s) for s in ids];found={}
        for start in range(0,len(ids),100):
            path='/v1/catalog/'+storefront+'/songs?'+urllib.parse.urlencode({'ids':','.join(dict.fromkeys(ids[start:start+100]))})
            for row in self.cached(path).get('data',[]):
                song=self.song(row)
                if song:found[song['id']]=song
        return [found[s] for s in ids if s in found]

    def track(self,storefront,sid):
        sid=str(sid)
        if not re.fullmatch('[a-z]{2}',storefront) or not re.fullmatch(r'[0-9]{1,20}',sid):raise ValueError('Choose an Apple Music search result.')
        rows=self.cached('/v1/catalog/'+storefront+'/songs/'+sid).get('data',[])
        track=self.song(rows[0]) if rows else None
        if not track or track['id']!=sid:raise ValueError('This song is unavailable in the host’s Apple Music region.')
        return track

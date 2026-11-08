"""Versioned, same-origin web API and bearer-token API for future native clients."""
import argparse
import hashlib
import hmac
import http.cookies
import ipaddress
import json
import logging
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
from .build_info import identity as build_identity
from .accounts import Accounts
from .avatars import Avatars
from .diagnostics import Diagnostics
from .stream_grants import StreamGrants
from .request_submission import RequestSubmissions, RequestLimit
from .request_progress import mark_background_requested
from .sound import Sound
from .render_cache import RenderCache, RenderBusy
from .admin import request_rows
from .connections import connected_users, ActiveStreams
from .apple_music import AppleMusic
from .spotify import Spotify
from .native_media import NativeMedia
from . import native_http
from . import spotify_http
from .playlists import Playlists
from .profile_stats import summary as profile_summary
from .parties import Parties
from . import party_http
from .listening import Listening
from .listening_events import ListeningEvents
from .library import Library, Player, PlaybackConflict
from . import requests as acquisition
from app.errors import CatalogError

DATA=os.environ.get('ARRIFY_DATA','/data')
ORIGIN=os.environ.get('ARRIFY_ORIGIN','http://localhost:8792').rstrip('/')
SECURE=os.environ.get('ARRIFY_SECURE_COOKIE','0')=='1'
ORIGINS=frozenset([ORIGIN]+[o.strip().rstrip('/') for o in os.environ.get('ARRIFY_ALLOWED_ORIGINS','').split(',') if o.strip()])
SETUP_NETWORKS=[ipaddress.ip_network(n.strip()) for n in os.environ.get('ARRIFY_SETUP_NETWORKS','192.168.1.0/24,127.0.0.0/8,::1/128').split(',') if n.strip()]
from .web_assets import WebAssets
WEB=Path('/opt/arrify/web')
WEB_ASSETS=WebAssets(WEB)

class Error(Exception):
    def __init__(self,status,message): self.status,self.message=status,message

class ArrifyHTTPServer(ThreadingHTTPServer):
    """Bound native threads when HTTP/2 proxies fan out artwork requests."""
    request_queue_size=256
    def __init__(self,*args,**kwargs):
        self.workers=ThreadPoolExecutor(max_workers=24,thread_name_prefix='arrify-http')
        self.request_slots=threading.BoundedSemaphore(48)
        super().__init__(*args,**kwargs)
    def process_request(self,request,client_address):
        # Bound accepted work as well as active threads. ThreadPoolExecutor's
        # default queue is unbounded and otherwise retains sockets under load.
        if not self.request_slots.acquire(blocking=False):
            body=b'{"error":"Arrify is busy. Try again in a moment."}'
            try:
                request.settimeout(.5)
                request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\nConnection: close\r\nRetry-After: 1\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
            except OSError:pass
            finally:self.shutdown_request(request)
            return
        try:future=self.workers.submit(self.process_request_thread,request,client_address)
        except RuntimeError:
            self.request_slots.release();self.shutdown_request(request);return
        def finished(result):
            try:
                if result.cancelled():self.shutdown_request(request)
            finally:self.request_slots.release()
        future.add_done_callback(finished)
    def server_close(self):
        super().server_close()
        self.workers.shutdown(wait=False,cancel_futures=True)

class Handler(BaseHTTPRequestHandler):
    server_version='Arrify'
    def setup(self):
        super().setup()
        self.connection.settimeout(30)
    def log_message(self,*args): pass
    def send(self,status,body=b'',mime='application/json',extra=None):
        artwork_match=re.fullmatch('/api/v1/artwork/([0-9]+)',urlparse(self.path).path)
        diagnostics=getattr(self.server,'diagnostics',None)
        if artwork_match and diagnostics:
            diagnostics.artwork_response(int(artwork_match[1]),status,len(body),'grant' in parse_qs(urlparse(self.path).query),self.headers.get('User-Agent',''))
        self.send_response(status)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(body)))
        if not any(key.lower()=='cache-control' for key in (extra or {})):
            self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        apple_enabled=getattr(getattr(self.server,'apple_music',None),'enabled',False)
        # MusicKit's cross-origin authorization handoff needs the referring site.
        # Reveal only the origin to Apple, never the Arrify path or query.
        self.send_header('Referrer-Policy','strict-origin-when-cross-origin' if apple_enabled else 'same-origin')
        policy="default-src 'self'; img-src 'self' data:; media-src 'self' blob:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if apple_enabled:
            policy="default-src 'self'; img-src 'self' data: https://*.mzstatic.com; media-src 'self' blob: https://*.music.apple.com https://*.itunes.apple.com https://*.itunes.com https://*.mzstatic.com; style-src 'self'; script-src 'self' https://js-cdn.music.apple.com; connect-src 'self' https://*.music.apple.com https://*.itunes.apple.com https://*.itunes.com https://*.mzstatic.com; frame-src https://authorize.music.apple.com; worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if getattr(getattr(self.server,'spotify',None),'enabled',False):
            additions={'img-src':['https://*.scdn.co'],'script-src':['https://sdk.scdn.co'],'frame-src':['https://sdk.scdn.co','https://open.spotify.com'],'connect-src':["'self'",'https://*.spotify.com','wss://*.spotify.com','https://*.scdn.co','https://*.spotifycdn.com','https://*.akamaized.net'],'media-src':['https://*.scdn.co','https://*.spotifycdn.com','https://*.akamaized.net'],'worker-src':["'self'","blob:"]}
            directives={part.strip().split()[0]:part.strip().split()[1:] for part in policy.split(';') if part.strip()}
            for directive,values in additions.items():directives[directive]=list(dict.fromkeys(directives.get(directive,[])+values))
            policy='; '.join(' '.join([k]+v) for k,v in directives.items())
        # The host may embed only our read-only display; other pages remain unframeable.
        page_path=urlparse(self.path).path
        if page_path in ('/','/party-display'):
            directives={part.strip().split()[0]:part.strip().split()[1:] for part in policy.split(';') if part.strip()}
            if page_path=='/':directives['frame-src']=list(dict.fromkeys(directives.get('frame-src',["'self'"])+["'self'"]))
            else:directives['frame-ancestors']=["'self'"]
            policy='; '.join(' '.join([k]+v) for k,v in directives.items())
        self.send_header('Content-Security-Policy',policy)
        for k,v in (extra or {}).items(): self.send_header(k,v)
        self.end_headers()
        if self.command!='HEAD': self.wfile.write(body)
    def json(self,data,status=200,extra=None):
        self.send(status,json.dumps(data,ensure_ascii=False).encode(),extra=extra)
    def cookie(self,token,clear=False):
        secure=SECURE and not (self.valid_origin() and self.headers.get('Origin','').startswith('http://'))
        return 'arrify_session='+token+'; Path=/; HttpOnly; SameSite=Lax; Max-Age='+('0' if clear else '604800')+('; Secure' if secure else '')
    def valid_origin(self):
        origin=self.headers.get('Origin','')
        return origin in ORIGINS and urlparse(origin).netloc.lower()==self.headers.get('Host','').lower()
    def setup_allowed(self):
        # Deliberately use the actual TCP peer, never forwarded headers.
        peer=ipaddress.ip_address(self.client_address[0])
        return any(peer in network for network in SETUP_NETWORKS)
    def token(self):
        auth=self.headers.get('Authorization','')
        if auth.startswith('Bearer '): return auth[7:],'native'
        cookie=http.cookies.SimpleCookie()
        try: cookie.load(self.headers.get('Cookie',''))
        except http.cookies.CookieError: pass
        return (cookie['arrify_session'].value if 'arrify_session' in cookie else ''),'web'
    def identity(self,write=False):
        token,kind=self.token()
        user=self.server.accounts.identity(token,kind)
        if write and kind=='web':
            if not self.valid_origin() or not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),self.server.accounts.csrf(token)):
                raise Error(403,'Invalid request origin or CSRF token.')
        return user
    def listening_result(self,result,user):
        grants=getattr(self.server,'stream_grants',None)
        if grants:
            token,kind=self.token();result=grants.attach(result,user,token,kind)
        return self.json(result)
    def stream_identity(self,tid,q,check_expiry=True):
        if 'native_grant' in q:return self.server.native_media.identity(tid,q,check_expiry)
        if 'grant' not in q:return self.identity()
        if any(len(q.get(k,[]))!=1 for k in ('grant','device_id','owner_epoch')):
            raise PermissionError('Invalid playback link.')
        grants=getattr(self.server,'stream_grants',None)
        if not grants:raise PermissionError('Playback link unavailable.')
        try:return grants.identity(q['grant'][0],tid,q['device_id'][0],q['owner_epoch'][0],check_expiry)
        except PermissionError:
            if getattr(self.server,'diagnostics',None):self.server.diagnostics.denied_stream(tid)
            raise
    def body(self):
        size=int(self.headers.get('Content-Length','0'))
        if size<1 or size>128000: raise Error(413,'Invalid request size.')
        if self.headers.get_content_type()!='application/json': raise Error(415,'JSON required.')
        value=json.loads(self.rfile.read(size))
        if not isinstance(value,dict): raise ValueError('JSON object required.')
        return value
    def handle_error(self,fn):
        try: fn()
        except Error as exc:
            if self.path.startswith('/api/v1/native/'):
                return native_http.error(self,exc.status,'invalid_request',exc.message)
            self.json({'error':exc.message},exc.status)
        except party_http.PartyGone as exc:self.json({'error':str(exc),'state':exc.state},410)
        except PermissionError as exc: self.json({'error':str(exc)},403)
        except party_http.CatalogBusy as exc:self.json({'error':str(exc)},503,{'Retry-After':'1'})
        except PlaybackConflict as exc:self.json({'error':str(exc)},409)
        except (ValueError,KeyError,TypeError,CatalogError) as exc:
            if self.path.startswith('/api/v1/native/'):
                return native_http.error(self,400,'invalid_request','Invalid request.')
            self.json({'error':str(exc) if isinstance(exc,(ValueError,CatalogError)) else 'Invalid request.'},400)
        except (BrokenPipeError,ConnectionResetError,TimeoutError): pass
        except Exception:
            logging.exception('Arrify request failed')
            try:
                if self.path.startswith('/api/v1/native/'):native_http.error(self,500,'server_error','Request failed.')
                else:self.json({'error':'Request failed.'},500)
            except OSError: pass
    def do_GET(self): self.handle_error(self.get)
    def do_HEAD(self): self.handle_error(self.get)
    def do_POST(self): self.handle_error(self.post)
    def get(self):
        parsed=urlparse(self.path); path=unquote(parsed.path); q=parse_qs(parsed.query)
        a,lib,player=self.server.accounts,self.server.library,self.server.player
        if native_http.dispatch(self,path):return
        if path=='/health': return self.json({'status':'ok','build':build_identity(),'catalog_tracks':len(lib.tracks),'refresh_error':lib.refresh_error,'last_refresh_success':lib.last_refresh_success,'active_streams':len(self.server.active_streams.snapshot()['items'])})
        if path=='/api/v1/setup':
            return self.json({'needed':a.needs_setup(),'allowed':self.setup_allowed()})
        if not path.startswith('/api/'):
            if path=='/.well-known/apple-app-site-association':
                app_ids=[x.strip() for x in os.environ.get('ARRIFY_IOS_APP_IDS','').split(',') if re.fullmatch(r'[A-Z0-9]{10}\.[A-Za-z0-9.-]+',x.strip())]
                return self.json({'applinks':{'details':[{'appIDs':app_ids,'components':[{'/':'/party/join'}]}] if app_ids else []}})
            assets={'/privacy':'privacy.html','/buffer-status.js':'buffer-status.js','/device-buffer-worker.js':'device-buffer-worker.js','/party':'party.html','/party-display':'party.html','/party-guest.js':'party-guest.js','/party-host.js':'party-host.js','/party.css':'party.css','/metadata.js':'metadata.js','/browse.js':'browse.js','/connections.js':'connections.js','/apple-music.js':'apple-music.js','/spotify.js':'spotify.js','/listening.js':'listening.js','/api.js':'api.js','/navigation.js':'navigation.js','/gapless.js':'gapless.js','/':'index.html','/app.js':'app.js','/player.js':'player.js','/playback.js':'playback.js','/install.js':'install.js','/sw.js':'sw.js','/offline.html':'offline.html','/manifest.webmanifest':'manifest.webmanifest','/style.css':'style.css','/brand.css':'brand.css','/recover':'recover.html','/recover.js':'recover.js','/output.js':'output.js','/timeline.js':'timeline.js'}
            if re.fullmatch(r'/assets/fonts/(outfit|dm-sans)-latin-wght-normal\.woff2',path):
                return self.send(200,(WEB/path.lstrip('/')).read_bytes(),'font/woff2',{'Cache-Control':'public, max-age=86400'})
            if re.fullmatch(r'/assets/channels/(80s-on-8|the-bridge|the-blend|1st-wave|the-pulse|classic-rewind|the-spectrum)\.svg',path):
                return self.send(200,(WEB/path.lstrip('/')).read_bytes(),'image/svg+xml',{'Cache-Control':'public, max-age=86400'})
            if path=='/assets/channels/xmplaylist.ico':
                return self.send(200,(WEB/'assets/channels/xmplaylist.ico').read_bytes(),'image/vnd.microsoft.icon',{'Cache-Control':'public, max-age=86400'})
            if re.fullmatch(r'/assets/(arrify-(logos|mark|wordmark)\.jpg|icon-(192|512)\.png|apple-touch-icon\.png)',path):
                return self.send(200,(WEB/path.lstrip('/')).read_bytes(),'image/jpeg' if path.endswith('.jpg') else 'image/png',{'Cache-Control':'public, max-age=86400'})
            if path=='/party/join':assets[path]='party.html'
            assets.update({'/manifest-ipad.webmanifest':'manifest-ipad.webmanifest','/pwa.js':'pwa.js','/pwa.css':'pwa.css','/party-import.js':'party-import.js','/guest-wifi.js':'guest-wifi.js','/party-display':'party-display.html','/party-tv.js':'party-tv.js'})
            if path not in assets: raise Error(404,'Not found.')
            filename=assets[path]
            body,headers=WEB_ASSETS.response(filename,q.get('v',[None])[0])
            return self.send(200,body,{'html':'text/html; charset=utf-8','js':'text/javascript','css':'text/css','webmanifest':'application/manifest+json'}[filename.rsplit('.',1)[1]],headers)
        if spotify_http.get(self,path,q):return
        if party_http.public_get(self,path,q):return
        if path=='/api/v1/me':
            user=self.identity()
            token,kind=self.token()
            return self.json({**user,'csrf':a.csrf(token) if kind=='web' else None})
        if re.fullmatch('/api/v1/stream/[0-9]+',path) and ('grant' in q or 'native_grant' in q):
            return self.stream(int(path.rsplit('/',1)[1]),q)
        try:
            user=self.stream_identity(int(path.rsplit('/',1)[1]),q) if re.fullmatch('/api/v1/artwork/[0-9]+',path) and 'grant' in q else self.identity()
        except PermissionError:
            if re.fullmatch('/api/v1/stream/[0-9]+',path) and getattr(self.server,'diagnostics',None):self.server.diagnostics.denied_stream(int(path.rsplit('/',1)[1]))
            raise
        uid=user['id']
        if path.startswith('/api/v1/parties') and party_http.host_get(self,path,user):return
        if path=='/api/v1/apple-music':return self.json(self.server.apple_music.config(uid))
        if path=='/api/v1/apple-music/token':
            a.rate_limit(('apple-developer-token',uid),30,900)
            return self.json({'developerToken':self.server.apple_music.token(),'origin':ORIGIN})
        if path=='/api/v1/profile':return self.json(a.profile(uid))
        if path=='/api/v1/profile/stats':return self.json(profile_summary(a,lib,uid))
        if re.fullmatch('/api/v1/avatar/[0-9]+',path):
            requested=int(path.rsplit('/',1)[1])
            if requested!=uid and not user['admin']:raise Error(403,'This profile is private.')
            mime,data=self.server.avatars.image(requested)
            return self.send(200,data,mime,{'Cache-Control':'private, max-age=300','Vary':'Cookie, Authorization'})
        offset=max(0,min(100000,int(q.get('offset',['0'])[0]))); limit=max(1,min(250,int(q.get('limit',['60'])[0])))
        query=q.get('q',[''])[0][:200]
        if path=='/api/v1/discover/explore':
            a.rate_limit(('catalog-explore',uid),60,900)
            from urllib.parse import urlencode
            return self.json(acquisition.call('/explore?'+urlencode({'q':query,'offset':offset})))
        cover=re.fullmatch(r'/api/v1/catalog-artwork/([0-9a-f-]{36})',path)
        if cover:
            from .catalog_artwork import thumbnail
            mime,data,ttl=thumbnail(cover[1],str(Path(DATA)/'catalog-artwork'))
            return self.send(200,data,mime,{'Cache-Control':'private, max-age='+str(ttl)})
        if path=='/api/v1/discover/artist-albums':
            a.rate_limit(('artist-albums',uid),60,900)
            from urllib.parse import urlencode
            if q.get('mbid'):
                return self.json(acquisition.call('/artist-albums?'+urlencode({'artist':q['mbid'][0],'title':'','offset':offset})))
            detail=lib.detail('artists',q.get('artist',[''])[0])
            if not detail.get('tracks'):raise ValueError('Artist has no reference songs.')
            return self.json(acquisition.call('/artist-albums?'+urlencode({'artist':detail['name'],'title':detail['tracks'][0]['title'],'offset':offset})))
        if path=='/api/v1/discover/album':
            a.rate_limit(('catalog-album',uid),30,900)
            from urllib.parse import urlencode
            if q.get('group'):
                result=acquisition.call('/album-group?'+urlencode({'group':q['group'][0]}))
            elif q.get('track'):
                track=lib.track(int(q['track'][0]))
                result=acquisition.call('/album-for-song?'+urlencode({'artist':track['artist'],'title':track['title']}))
            else:
                selection=q.get('selection',[''])[0]
                if not re.fullmatch('[0-9a-f-]{36}',selection):raise ValueError('Choose an album from search.')
                result=acquisition.call('/album?'+urlencode({'selection':selection}))
            from .catalog_album import attach_local
            with lib.index_lock:local_tracks=list(lib.tracks.values())
            with lib.music.lock:
                technical={r['track_id']:dict(r) for r in lib.music.db.execute('SELECT track_id,lossless,bitrate_kbps FROM track_technical')}
            attach_local(result,local_tracks,technical)
            self.server.request_submissions.mark_requested(result['items'])
            mark_background_requested(result['items'])
            return self.json(result)
        if path=='/api/v1/discover':
            a.rate_limit(('discover',uid),30,900)
            result=acquisition.search(query,offset)
            self.server.request_submissions.mark_requested(result.get('items',[]))
            mark_background_requested(result.get('items',[]))
            return self.json(result)
        if path=='/api/v1/requests':return self.json(self.server.request_progress.rows(uid,offset,limit))
        if path=='/api/v1/notifications':return self.json(self.server.request_progress.notifications(uid))
        if path=='/api/v1/request-notifications':
            notices=self.server.request_progress.notifications(uid)
            return self.json({'items':[{**r,'notification_id':r['id'],'id':r['request_id']} for r in notices['items'] if not r['read'] and r.get('track_id')]})
        if re.fullmatch('/api/v1/tracks/[0-9]+',path):
            return self.json({'track':lib.track(int(path.rsplit('/',1)[1]))})
        if re.fullmatch('/api/v1/technical/[0-9]+',path):
            return self.json(lib.technical(int(path.rsplit('/',1)[1])))
        prepare=re.fullmatch('/api/v1/radio/resolve/(track|artist|catalog)/([a-f0-9]+)/([a-f0-9-]{36})',path)
        if prepare:
            a.rate_limit(('radio-resolve',uid),60,900)
            candidate=lib.radio.recommendation(prepare[1],prepare[2],prepare[3])
            result=acquisition.selection(prepare[3]);item=next((x for x in result.get('items',[]) if x.get('recording_id')==prepare[3]),None)
            from .radio import recording_key
            if not item or recording_key(item)!=recording_key(candidate):raise ValueError('The recording could not be matched confidently. Try catalog search.')
            self.server.request_submissions.mark_requested([item])
            mark_background_requested([item])
            return self.json({'item':item})
        match=re.fullmatch('/api/v1/radio/(track|artist|catalog)/([a-f0-9]+)',path)
        if match:
            a.rate_limit(('radio-preview',uid),60,60)
            return self.json(lib.radio.preview(match[1],match[2],min(limit,50)))
        if path=='/api/v1/browse':
            a.rate_limit(('browse',uid),120,60)
            filters={key:q.get(key,[''])[0] for key in ('genre','decade')}
            filters['kind']=q.get('kind',['songs'])[0]
            return self.json(lib.discovery.page(filters,offset,min(limit,100)) if q.get('page') else lib.discovery.overview())
        if path=='/api/v1/browse/charts':
            a.rate_limit(('browse-charts',uid),120,60)
            chart=lib.discovery.chart(q.get('genre',['overall'])[0],q.get('new',['0'])[0]=='1');requested=self.server.request_submissions.browse_requests(uid)
            for item in chart['items']:item['requested']=requested.get(item['request_key'])
            self.server.request_submissions.mark_requested(chart['items'])
            mark_background_requested(chart['items'])
            return self.json(chart)
        logo=re.fullmatch(r'/api/v1/stations/artwork/((?:sirius-[a-z0-9-]{1,80}|the-spectrum))',path)
        if logo:
            image=Path('/spectrum-channel/artwork')/(logo[1]+'.webp')
            if not image.is_file():raise Error(404,'Artwork not available.')
            return self.send(200,image.read_bytes(),'image/webp',{'Cache-Control':'private, max-age=86400'})
        if path=='/api/v1/stations':
            from .channels import PersonalStations
            a.rate_limit(('stations-read',uid),90,60)
            result=PersonalStations(a).get(uid,lib.collections())
            try:directory=acquisition.call('/stations');result['catalog']=directory['items'];result['catalog_updated']=directory['updated']
            except ValueError:result['catalog']=[];result['catalog_unavailable']=True
            result['collections']=PersonalStations(a).collections(uid,lib.collections())
            try:
                activity=acquisition.call('/activity');own={x['slug'] for x in result['items']}
                result['progress']=[x for x in activity['stations'] if x['slug'] in own]
            except ValueError:result['progress']=[]
            return self.json(result)
        if path=='/api/v1/home':
            with a.lock:
                recent=a.db.execute('SELECT track_id,max(played_at) played FROM history WHERE user_id=? GROUP BY track_id ORDER BY played DESC LIMIT 12',(uid,)).fetchall()
            from .channels import PersonalStations
            return self.json({'collections':PersonalStations(a).collections(uid,lib.collections()),'recent':[lib.track(r[0]) for r in recent if r[0] in lib.tracks], 'suggested_albums':lib.suggestions(),'tracks':len(lib.tracks),'albums':len(lib.albums),'artists':len(lib.artists)})
        if path=='/api/v1/search/history':return self.json(a.recent_searches(uid))
        if path=='/api/v1/search':
            a.rate_limit(('local-search',uid),180,60)
            return self.json(lib.search.query(query,offset,limit))
        if path=='/api/v1/collections': return self.json({'items':lib.collections()})
        if path.startswith('/api/v1/collections/'):
            slug=path.rsplit('/',1)[1]; ids=lib.collection_ids(slug)
            return self.json({'collection':next(p for p in lib.collections() if p['slug']==slug),'tracks':[lib.track(i) for i in ids[offset:offset+limit]],'total':len(ids)})
        if path in ('/api/v1/albums','/api/v1/artists'): return self.json(lib.browse(path.rsplit('/',1)[1],query,offset,limit))
        if re.fullmatch('/api/v1/(albums|artists)/[a-f0-9]{24}',path):
            return self.json(lib.detail(path.split('/')[3],path.rsplit('/',1)[1]))
        if path=='/api/v1/listening/events':return self.listening_events(uid)
        if path=='/api/v1/listening':return self.listening_result(Listening(player).state(uid,q.get('revision',[None])[0]),user)
        if path=='/api/v1/player': return self.json(Listening(player).state(uid))
        if path=='/api/v1/favorites':
            with a.lock: ids=[r[0] for r in a.db.execute('SELECT track_id FROM favorites WHERE user_id=? ORDER BY rowid DESC',(uid,))]
            return self.json({'tracks':[lib.track(i) for i in ids if i in lib.tracks]})
        if path=='/api/v1/playlists':
            return self.json({'items':Playlists(a,lib).rows(uid,q.get('scope',['mine'])[0])})
        if re.fullmatch('/api/v1/playlists/[0-9]+',path):
            try:
                playlist=Playlists(a,lib).get(uid,int(path.rsplit('/',1)[1]));result={'playlist':playlist}
                if q.get('include_tracks',['0'])[0]=='1':
                    with lib.index_lock:result['tracks']=[lib.track(i) for i in playlist['track_ids'] if i in lib.tracks]
                return self.json(result)
            except LookupError:raise Error(404,'Playlist not found.')
        if path=='/api/v1/history':
            with a.lock: rows=a.db.execute('SELECT track_id,played_at FROM history WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?',(uid,limit,offset)).fetchall()
            return self.json({'items':[{'track':lib.track(r[0]),'played_at':r[1]} for r in rows if r[0] in lib.tracks]})
        if path=='/api/v1/sound':return self.json(self.server.sound.preferences(uid))
        if re.fullmatch('/api/v1/sound/[0-9]+',path):
            a.rate_limit(('sound-analysis',uid),180,900)
            return self.json(self.server.sound.status(int(path.rsplit('/',1)[1]),q.get('mode',['track'])[0]))
        if path=='/api/v1/admin/acquisition':
            if not user['admin']:raise Error(403,'Administrator access required.')
            a.rate_limit(('acquisition-status',uid),30,60)
            return self.json(acquisition.call('/activity'))
        if path=='/api/v1/admin/metadata':
            if not user['admin']:raise Error(403,'Administrator access required.')
            return self.json(lib.metadata.public(offset,min(limit,50)))
        if path=='/api/v1/admin/locations':
            if not user['admin']:raise Error(403,'Administrator access required.')
            return self.json(lib.locations.public())
        if path=='/api/v1/admin/connections':
            if not user['admin']:raise Error(403,'Administrator access required.')
            a.rate_limit(('admin-connections',uid),12,60)
            return self.json(connected_users(a,lib,user,streams=self.server.active_streams))
        if path=='/api/v1/admin/overview':
            if not user['admin']:raise Error(403,'Administrator access required.')
            return self.json(a.admin_overview() | {'tracks':len(lib.tracks),'albums':len(lib.albums),'locations':len(lib.locations.state['items'])})
        if path=='/api/v1/admin/requests':
            if not user['admin']:raise Error(403,'Administrator access required.')
            return self.json(request_rows(a,None,offset,min(limit,50)))
        if path=='/api/v1/admin/library/folders':
            if not user['admin']:raise Error(403,'Administrator access required.')
            return self.json(lib.locations.folders(q.get('path',[''])[0],q.get('filter',[''])[0]))
        if path=='/api/v1/admin/summary':
            if not user['admin']:raise Error(403,'Administrator access required.')
            return self.json({'pending':a.pending_count()})
        if path=='/api/v1/admin/users':
            if not user['admin']: raise Error(403,'Administrator access required.')
            return self.json(a.admin_users())
        if re.fullmatch('/api/v1/artist-artwork/[0-9a-f]{24}',path):
            artist_id = path.rsplit('/',1)[1]
            if artist_id not in lib.artists:
                raise Error(404,'Artist not found.')
            image = Path('/data/artist-artwork') / (artist_id + '.jpg')
            if not image.is_file():
                raise Error(404,'Artist artwork not found.')
            return self.send(200,image.read_bytes(),'image/jpeg',{'Cache-Control':'private, max-age=3600'})
        if re.fullmatch('/api/v1/artwork/[0-9]+',path):
            artwork_headers={'Cache-Control':'private, no-store' if 'grant' in q else 'private, max-age=300'}
            identified=lib.supplemental_artwork(int(path.rsplit('/',1)[1]),directory='/data/recognition-artwork')
            if identified is not None:
                return self.send(200,identified,'image/jpeg',artwork_headers)
            try:
                mime,data,source=lib.music.artwork(int(path.rsplit('/',1)[1]))
            except FileNotFoundError:
                # Missing local artwork continues through Arrify's fallback chain.
                mime,data,source='',b'','fallback'
            if source=='fallback':
                supplemental=lib.supplemental_artwork(int(path.rsplit('/',1)[1]))
                if supplemental is not None:
                    return self.send(200,supplemental,'image/jpeg',artwork_headers)
            if source=='fallback':
                related=lib.related_artwork(int(path.rsplit('/',1)[1]))
                if related is not None:
                    mime,data=related
                    return self.send(200,data,mime,artwork_headers)
            if source=='fallback':
                mime='image/png'
                data=(WEB/'assets/icon-192.png').read_bytes()
            return self.send(200,data,mime,artwork_headers)
        if re.fullmatch('/api/v1/stream/[0-9]+',path):
            return self.stream(int(path.rsplit('/',1)[1]),q)
        raise Error(404,'Not found.')

    def post(self):
        path=urlparse(self.path).path
        data=self.body(); a,lib,p=self.server.accounts,self.server.library,self.server.player
        if native_http.dispatch(self,path,data):return
        if party_http.public_post(self,path,data):return
        if path in ('/api/v1/auth/recovery-link','/api/v1/auth/recovery-check','/api/v1/auth/recover'):
            a.rate_limit(('recovery',self.client_address[0]),12,900)
            if not self.valid_origin():raise Error(403,'Invalid request origin.')
            if path.endswith('/recovery-link'):
                if not self.setup_allowed():raise Error(403,'Start recovery from your local LAN.')
                token=a.claim_recovery()
                return self.json({'url':ORIGIN+'/recover#'+token})
            if path.endswith('/recovery-check'):return self.json(a.check_recovery(data.get('token')))
            result=a.finish_recovery(data.get('token'),data.get('password'))
            return self.json(result,extra={'Set-Cookie':self.cookie('',True)})
        if path in ('/api/v1/auth/register','/api/v1/auth/login','/api/v1/auth/token','/api/v1/auth/setup'):
            a.rate_limit((self.client_address[0],path))
            if path!='/api/v1/auth/token' and not self.valid_origin(): raise Error(403,'Invalid request origin.')
            if path.endswith('/setup'):
                if not self.setup_allowed():raise Error(403,'Initial owner setup is available only from the local LAN.')
                return self.json(a.bootstrap(data.get('username'),data.get('password'),data.get('email')),201)
            if path.endswith('/register'): return self.json(a.register(data.get('username'),data.get('password'),data.get('email')),201)
            kind='native' if path.endswith('/token') else 'web'
            token=a.login(data.get('username'),data.get('password'),kind)
            if kind=='native': return self.json({'access_token':token,'token_type':'Bearer','expires_in':604800})
            return self.json({'ok':True},extra={'Set-Cookie':self.cookie(token)})
        user=self.identity(write=True); uid=user['id']
        if spotify_http.post(self,path,data,user):return
        if path.startswith('/api/v1/parties') and party_http.host_post(self,path,data,user):return
        if path=='/api/v1/stations':
            from .channels import PersonalStations
            a.rate_limit(('stations-edit',uid),60,60)
            if data.get('action')=='add':a.rate_limit(('stations-add',uid),30,3600)
            return self.json(PersonalStations(a).update(uid,data,lib.collections(),lambda channel:acquisition.call('/stations',{'id':channel})))
        if path=='/api/v1/sound':
            a.rate_limit(('sound-settings',uid),30,900)
            return self.json(self.server.sound.preferences(uid,data))
        if path=='/api/v1/apple-music':
            a.rate_limit(('apple-profile-link',uid),12,900)
            if data.get('action')=='disconnect':
                with a.transaction():
                    self.server.parties.apple_playback.stop(uid)
                    result=self.server.apple_music.disconnect(uid)
                self.server.parties.notify(uid)
                return self.json(result)
            if data.get('action')!='connect':raise ValueError('Choose Connect or Disconnect Apple Music.')
            return self.json(self.server.apple_music.link(uid,data.get('music_user_token')))
        if path=='/api/v1/search/history':
            operation=data.get('action');bucket=operation if isinstance(operation,str) and operation in ('remember','remove','clear') else 'invalid'
            a.rate_limit(('search-history',uid,bucket),180,900)
            return self.json(a.edit_search_history(uid,data))
        if path=='/api/v1/player/diagnostics':
            key=('playback-diagnostics',uid,data['source']) if data.get('source') in ('spotify','apple') else ('playback-diagnostics',uid)
            a.rate_limit(key,8,900)
            return self.json(self.server.diagnostics.write(uid,data))
        if path=='/api/v1/request-notifications/read':
            notices=self.server.request_progress.notifications(uid)
            ids=[r['id'] for r in notices['items'] if r['request_id']==str(data.get('id',''))]
            return self.json(self.server.request_progress.mark_read(uid,ids))
        if path=='/api/v1/profile/sharing':
            a.rate_limit(('profile-sharing',uid),60,900)
            return self.json(a.set_playlist_shared_default(uid,data.get('playlist_shared_default')))
        if path=='/api/v1/profile':
            a.rate_limit(('profile',uid),12,900)
            return self.json(a.update_profile(uid,data.get('display_name',''),data.get('email',''),data.get('text_size')))
        if path=='/api/v1/profile/refresh':
            a.rate_limit(('profile',uid),12,900)
            return self.json(a.refresh_avatar(uid))
        if path=='/api/v1/radio/seed':
            a.rate_limit(('radio-seed',uid),30,900)
            return self.json({'station':self.server.library.radio.register(data.get('artist'),data.get('title',''))})
        if path=='/api/v1/browse/request':
            a.rate_limit(('browse-request',uid),60,900)
            key=str(data.get('entry',''));genre=str(data.get('genre','overall'))
            if not re.fullmatch('[a-f0-9]{24}',key):raise ValueError('Invalid chart selection.')
            wanted=lib.discovery.request_entry(genre,key)
            from .radio import recording_key
            with lib.index_lock:available=lib.radio_index.recording_ids.get(recording_key(wanted),set())
            if available:return self.json({'available':True,'track_id':min(available)})
            existing=self.server.request_submissions.existing_browse(uid,key)
            if existing:return self.json(existing,202)
            result=acquisition.song(wanted['artist'],wanted['title'])
            matched=result.get('requested',{});items=result.get('items',[])
            if not isinstance(matched,dict) or any(str(matched.get(k,'')).casefold()!=wanted[k].casefold() for k in ('artist','title')) or len(items)!=1 or not items[0].get('requestable') or not items[0].get('album') or not re.fullmatch('[a-f0-9-]{36}',items[0].get('selection','')):raise ValueError('The correct song and official album could not be verified. Nothing was requested.')
            self.identity(write=True)  # Account/session may change during verification.
            try:created=self.server.request_submissions.create(uid,items[0]['selection'],max(0,int(os.environ.get('ARRIFY_REQUESTS_PER_DAY','0'))),source={'browse_key':key})
            except RequestLimit as error:raise Error(429,str(error)) from None
            self.server.request_progress.wake.set()
            return self.json(created,202)
        if path=='/api/v1/requests':
            try:
                result=self.server.request_submissions.create(uid,str(data.get('selection','')),max(0,int(os.environ.get('ARRIFY_REQUESTS_PER_DAY','0'))))
            except RequestLimit as error:raise Error(429,str(error)) from None
            self.server.request_progress.wake.set()
            return self.json(result,202)
        if path=='/api/v1/request-markers':
            a.rate_limit(('request-markers',uid),30,60)
            items=data.get('items')
            if not isinstance(items,list) or len(items)>100 or any(not isinstance(x,dict) or any(not isinstance(x.get(k,''),str) or len(x.get(k,''))>500 for k in ('artist','title','selection','request_key')) for x in items):raise ValueError('Invalid song list.')
            shared=self.server.request_submissions.browse_requests(uid)
            checked=[{k:x.get(k,'') for k in ('artist','title','selection','request_key')} for x in items]
            for x in checked:x['requested']=bool(shared.get(x['request_key']))
            self.server.request_submissions.mark_requested(checked)
            mark_background_requested(checked)
            return self.json({'items':[{'index':i,'requested':x['requested']} for i,x in enumerate(checked)]})
        if path=='/api/v1/notifications/dismiss':return self.json(self.server.request_progress.dismiss(uid,data.get('ids'),all=data.get('all') is True))
        if path=='/api/v1/notifications/read':return self.json(self.server.request_progress.mark_all_read(uid) if data.get('all') is True else self.server.request_progress.mark_read(uid,data.get('ids')))
        if path=='/api/v1/auth/logout':
            self.server.listening.logout(uid,data,a.digest(self.token()[0]))
            return self.json({'ok':True},extra={'Set-Cookie':self.cookie('',True)})
        if path=='/api/v1/listening':return self.listening_result(self.server.listening.command(uid,data),user)
        if path=='/api/v1/player':
            with a.lock:
                if p.load(uid).get('listening',{}).get('owner'):raise Error(409,'Refresh Arrify to control this listening session.')
                return self.json(p.command(uid,data))
        if path=='/api/v1/favorites':
            tid=int(data['track_id']); lib.track(tid)
            with a.lock:
                if data.get('liked',True): a.db.execute('INSERT OR IGNORE INTO favorites VALUES(?,?)',(uid,tid))
                else: a.db.execute('DELETE FROM favorites WHERE user_id=? AND track_id=?',(uid,tid))
            return self.json({'ok':True})
        if path=='/api/v1/playlists':
            try:return self.json(Playlists(a,lib).edit(uid,data))
            except LookupError:raise Error(404,'Playlist not found.')
        if path=='/api/v1/admin/metadata':
            if not user['admin']:raise Error(403,'Administrator access required.')
            a.rate_limit(('metadata-admin',uid),30,900)
            return self.json(self.server.library.metadata.request_scan(data.get('scope','missing')) if data.get('action')=='scan' else self.server.library.metadata.edit(data))
        if path=='/api/v1/admin/locations':
            if not user['admin']:raise Error(403,'Administrator access required.')
            a.rate_limit(('locations',uid),60,900)
            return self.json(self.server.library.locations.edit(data))
        if path=='/api/v1/admin/users':
            if not user['admin']:raise Error(403,'Administrator access required.')
            a.rate_limit(('account-admin',uid),60,900)
            return self.json(a.manage_user(uid,data))
        if path=='/api/v1/admin/approve':
            if not user['admin']: raise Error(403,'Administrator access required.')
            with a.lock:target=a.db.execute('SELECT id FROM users WHERE username=? AND deleted_at=0',(str(data['username']),)).fetchone()
            if not target:raise ValueError('Account not found.')
            return self.json(a.manage_user(uid,{'id':target['id'],'action':'approve'}))
        raise Error(404,'Not found.')

    def listening_events(self,uid):
        if self.command=='HEAD':return self.send(200,b'','text/event-stream')
        hub=self.server.listening_events
        if not self.server.event_slots.acquire(blocking=False):raise Error(503,'Live updates are busy. State polling remains available.')
        opened=False
        try:
            opened=hub.open(uid)
            if not opened:raise Error(429,'Too many live connections for this account.')
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Cache-Control','private, no-cache, no-transform');self.send_header('X-Accel-Buffering','no');self.send_header('Connection','close');self.end_headers();self.close_connection=True
            version=hub.version(uid);self.wfile.write(b'retry: 2000\n\n');self.wfile.flush()
            deadline=time.monotonic()+110
            while __import__('time').monotonic()<deadline:
                try:self.identity()
                except PermissionError:break
                self.wfile.write(('event: session\ndata: '+str(version)+'\n\n').encode());self.wfile.flush()
                changed=hub.wait(uid,version,10)
                if changed==version:
                    self.wfile.write(b': keepalive\n\n');self.wfile.flush()
                    # Identity is rechecked at least every ten seconds.
                    try:self.identity()
                    except PermissionError:break
                version=changed
        except (BrokenPipeError,ConnectionResetError,TimeoutError,OSError):pass
        finally:
            if opened:hub.close(uid)
            self.server.event_slots.release()

    def stream(self,tid,q):
        uid=self.stream_identity(tid,q)['id']
        if self.command=='HEAD':
            if self.headers.get('X-Arrify-Prepare')=='1':
                if not self.server.prepare_slots.acquire(blocking=False):raise Error(503,'Preparation is busy. Retry shortly.')
                try:return self._stream(tid,q)
                finally:self.server.prepare_slots.release()
            return self._stream(tid,q)
        self.active_stream_key=self.server.active_streams.open(uid,tid,q.get('profile',['original'])[0])
        try:return self._stream(tid,q)
        finally:
            self.server.active_streams.close(self.active_stream_key)
            self.active_stream_key=None

    def _stream(self,tid,q):
        self.stream_prepare_started=time.perf_counter()
        uid=self.stream_identity(tid,q)['id'];device=q.get('device_id',[''])[0];epoch=q.get('owner_epoch',[''])[0]
        def owns_stream():return Listening(self.server.player).stream_allowed(uid,device,epoch)
        if not owns_stream():raise Error(409,'Playback belongs to another device.')
        row,filename=self.server.library.music._track_file(tid)
        profile=q.get('profile',['original'])[0]
        if profile not in ('original','aac','mp3','flac','pcm'): raise ValueError('Unknown playback profile.')
        mode=q.get('level',['off'])[0]
        if mode not in ('off','track','album'):raise ValueError('Invalid leveling mode.')
        leveling=self.server.sound.status(tid,mode)
        gain=leveling['gain_db']
        checked=time.monotonic()
        def authorized():
            nonlocal checked
            if time.monotonic()-checked<1:return True
            checked=time.monotonic()
            try:self.stream_identity(tid,q,check_expiry=False);return owns_stream()
            except PermissionError:return False
        if profile=='pcm' and self.server.library.technical(tid)['duration']>280:
            raise Error(422,'This track exceeds the gapless preview memory limit. Use standard playback.')
        if profile in ('aac','mp3'):
            try:
                with self.server.render_cache.open(filename,profile,gain,self.server.transcodes,**({'ready_only':True} if 'native_grant' in q else {})) as audio:
                    self.stream_identity(tid,q)
                    if not owns_stream():raise Error(409,'Playback belongs to another device.')
                    return self.serve_audio(audio,'audio/mp4' if profile=='aac' else 'audio/mpeg',authorized,immutable=True)
            except RenderBusy:
                raise Error(503,'Refresh the playback manifest.' if 'native_grant' in q else 'Transcoding is busy. Retry shortly.')
            except (subprocess.SubprocessError,ValueError):raise Error(422,'This file could not be rendered.')
        elif profile!='original' or mode!='off':
            profile='flac' if profile=='original' else profile
            mime={'aac':'audio/aac','mp3':'audio/mpeg','flac':'audio/flac','pcm':'audio/wav'}[profile]
            if self.command=='HEAD':return self.send(200,b'',mime)
            seek=float(q.get('start',['0'])[0])
            if not 0<=seek<=86400: raise ValueError('Invalid seek position.')
            if not self.server.transcodes.acquire(blocking=False): raise Error(503,'Transcoding is busy. Retry shortly.')
            proc=None
            try:
                args=['ffmpeg','-v','error','-nostdin','-threads','1','-filter_threads','1','-filter_complex_threads','1','-ss',str(seek),'-i',filename,'-vn','-sn','-dn','-map_metadata','-1','-ac','2']
                if gain:args+=['-af','volume='+str(gain)+'dB']
                if profile in ('aac','mp3'):args+=['-b:a','192k']
                if profile=='pcm':args+=['-ar','44100']
                args+=['-c:a',{'aac':'aac','mp3':'libmp3lame','flac':'flac','pcm':'pcm_s16le'}[profile],'-threads','1','-f',{'aac':'adts','mp3':'mp3','flac':'flac','pcm':'wav'}[profile],'pipe:1']
                proc=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
                first=proc.stdout.read(65536)
                if not first: raise Error(422,'This file could not be rendered.')
                self.stream_identity(tid,q)
                self.send_response(200);self.send_header('Content-Type',mime)
                self.send_header('X-Arrify-Leveling',leveling['status']);self.send_header('X-Arrify-Gain-Db',str(gain))
                self.send_header('Cache-Control','no-store');self.send_header('Server-Timing',f'prepare;dur={(time.perf_counter()-self.stream_prepare_started)*1000:.1f}');self.send_header('Connection','close');self.end_headers();self.close_connection=True
                self.server.active_streams.sending(self.active_stream_key)
                self.wfile.write(first)
                while chunk:=proc.stdout.read(65536):
                    if not authorized():break
                    self.wfile.write(chunk)
            finally:
                if proc:
                    proc.kill();proc.wait();proc.stdout.close()
                self.server.transcodes.release()
            return
        with open(filename,'rb') as audio:
            if 'native_grant' in q:self.server.native_media.identity(tid,q,file_stat=os.fstat(audio.fileno()))
            return self.serve_audio(audio,row['mime_type'],authorized)

    def serve_audio(self,audio,mime,authorized,immutable=False):
        stat=os.fstat(audio.fileno());total=stat.st_size; start,end=0,total-1; status=200
        # Representation identity, stable across reads but changed by replacement/write.
        identity=f'{stat.st_dev}:{stat.st_ino}:{stat.st_size}:'+ (str(audio.name) if immutable else f'{stat.st_mtime_ns}:{stat.st_ctime_ns}')
        # Rendered files are atomically published and never modified; LRU touches
        # their timestamps, so only original files use mtime/ctime as validators.
        etag='\"'+hashlib.sha256(identity.encode()).hexdigest()+'\"'
        value=self.headers.get('Range')
        if self.headers.get('If-Range') not in (None,etag):value=None
        if value:
            match=re.fullmatch(r'bytes=(\d*)-(\d*)',value)
            if not match or not any(match.groups()): return self.send(416,extra={'Content-Range':f'bytes */{total}'})
            left,right=match.groups()
            if left: start=int(left);end=min(int(right),end) if right else end
            else:
                if int(right)==0: return self.send(416,extra={'Content-Range':f'bytes */{total}'})
                start=max(0,total-int(right))
            if start>=total or start>end: return self.send(416,extra={'Content-Range':f'bytes */{total}'})
            status=206
        length=end-start+1
        self.send_response(status); self.send_header('Content-Type',mime);self.send_header('Content-Length',str(length));self.send_header('Accept-Ranges','bytes');self.send_header('ETag',etag);self.send_header('Cache-Control','private, no-store');self.send_header('X-Content-Type-Options','nosniff')
        if hasattr(self,'stream_prepare_started'):self.send_header('Server-Timing',f'prepare;dur={(time.perf_counter()-self.stream_prepare_started)*1000:.1f}')
        if status==206: self.send_header('Content-Range',f'bytes {start}-{end}/{total}')
        self.end_headers()
        if self.command=='HEAD': return
        self.server.active_streams.sending(self.active_stream_key)
        audio.seek(start)
        while length:
            if not authorized():break
            chunk=audio.read(min(131072,length))
            if not chunk: break
            self.wfile.write(chunk);length-=len(chunk)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('operation',nargs='?',default='serve');parser.add_argument('username',nargs='?');parser.add_argument('new_username',nargs='?');args=parser.parse_args()
    accounts=Accounts(DATA)
    if args.operation=='rename-account':
        if not args.username or not args.new_username:parser.error('old and new usernames required')
        accounts.rename_account(args.username,args.new_username)
        print('Account renamed; existing sessions revoked.')
        return
    if args.operation=='recover':
        if not args.username:parser.error('username required')
        accounts.enable_recovery(args.username)
        print('One-use password recovery enabled for ten minutes. Start at the LAN /recover page.')
        return
    if args.operation=='make-admin':
        if not args.username: parser.error('username required')
        accounts.approve(args.username,admin=True);print('Account approved as administrator.');return
    lib=Library(DATA)
    server=ArrifyHTTPServer(('0.0.0.0',8080),Handler); server.daemon_threads=True
    server.accounts,server.library,server.player=accounts,lib,Player(accounts,lib)
    server.request_submissions=RequestSubmissions(accounts,acquisition.call)
    server.listening_events=ListeningEvents();server.event_slots=threading.BoundedSemaphore(8)
    server.listening=Listening(server.player,events=server.listening_events)
    server.avatars=Avatars(accounts)
    server.diagnostics=Diagnostics(DATA)
    server.active_streams=ActiveStreams()
    server.stream_grants=StreamGrants(accounts,server.player,Path(DATA)/'secrets')
    server.sound=Sound(DATA,lib,accounts)
    server.transcodes=threading.BoundedSemaphore(2)
    server.prepare_slots=threading.BoundedSemaphore(1)
    server.render_cache=RenderCache(DATA)
    server.native_media=NativeMedia(server)
    from .request_progress import RequestProgress
    server.request_progress=RequestProgress(accounts,lib)
    server.apple_music=AppleMusic(accounts,ORIGIN)
    server.spotify=Spotify(accounts,ORIGIN)
    server.parties=Parties(accounts,server.player,server.listening_events,ORIGIN,apple=server.apple_music,spotify=server.spotify)
    threading.Thread(target=server.parties.background,daemon=True).start()
    threading.Thread(target=server.request_progress.background,daemon=True).start()
    threading.Thread(target=lib.background_refresh,daemon=True).start()
    try:server.serve_forever()
    finally:server.native_media.close();server.server_close()

if __name__=='__main__': main()

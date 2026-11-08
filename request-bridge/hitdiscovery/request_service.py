"""Private Unix-socket request adapter. Acquisition stays inside HitDiscovery.

Runs alongside the existing daemon; shares its supported Store/Acquirer,
expansion lock, byte reservations, ownership rules and quality policy.
No TCP listener and no arbitrary Lidarr proxy.
"""
import fcntl
import copy
import hashlib
import http.server
import json
import logging
import os
import re
import socket
import socketserver
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import threading
import time
from pathlib import Path
from urllib.parse import urlparse,parse_qs,urlencode
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from .api import HTTP
from .config import Config
from .store import Store,now
from .resolver import Resolver
from .matching import key
from .request_ownership import RequestOwnership
from .song_route import ensure as ensure_song_route, active as song_route_active, hold_group as hold_song_group
from .budget import Budget
from .request_catalog import catalog,recording_selection,song_selection,album_tracks,album_for_song,artist_albums,album_for_group,explore_catalog

log=logging.getLogger(__name__)
ROOT=Path(os.getenv('REQUEST_DIR','/requests'))
MBID=re.compile(r'^[0-9a-f-]{36}$')
network_lock=threading.Lock()
submission_lock=threading.Lock()
metadata_health_lock=threading.Lock()
ownership_index=RequestOwnership()

def metadata_observation(ok,error=None):
    with metadata_health_lock:
        value={'at':time.time(),'status':'available' if ok else 'unavailable'}
        if error is not None:
            value['error_type']=type(error).__name__
            if isinstance(error,HTTPError):value['http_status']=error.code
        atomic(ROOT/'metadata-health.json',value)

@contextmanager
def metadata_network(deadline):
    if not network_lock.acquire(timeout=max(.1,deadline-time.monotonic())):raise TimeoutError('Metadata service busy.')
    try:yield
    finally:network_lock.release()

def atomic(path,value):
    if path.parent==ROOT/'status' and value.get('state'):
        old=json.loads(path.read_text()) if path.exists() else {}
        history=old.get('history',[])
        phase=value.get('phase') or value['state']
        if not history or history[-1].get('state')!=phase:
            history=history+[{'state':phase,'at':value.get('updated') or now()}]
        value['history']=history[-40:]
    tmp=path.with_suffix('.tmp')
    durable=path.parent==ROOT/'inbox'
    with tmp.open('w') as handle:
        json.dump(value,handle)
        if durable:handle.flush();os.fsync(handle.fileno())
    tmp.replace(path)
    if durable:
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)

def download_progress(queue,album_id):
    item=next((q for q in queue.get('records',[]) if q.get('albumId')==album_id),None)
    if not item:return {}
    tracked=item.get('trackedDownloadState','');status=item.get('status','')
    if tracked=='importFailed' or status=='failed':phase='import-error'
    elif tracked in ('importPending','importing','imported') or status=='completed':phase='importing'
    elif status=='paused':phase='paused'
    else:phase='downloading'
    result={'phase':phase}
    size=item.get('size',0);left=item.get('sizeleft')
    if size and isinstance(left,(int,float)):
        result['progress_percent']=round(max(0,min(100,100*(size-left)/size)),1)
    return result

def search(config,query,offset=0,recording_id=None,song=None,album_selection=None,album_song=None,discography=None,album_group=None,explore=False):
    if discography is not None:
        if any(not isinstance(discography.get(k),str) or not (0 if k=='title' else 1)<=len(discography[k])<=500 for k in ('artist','title')):raise ValueError('Invalid artist identity.')
        query=json.dumps(discography,sort_keys=True)
    if album_group is not None:
        if not MBID.fullmatch(album_group):raise ValueError('Invalid album.')
        query=album_group
    if album_song is not None:
        if not isinstance(album_song,dict) or any(not isinstance(album_song.get(k),str) or not album_song[k].strip() or len(album_song[k])>500 for k in ('artist','title')):raise ValueError('Invalid song identity.')
        query=json.dumps([album_song['artist'],album_song['title']],ensure_ascii=False)
    if album_selection is not None:
        if not MBID.fullmatch(album_selection):raise ValueError('Invalid album selection.')
        query=album_selection
    if song is not None:
        if not isinstance(song,dict) or any(not isinstance(song.get(k),str) or not song[k].strip() or len(song[k])>500 for k in ('artist','title')):raise ValueError('Invalid song identity.')
        query=json.dumps([song['artist'],song['title']],ensure_ascii=False)
    if recording_id is not None:
        if not MBID.fullmatch(recording_id):raise ValueError('Invalid recording.')
        query=recording_id
    query=query.strip() if song or album_song or discography else query.strip()[:200]
    if len(query)<2:return {'items':[],'next_offset':None}
    if not 0<=offset<=(10000 if discography or explore else 200):raise ValueError('Invalid search page.')
    if not config.contact: raise ValueError('Metadata search contact is not configured.')
    s=Store(config.data/'discovery.sqlite3')
    try:
        cache_key=('arrify-explore-v6:' if explore else 'arrify-discography-v1:' if discography else 'arrify-group-album-v2:' if album_group else 'arrify-local-album-v1:' if album_song else 'arrify-album-tracks-v1:' if album_selection else 'arrify-song-selection-v2:' if song else 'arrify-recording-selection-v2:' if recording_id else 'arrify-album-search-v1:')+hashlib.sha256((query.casefold()+':'+str(offset)).encode()).hexdigest()
        cached=s.cached(cache_key,86400)
        if cached is not None:return cached
        deadline=time.monotonic()+45
        def get(endpoint,**params):
            url='https://musicbrainz.org/ws/2/'+endpoint+'?'+urlencode({'fmt':'json',**params})
            saved=s.cached(url,86400)
            if saved is not None:return saved
            with metadata_network(deadline):
                remaining=deadline-time.monotonic()
                if remaining<=0:raise TimeoutError('Metadata lookup timed out.')
                last=ROOT/'search-clock.json'
                stamp=json.loads(last.read_text()).get('at',0) if last.exists() else 0
                delay=max(0,1.2-(time.time()-stamp))
                if delay:time.sleep(delay)
                atomic(last,{'at':time.time()})
                request=Request(url,headers={'User-Agent':f'Arrify/0.2 ({config.contact})','Accept':'application/json','Content-Type':'application/json'})
                for attempt in range(2):
                    try:
                        with urlopen(request,timeout=max(.5,min(8,deadline-time.monotonic()))) as response:
                            raw=response.read(8*1024*1024+1)
                        break
                    except (HTTPError,URLError,TimeoutError,OSError) as exc:
                        if attempt or (isinstance(exc,HTTPError) and exc.code not in (429,500,502,503,504)) or deadline-time.monotonic()<3:raise
                        time.sleep(2.4);atomic(last,{'at':time.time()})
                if len(raw)>8*1024*1024:raise ValueError('Metadata response too large.')
                result=json.loads(raw);s.cache(url,result);return result
        if explore:
            result=explore_catalog(query,get,offset)
        elif discography:
            result=artist_albums(discography['artist'],discography['title'],get,offset)
        elif album_group:
            result=album_for_group(album_group,get)
        elif album_song:
            result=album_for_song(album_song['artist'],album_song['title'],get)
        elif album_selection:
            selected=s.cached('arrify-selection:'+album_selection,86400*30)
            if not selected:raise ValueError('Search again before opening this album.')
            result=album_tracks(selected,get)
        else:
            result=song_selection(song['artist'],song['title'],get) if song else recording_selection(recording_id,get) if recording_id else catalog(query,get,offset)
        items=result['items']
        for item in items:
            if item['requestable']:s.cache('arrify-selection:'+item['selection'],item)
        items=[{k:v for k,v in item.items() if k!='resolved'} for item in items]
        result['items']=items
        if not result.get('incomplete'):s.cache(cache_key,result)
        return result
    finally:s.db.close()

def submit(config,data):
    selection=str(data.get('selection',''))
    if not MBID.fullmatch(selection): raise ValueError('Choose a catalog result first.')
    s=Store(config.data/'discovery.sqlite3')
    try: item=s.cached('arrify-selection:'+selection,86400*30)
    finally:s.db.close()
    if not item: raise ValueError('Search again before requesting this selection.')
    if item.get('requestable') is False:raise ValueError('An album must be verified before this song can be requested.')
    rid=hashlib.sha256(selection.encode()).hexdigest()[:32]
    path=ROOT/'inbox'/f'{rid}.json'
    if not path.exists():
        atomic(path,{'id':rid,'selection':item,'created':now(),'state':'queued','requested_by':int(data['user_id'])})
    result=status(rid)
    return {'id':rid,'state':result.get('state','queued'),**{k:result.get(k,'') for k in ('title','artist','album')}}

def status(rid):
    if not re.fullmatch('[a-f0-9]{32}',rid): raise ValueError('Invalid request.')
    out=ROOT/'status'/f'{rid}.json'
    result=json.loads(out.read_text()) if out.exists() else {'id':rid,'state':'queued'}
    incoming=ROOT/'inbox'/f'{rid}.json'
    if incoming.exists():
        item=json.loads(incoming.read_text())['selection']
        result.update(title=item['title'],artist=item['artist'],album=item.get('album',''))
    if result.get('state')=='waiting-for-release' and result.get('phase') not in ('song-search','downloading','importing'):result['detail']='This song isn’t available right now. We’ll keep checking.'
    return {k:v for k,v in result.items() if k not in ('retry_at','release_group_id')}

def process(config,path):
    request=json.loads(path.read_text()); rid=request['id']; output=ROOT/'status'/f'{rid}.json'
    previous=json.loads(output.read_text()) if output.exists() else {}
    if previous.get('state') in ('available','needs-review','already-owned','rejected'):return
    if previous.get('retry_at',0)>time.time():return
    s=Store(config.data/'discovery.sqlite3')
    try:
        item=request['selection']; selected=item['recording_id']
        resolved=item.get('resolved') or s.cached('arrify-resolved:'+selected,86400*30)
        if item.get('resolved'):
            resolved=dict(resolved,discovery_channel='arrify-user-request',arrify_requested=True,
                evidence=[{'source':'Arrify user request','independent_family':'Arrify','kind':'current','date':request['created'],
                           'url':'https://musicbrainz.org/recording/'+selected,'unranked':True}])
        if not resolved:
            atomic(output,{'id':rid,'state':'resolving','updated':now()})
            candidate={'artist':item['artist'],'title':item['title'],'discovery_channel':'imported-playlist',
                'evidence':[{'source':'Arrify user request','independent_family':'Arrify','kind':'current',
                    'date':request['created'],'url':'https://musicbrainz.org/recording/'+selected,'unranked':True}]}
            with network_lock:
                resolved=Resolver(HTTP(s),config.contact).resolve(candidate)
            if resolved.get('resolution_status')!='resolved' or resolved.get('recording_id')!=selected:
                atomic(output,{'id':rid,'state':'needs-review','detail':'No confident official release matching the selected recording.','updated':now()});return
            resolved['discovery_channel']='arrify-user-request'
            resolved['arrify_requested']=True
            s.cache('arrify-resolved:'+selected,resolved)
        rg=resolved['release_group_id']
        # NAS inventory, not a Lidarr artist entry, establishes ownership.
        owned=ownership_index.get(s).owns(resolved)
        if owned:
            atomic(output,{'id':rid,'state':'already-owned','updated':now()});return
        s.cache('resolution:'+key(resolved['artist'],resolved['title']),resolved)
        s.cache('arrify-priority:'+rg,{'requested':request['created']})
        # Explicit operator policy: approved user selections authorize their
        # album's verified release size plus 10%, independently of daily caps.
        if not s.cached('arrify-authorization:'+rg,ttl=86400*3650):
            s.cache('arrify-authorization:'+rg,{'request_id':rid,'created':request['created'],'mode':'verified-album-size-plus10'})
        s.audit('arrify-request-prioritized',{'request_id':rid,'rg':rg}) if not previous.get('release_group_id') else None
        req=s.db.execute('SELECT state,lidarr_id,detail FROM requests WHERE rg=?',(rg,)).fetchone()
        state=req['state'] if req else 'priority-queued'
        if state=='downloaded': state='imported-awaiting-library-check'
        # In-flight downloads/imports continue past an allowance's expiry.
        if state in ('priority-queued','prepared'):
            if not (config.acquire and config.reviewed): state='acquisition-disabled'
            # The request's scoped budget will authorize exact bytes when a
            # real eligible release is selected. Normal daily expiry is retained.
        policy=s.cached('arrify-policy:'+rg) or {}
        if policy.get('detail') in ('Existing artist is unmonitored; operator review is required to preserve that setting.', 'Existing metadata profile excludes this release type; operator review is required.'):
            policy={'detail':''}; s.cache('arrify-policy:'+rg,policy)
        if state=='priority-queued' and policy.get('detail'):state='policy-review'
        detail=policy.get('detail','')
        if req and state=='waiting-for-release':
            detail=req['detail'] or 'No suitable download was found yet. Arrify will retry automatically.'
        progress={}
        if req and req['lidarr_id'] and state in ('requested','prepared','partial-owned'):
            from .api import Lidarr
            try:
                queue=s.cached('arrify-queue-progress',ttl=15)
                if queue is None:
                    queue=Lidarr(config,HTTP(s)).get('queue',pageSize=1000)
                    s.cache('arrify-queue-progress',queue)
                progress=download_progress(queue,req['lidarr_id'])
            except Exception:log.warning('Download progress unavailable; request state retained')
        if item.get('kind')=='Song':
            song_route=ensure_song_route(ROOT,rid,rg)
            if song_route.get('phase')=='available':
                state='imported-awaiting-library-check';progress={'phase':'imported-awaiting-library-check'};detail='The requested song was imported; waiting for the library scan.'
            elif song_route_active(song_route) and state!='requested':
                phase=song_route.get('phase')
                progress={'phase':phase if phase in ('downloading','importing') else 'song-search'}
                if 'progress_percent' in song_route:progress['progress_percent']=song_route['progress_percent']
                detail='Finding the requested song before trying a complete album.'
        atomic(output,{'id':rid,'state':state,'detail':detail,'automatic_allowance':True,'release_group_id':rg,'updated':now(),'retry_at':time.time()+15,**progress})
    except Exception:
        log.exception('Request processing deferred')
        atomic(output,{'id':rid,'state':'retrying','updated':now(),'retry_at':time.time()+60})
    finally:s.db.close()

class UnixServer(socketserver.ThreadingMixIn,http.server.HTTPServer):
    address_family=socket.AF_UNIX
    daemon_threads=True
    request_queue_size=32
    def __init__(self,*args,**kwargs):
        self.workers=ThreadPoolExecutor(max_workers=8,thread_name_prefix='arrify-requests')
        super().__init__(*args,**kwargs)
    def process_request(self,request,address):self.workers.submit(self.process_request_thread,request,address)
    def server_close(self):
        super().server_close();self.workers.shutdown(wait=False,cancel_futures=True)
    def get_request(self):
        conn,_=self.socket.accept();return conn,('local',0)

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,value,status=200):
        body=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def metadata_reply(self,*args,**kwargs):
        result=search(*args,**kwargs);metadata_observation(True);return self.reply(result)
    def do_GET(self):
        try:
            parsed=urlparse(self.path)
            if parsed.path=='/health':
                from .worker_health import read
                worker=read(ROOT/'worker-heartbeat.json')
                try:metadata=json.loads((ROOT/'metadata-health.json').read_text())
                except (OSError,ValueError):metadata={'status':'not-yet-observed'}
                return self.reply({'service':'degraded' if worker['stale'] else 'running','worker':worker,'metadata':metadata},503 if worker['stale'] else 200)
            if parsed.path=='/activity':
                from .activity import snapshot
                return self.reply(snapshot(self.server.config.data,self.server.config.reports))
            if parsed.path=='/stations':
                from .station_subscriptions import directory
                return self.reply(directory(self.server.config.data))
            if parsed.path=='/song':
                q=parse_qs(parsed.query);return self.metadata_reply(self.server.config,'',song={'artist':q.get('artist',[''])[0],'title':q.get('title',[''])[0]})
            if parsed.path=='/explore':
                q=parse_qs(parsed.query);return self.metadata_reply(self.server.config,q.get('q',[''])[0],offset=int(q.get('offset',['0'])[0]),explore=True)
            if parsed.path=='/artist-albums':
                q=parse_qs(parsed.query);return self.metadata_reply(self.server.config,'',offset=int(q.get('offset',['0'])[0]),discography={'artist':q.get('artist',[''])[0],'title':q.get('title',[''])[0]})
            if parsed.path=='/album-group':return self.metadata_reply(self.server.config,'',album_group=parse_qs(parsed.query).get('group',[''])[0])
            if parsed.path=='/album-for-song':
                q=parse_qs(parsed.query);return self.metadata_reply(self.server.config,'',album_song={'artist':q.get('artist',[''])[0],'title':q.get('title',[''])[0]})
            if parsed.path=='/album':return self.metadata_reply(self.server.config,'',album_selection=parse_qs(parsed.query).get('selection',[''])[0])
            if parsed.path=='/selection':return self.metadata_reply(self.server.config,'',recording_id=parse_qs(parsed.query).get('recording_id',[''])[0])
            if parsed.path=='/search':return self.metadata_reply(self.server.config,parse_qs(parsed.query).get('q',[''])[0],int(parse_qs(parsed.query).get('offset',['0'])[0]))
            if parsed.path=='/statuses':
                ids=parse_qs(parsed.query).get('ids',[''])[0].split(',')
                if not 0<len(ids)<=100 or any(not re.fullmatch('[a-f0-9]{32}',rid) for rid in ids):raise ValueError('Invalid request identifiers.')
                return self.reply({'items':[status(rid) for rid in dict.fromkeys(ids)]})
            if re.fullmatch('/status/[a-f0-9]{32}',parsed.path):return self.reply(status(parsed.path.rsplit('/',1)[1]))
            self.reply({'error':'Not found'},404)
        except (BrokenPipeError,ConnectionResetError):
            log.info('Metadata response client disconnected; provider health unchanged')
        except json.JSONDecodeError as exc:
            metadata_observation(False,exc)
            log.exception('Metadata request returned invalid JSON')
            self.reply({'error':'Metadata service returned an invalid response. Please retry shortly.'},503)
        except ValueError as exc:self.reply({'error':str(exc)},400)
        except Exception as exc:
            metadata_observation(False,exc)
            log.exception('Metadata request failed (%s)',type(exc).__name__)
            self.reply({'error':'Metadata lookup is temporarily unavailable. Your library is still available; please retry shortly.'},503)
    def do_POST(self):
        try:
            if self.path not in ('/requests','/stations'):return self.reply({'error':'Not found'},404)
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<4096:raise ValueError('Invalid request.')
            data=json.loads(self.rfile.read(length))
            if self.path=='/stations':
                from .station_subscriptions import request
                return self.reply(request(self.server.config.data,data.get('id')),202)
            with submission_lock:result=submit(self.server.config,data)
            self.reply(result,202)
        except ValueError as exc:self.reply({'error':str(exc)},400)
        except Exception:self.reply({'error':'Request service unavailable'},503)

def main():
    logging.basicConfig(level=logging.INFO)
    config=Config();config.validate()
    ROOT.mkdir(parents=True,exist_ok=True)
    for name in ('inbox','status'):(ROOT/name).mkdir(exist_ok=True)
    # Exclusive lifetime lock: prevents duplicate daemon startup or socket removal.
    lock=open(ROOT/'service.lock','a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    sock=ROOT/'request.sock'
    if sock.exists():sock.unlink()
    server=UnixServer(str(sock),Handler);server.config=config;os.chmod(sock,0o660)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    acquisition_due={}
    from .worker_health import beat
    while True:
        beat(ROOT/'worker-heartbeat.json','request-processing')
        for path in sorted((ROOT/'inbox').glob('*.json')):
            process(config,path)
        # These are the same acquisition operations; expansion.lock serializes
        # against the existing daemon. Every mutation retains existing gates.
        if any((ROOT/'inbox').glob('*.json')):
            from .preparation import prepare
            from .expansion import maintenance
            groups={}
            for path in (ROOT/'status').glob('*.json'):
                record=json.loads(path.read_text())
                if record.get('release_group_id') and record.get('state') in ('priority-queued','prepared','requested','waiting-for-release','retrying','held-for-review'):
                    groups[record['release_group_id']]=record.get('state')
            for rg in sorted(groups):
                if hold_song_group(ROOT,rg):
                    # Metadata preparation may proceed; album acquisition waits for
                    # the per-song route to finish or explicitly enable fallback.
                    if groups[rg]=='priority-queued':
                        scoped=copy.copy(config);scoped.arrify_request_rg=rg
                        try:prepare(scoped,priority_only=True)
                        except Exception:log.exception('Song metadata preparation deferred')
                    continue
                if time.monotonic()<acquisition_due.get(rg,0): continue
                state=groups[rg]
                acquisition_due[rg]=time.monotonic()+(1800 if state=='waiting-for-release' else 120)
                scoped=copy.copy(config);scoped.arrify_request_rg=rg
                for phase in ((prepare,maintenance) if state=='priority-queued' else (maintenance,)):
                    beat(ROOT/'worker-heartbeat.json',phase.__name__)
                    try:phase(scoped,priority_only=True)
                    except Exception:log.exception('Priority acquisition deferred')
        beat(ROOT/'worker-heartbeat.json','waiting')
        time.sleep(10)

if __name__=='__main__':main()

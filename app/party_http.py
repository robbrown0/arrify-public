"""Public party routes never grant playback, account or host access."""
import http.cookies
import hashlib
import json
import re
import threading
import time
from urllib.parse import urlparse
from . import requests as acquisition
from .parties import qr_svg
from .party_sessions import PartyGone


# Slow provider calls may occupy only three HTTP workers, leaving controls free.
CATALOG_SLOTS=threading.BoundedSemaphore(3)
PUBLIC_EVENT_SLOTS={'display':threading.BoundedSemaphore(2),'guest':threading.BoundedSemaphore(2)}

class CatalogBusy(ValueError):pass

def catalog_search(query):
    if not CATALOG_SLOTS.acquire(blocking=False):raise CatalogBusy('Music search is busy. Try again in a moment.')
    try:return acquisition.search(query).get('items',[])
    finally:CATALOG_SLOTS.release()


def cookie_name(party):return 'arrify_party_'+party['id']

def cookie_value(handler,party):
    jar=http.cookies.SimpleCookie()
    try:jar.load(handler.headers.get('Cookie',''))
    except http.cookies.CookieError:pass
    name=cookie_name(party)
    return jar[name].value if name in jar else ''

def state_reply(h,result):
    stable={**result}
    if 'playback' in stable:stable['playback']={k:v for k,v in stable['playback'].items() if k!='server_time'}
    field=result.get('source','')+'_playback'
    if result.get('source') in ('apple','spotify') and field in result:
        stable={**result,field:{k:v for k,v in result[field].items() if k not in ('seen','sequence','position','duration')}}
    etag='"'+hashlib.sha256(json.dumps(stable,sort_keys=True).encode()).hexdigest()[:24]+'"'
    if h.headers.get('If-None-Match')==etag:h.send(304,b'',extra={'ETag':etag})
    else:h.json(result,extra={'ETag':etag})


def public_events(h,service,token,display,secret):
    """Reuse the existing invalidation hub, with separate bounded public capacity.

    Every wake revalidates the capability and guest in SQLite. Events carry no
    queue data, so a revoked guest cannot keep reading a former host's updates.
    """
    hub=service.events
    slots=PUBLIC_EVENT_SLOTS['display' if display else 'guest']
    if not hub or not slots.acquire(blocking=False):
        h.json({'error':'Live updates are busy. Reconnecting shortly.'},503,{'Retry-After':'5'});return
    opened=False;key=None
    try:
        with service.a.lock:
            p=service.row(token=token,display=display)
            if p['ended']:raise PartyGone('ended')
            if not display:service.guest(p,secret)
            uid=p['owner'];key=('party-display' if display else 'party-guest',p['id'])
        opened=hub.open(key)
        if not opened:
            h.json({'error':'Too many live displays for this party.'},429,{'Retry-After':'10'});return
        h.send_response(200)
        for name,value in [('Content-Type','text/event-stream'),('Cache-Control','private, no-store, no-transform'),('X-Accel-Buffering','no'),('Connection','close'),('Referrer-Policy','no-referrer')]:h.send_header(name,value)
        h.end_headers();h.close_connection=True
        version=-1;deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            finished=False
            with service.a.lock:
                try:
                    p=service.row(token=token,display=display)
                    if p['ended']:raise PartyGone('ended')
                    if not display:service.guest(p,secret)
                except (PartyGone,ValueError,PermissionError) as exc:
                    state=getattr(exc,'state','ended')
                    event='terminal';payload={'state':state};finished=True
                if not finished:
                    current=hub.version(uid)
                    event='session' if current!=version else 'ping'
                    payload={'version':current,'revision':p['revision'],'server_time':time.time()}
                    version=current
            h.wfile.write(('id: '+str(version)+'\nevent: '+event+'\ndata: '+json.dumps(payload)+'\n\n').encode());h.wfile.flush()
            if finished:return
            hub.wait(uid,version,5)
    except PermissionError:raise
    except (BrokenPipeError,ConnectionResetError,TimeoutError,OSError):pass
    finally:
        if opened:hub.close(key)
        slots.release()


def public_get(h,path,q):
    service=getattr(h.server,'parties',None)
    if not service:return False
    match=re.fullmatch(r'/api/v1/party-(public|display)/([A-Za-z0-9_-]{32})(?:/(search|catalog|qr.svg|wifi.svg|events|artwork/[0-9]+))?',path)
    if not match:return False
    kind,token,action=match.groups();a=service.a
    a.rate_limit(('party-invitation',h.client_address[0]),1200,60)
    with a.lock:p=service.row(token=token,display=kind=='display')
    if p['ended']:
        if action:raise PartyGone('ended')
        h.json(service.terminal(p));return True
    if action=='events':
        public_events(h,service,token,kind=='display',cookie_value(h,p));return True
    if action=='wifi.svg':
        with a.transaction():
            p=service.row(token=token,display=kind=='display')
            if p['ended']:raise PartyGone('ended')
            data=service.wifi.svg(service.wifi.settings(p['id'])['profile'])
            h.send(200,data,'image/svg+xml',{'Cache-Control':'private, no-store'})
        return True
    if kind=='display':
        if action=='qr.svg':
            with a.lock:
                p=service.row(token=token,display=True)
                if p['ended']:raise PartyGone('ended')
                h.send(200,qr_svg(service.guest_url(p)),'image/svg+xml')
            return True
        if action and action.startswith('artwork/'):
            with a.lock:
                p=service.row(token=token,display=True)
                if p['ended']:raise PartyGone('ended')
                row=a.db.execute("SELECT track_id FROM party_entries WHERE id=? AND party_id=? AND status IN ('queued','playing')",(int(action.split('/')[1]),p['id'])).fetchone()
                if not row or p['source']!='arrify':raise ValueError('Artwork is not available.')
            try:mime,data,source=service.lib.music.artwork(row['track_id'])
            except FileNotFoundError:mime,data,source='',b'','fallback'
            if source=='fallback':
                from .server import WEB
                mime='image/png';data=(WEB/'assets/icon-192.png').read_bytes()
            with a.lock:
                if service.row(token=token,display=True)['ended']:raise PartyGone('ended')
                etag='"'+hashlib.sha256(data).hexdigest()+'"'
                h.send(304 if h.headers.get('If-None-Match')==etag else 200,b'' if h.headers.get('If-None-Match')==etag else data,mime,{'ETag':etag,'Cache-Control':'private, no-cache'})
            return True
        if action:raise ValueError('Not available on the TV display.')
        with a.transaction():result=service.view(p,display=True)
        state_reply(h,result);return True
    secret=cookie_value(h,p)
    with a.transaction():
        p=service.row(token=token)
        if p['ended']:raise PartyGone('ended')
        try:g=service.guest(p,secret)
        except PermissionError:g=None
        if not action:
            result=service.view(p,g['id']) if g else {k:p[k] for k in ('id','name','open','ended','revision','source')}
            if not g:
                result.update(items=[],total=0,state='active');service.add_wifi(result,p)
            if g:result['guest']={'name':g['name'],'csrf':g['csrf']}
            state_reply(h,result);return True
        if not g:raise PermissionError('Join this party first.')
        if p['ended']:raise ValueError('This party has ended.')
    query=q.get('q',[''])[0].strip()[:200]
    if len(query)<2:raise ValueError('Enter at least two characters to search.')
    a.rate_limit(('party-search',g['id']),60,60)
    if action=='search' and p.get('source')=='spotify':
        a.rate_limit(('party-spotify-catalog',p['id']),120,900)
        h.json({'items':service.spotify.search(p['owner'],query)});return True
    if action=='search' and p.get('source')=='apple':
        a.rate_limit(('party-apple-catalog',p['id']),120,900)
        h.json({'items':service.apple.search(p['storefront'],query)});return True
    if action=='catalog' and p.get('source')!='arrify':raise ValueError('This party searches its music service directly.')
    if action=='search':
        result=service.lib.search.query(query,0,25)
        h.json({'items':[service.track(t['id']) for t in result['tracks']]});return True
    if action=='catalog':
        a.rate_limit(('party-catalog',g['id']),10,900)
        a.rate_limit(('party-catalog-budget',p['id']),60,900)
        items=catalog_search(query)
        # The companion issues selections. A guest cannot submit arbitrary artist/title text.
        safe=[{k:x.get(k) for k in ('selection','title','artist','album','requestable')} for x in items[:25]]
        h.json({'items':safe});return True
    raise ValueError('Unknown party action.')


def public_post(h,path,data):
    service=getattr(h.server,'parties',None)
    if not service:return False
    match=re.fullmatch(r'/api/v1/party-public/([A-Za-z0-9_-]{32})/(join|add|request)',path)
    if not match:return False
    if not h.valid_origin():raise PermissionError('Invalid request origin.')
    token,action=match.groups();a=service.a
    a.rate_limit(('party-invitation',h.client_address[0]),1200,60)
    with a.lock:p=service.row(token=token)
    secret=cookie_value(h,p)
    if action=='join':
        a.rate_limit(('party-join',p['id'],h.client_address[0]),500,3600)
        p,g,issued=service.join(token,data.get('name',''),secret)
        extra=None
        if issued:
            from .server import SECURE
            secure=SECURE and not h.headers.get('Origin','').startswith('http://')
            extra={'Set-Cookie':cookie_name(p)+'='+issued+'; Path=/api/v1/party-public/; HttpOnly; SameSite=Lax; Max-Age=604800'+('; Secure' if secure else '')}
        with a.transaction():result=service.view(p,g['id'])
        result['guest']={'name':g['name'],'csrf':g['csrf']};h.json(result,extra=extra);return True
    with a.lock:g=service.guest(p,secret,h.headers.get('X-CSRF-Token',''))
    a.rate_limit(('party-write',g['id']),60,60)
    if action=='request' and p.get('source')!='arrify':raise ValueError('This party searches Apple Music directly; downloads are not requested.')
    if action=='add' and p.get('source')=='apple':
        track=service.apple.track(p['storefront'],data.get('catalog_id'))
        result=service.add_apple(p['owner'],p['id'],track,token,secret,h.headers.get('X-CSRF-Token',''))
    elif action=='add' and p.get('source')=='spotify':
        track=service.spotify.track(p['owner'],data.get('catalog_id'))
        result=service.add_service(p['owner'],p['id'],track,'spotify',token,secret,h.headers.get('X-CSRF-Token',''))
    elif action=='add':result=service.add(token,secret,h.headers.get('X-CSRF-Token',''),data.get('track_id'))
    else:
        import os
        result=service.request(token,secret,h.headers.get('X-CSRF-Token',''),data.get('selection'),h.server.request_submissions,max(0,int(os.environ.get('ARRIFY_REQUESTS_PER_DAY','0'))))
        h.server.request_progress.wake.set()
    h.json(result);return True


def host_get(h,path,user):
    service=h.server.parties;uid=user['id']
    imports=re.fullmatch(r'/api/v1/parties/([a-f0-9]{32})/import-playlists',path)
    if imports:
        from . import party_import
        with service.a.lock:
            p=service.row(imports[1],uid)
            if p['ended']:raise PartyGone('ended')
            if p['source']!='arrify':raise ValueError('Use Apple Music playlists for this party.')
        q=__import__('urllib.parse',fromlist=['parse_qs']).parse_qs(urlparse(h.path).query)
        if 'kind' in q:h.json(party_import.preview(service,uid,p['id'],q['kind'][0],q.get('id',[''])[0]))
        else:h.json({'items':party_import.native_choices(service,uid)})
        return True
    if path in ('/api/v1/parties/guest-wifi','/api/v1/parties/guest-wifi/qr.svg'):
        profile=service.wifi.default(uid)
        if path.endswith('/qr.svg'):h.send(200,service.wifi.svg(profile),'image/svg+xml',{'Cache-Control':'private, no-store'})
        else:h.json({'profile':profile,'editable':True,'qr_url':'/api/v1/parties/guest-wifi/qr.svg' if profile else None})
        return True
    wifi=re.fullmatch(r'/api/v1/parties/([a-f0-9]{32})/wifi(?:/qr.svg)?',path)
    if wifi:
        with service.a.transaction():service.row(wifi[1],uid);result=service.wifi.settings(wifi[1])
        if path.endswith('/qr.svg'):h.send(200,service.wifi.svg(result['profile']),'image/svg+xml',{'Cache-Control':'private, no-store'})
        else:h.json(result)
        return True
    if path=='/api/v1/parties':h.json({'items':service.list(uid)});return True
    search=re.fullmatch(r'/api/v1/parties/([a-f0-9]{32})/search',path)
    if search:
        with service.a.lock:p=service.row(search[1],uid)
        if p['ended']:raise ValueError('This party has ended.')
        query=__import__('urllib.parse',fromlist=['parse_qs']).parse_qs(urlparse(h.path).query).get('q',[''])[0]
        service.a.rate_limit(('party-host-search',uid),60,60)
        if p['source']=='apple':items=service.apple.search(p['storefront'],query)
        elif p['source']=='spotify':items=service.spotify.search(uid,query)
        else:items=[service.track(t['id']) for t in service.lib.search.query(query,0,25)['tracks']]
        h.json({'items':items});return True
    match=re.fullmatch(r'/api/v1/parties/([a-f0-9]{32})(/qr.svg)?',path)
    if not match:return False
    pid,qr=match.groups()
    if qr:
        with service.a.lock:
            p=service.row(pid,uid)
            if p['ended']:raise PartyGone('ended')
            h.send(200,qr_svg(service.guest_url(p)),'image/svg+xml')
    else:state_reply(h,service.host(uid,pid))
    return True


def host_post(h,path,data,user):
    service=h.server.parties;uid=user['id']
    imports=re.fullmatch(r'/api/v1/parties/([a-f0-9]{32})/import-playlists',path)
    if imports:
        from . import party_import
        service.a.rate_limit(('party-playlist-import',uid),12,60)
        h.json(party_import.copy(service,uid,imports[1],data));return True
    spotify_playback=re.fullmatch(r'/api/v1/parties/([a-f0-9]{32})/spotify-playback',path)
    if spotify_playback:
        service.a.rate_limit(('spotify-party-playback',uid),180,60)
        h.json(service.spotify_playback.command(uid,spotify_playback[1],data));return True
    playback=re.fullmatch(r'/api/v1/parties/([a-f0-9]{32})/apple-playback',path)
    if playback:
        h.server.accounts.rate_limit(('apple-party-playback',user['id']),180,60)
        h.json(h.server.parties.apple_playback.command(user['id'],playback[1],data));return True
    if path=='/api/v1/parties/guest-wifi':
        service.a.rate_limit(('guest-wifi-write',uid),30,60)
        profile=service.wifi.default(uid,data)
        service.notify(uid)
        h.json({'profile':profile,'editable':True,'qr_url':'/api/v1/parties/guest-wifi/qr.svg' if profile else None});return True
    wifi=re.fullmatch(r'/api/v1/parties/([a-f0-9]{32})/wifi',path)
    if wifi:
        with service.a.transaction():
            p=service.row(wifi[1],uid)
            if p['ended']:raise PartyGone('ended')
            service.a.rate_limit(('guest-wifi-write',uid),30,60)
            result=service.wifi.settings(p['id'],data);service.touch(p['id'])
        service.notify(uid);h.json(result);return True
    if path=='/api/v1/parties':
        h.server.accounts.rate_limit(('party-create',user['id']),20,3600)
        h.json(h.server.parties.host(user['id'],data=data),201);return True
    match=re.fullmatch(r'/api/v1/parties/([a-f0-9]{32})',path)
    if not match:return False
    service=h.server.parties
    if data.get('action')=='delete':
        h.json(service.delete(user['id'],match[1],data.get('confirm_end') is True));return True
    with service.a.lock:p=service.row(match[1],user['id'])
    if data.get('action')=='add' and p['source']=='spotify':
        service.a.rate_limit(('party-host-add',uid),60,60)
        track=service.spotify.track(uid,data.get('catalog_id'))
        h.json(service.add_service(uid,p['id'],track,'spotify'));return True
    if data.get('action')=='add' and p['source']=='apple':
        service.a.rate_limit(('party-host-add',user['id']),60,60)
        track=service.apple.track(p['storefront'],data.get('catalog_id'))
        h.json(service.add_apple(user['id'],p['id'],track));return True
    h.json(service.host(user['id'],match[1],data));return True

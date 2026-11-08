"""Validated, durable station requests; acquisition remains in the existing engine."""
import fcntl
import json
import os
import re
import time
from pathlib import Path

IDENTITY = re.compile(r'^[a-z0-9][a-z0-9-]{0,79}$')

def slug(channel):
    return 'the-spectrum' if channel == 'thespectrum' else 'sirius-' + channel

def read(path, default):
    try:return json.loads(Path(path).read_text())
    except FileNotFoundError:return default

def atomic(path, value):
    path=Path(path);tmp=path.with_suffix('.tmp')
    with tmp.open('w') as f:
        json.dump(value,f);f.flush();os.fsync(f.fileno())
    tmp.replace(path)
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)

def directory(data):
    value=read(Path(data)/'station-directory.json',{'items':[],'updated':0})
    return {'items':value['items'],'updated':value.get('updated',0)}

def refresh_directory(client,data):
    """One cached public directory, never one upstream call per listener."""
    path=Path(data)/'station-directory.json'
    if time.time()-directory(data)['updated']<86400:return
    value=client.json('https://xmplaylist.com/api/station',86400)
    if value.get('next'):raise ValueError('Station directory pagination changed; retaining previous complete catalog')
    items=[];seen=set()
    for row in value.get('results',[]):
        channel=row.get('deeplink','')
        if not isinstance(channel,str) or not IDENTITY.fullmatch(channel) or channel in seen or row.get('isVisible') is False:continue
        name=row.get('name','')
        if not isinstance(name,str) or not name.strip():continue
        items.append({'id':channel,'slug':slug(channel),'name':name.strip()[:160],
            'number':str(row.get('number',''))[:12],
            'genres':[g[:50] for g in row.get('genres',[]) if isinstance(g,str)][:8],
            'image_url':image_url(row.get('imageUrl')),
            'description':str(row.get('shortDescription',''))[:300]})
        seen.add(channel)
    if not items or len(items)>500:raise ValueError('Invalid station directory; retaining previous catalog')
    atomic(path,{'items':items,'updated':time.time()})

def requested(data):
    return read(Path(data)/'requested-stations.json',{'items':[]})['items']

def request(data,channel):
    if not isinstance(channel,str) or not IDENTITY.fullmatch(channel):raise ValueError('Choose a station from the directory.')
    item=next((x for x in directory(data)['items'] if x['id']==channel),None)
    if not item:raise ValueError('This station is not in the current directory. Refresh and try again.')
    with (Path(data)/'requested-stations.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        rows=requested(data)
        if not any(x['id']==channel for x in rows):
            rows.append(item|{'requested':time.time()})
            atomic(Path(data)/'requested-stations.json',{'items':rows})
    return {'station':item,'state':'scheduled'}

def definitions(data,defaults):
    rows=list(defaults);seen={x[0] for x in rows}
    for item in requested(data):
        channel=item.get('id','')
        if not IDENTITY.fullmatch(channel) or channel in seen or channel=='thespectrum':continue
        rows.append((channel,item['name'],slug(channel)));seen.add(channel)
    return rows

def acquisition_channels(data):
    return {'spectrum' if x['id']=='thespectrum' else slug(x['id']) for x in requested(data)}


def image_url(value):
    # Directory artwork is the channel branding mirrored by xmplaylist.
    if isinstance(value,str) and re.fullmatch(r'https://images\.xmplaylist\.com/channel-images/[a-f0-9]{64}\.webp',value):return value
    return None

def cache_artwork(data,output):
    import urllib.request
    import logging
    folder=Path(output)/'artwork';folder.mkdir(exist_ok=True)
    wanted={slug(x[0]) for x in __import__(__package__+'.stations',fromlist=['STATIONS']).STATIONS}|{x['slug'] for x in requested(data)}|{'the-spectrum'}
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    opener=urllib.request.build_opener(NoRedirect)
    for row in directory(data)['items']:
        url=image_url(row.get('image_url'));path=folder/(row['slug']+'.webp')
        if row['slug'] not in wanted or not url or path.exists():continue
        try:
            with opener.open(urllib.request.Request(url,headers={'User-Agent':'Arrify local channel artwork'}),timeout=10) as response:
                payload=response.read(2*1024*1024+1)
            if len(payload)>2*1024*1024 or payload[:4]!=b'RIFF' or payload[8:12]!=b'WEBP':raise ValueError('Invalid channel artwork')
            temporary=path.with_suffix('.tmp');temporary.write_bytes(payload);temporary.replace(path)
        except Exception:logging.getLogger(__name__).warning('Artwork unavailable for %s; retaining fallback',row['id'])

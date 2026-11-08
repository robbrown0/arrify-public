"""Observed Spectrum airplay via documented public xmplaylist endpoints."""
import json
import logging
import os
from datetime import datetime,timezone
from pathlib import Path
from .matching import key,Ownership
log=logging.getLogger(__name__)
URL='https://xmplaylist.com/api/station/thespectrum'
ARTIST_ALIASES={'Cranberries':'The Cranberries'}

def merge(previous,rows):
    songs={key(x['track']['artists'][0],x['track']['title']):x for x in previous if x.get('track',{}).get('artists')}
    for row in rows:
        t=row.get('track',{})
        if t.get('title') and t.get('artists'):
            k=key(t['artists'][0],t['title'])
            songs[k]=songs.get(k,{})|row
    return list(songs.values())

def collect(http,data):
    import fcntl
    with (data/'spectrum.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        return _collect(http,data)

def _collect(http,data):
    from .airplay import AirplayClient,AirplayDeferred
    path=data/'spectrum-source.json'
    old=json.loads(path.read_text()) if path.exists() else {'results':[]}
    client=AirplayClient(http,data);recent=[];top=[]
    try:recent=[r for r in client.json('https://xmplaylist.com/api/feed',600).get('results',[]) if r.get('channelId')=='thespectrum']
    except AirplayDeferred as e:log.info('%s',e)
    try:top=client.json(URL+'/most-heard?days=60',86400).get('results',[])
    except AirplayDeferred as e:log.info('%s',e)
    rows=merge(old['results'],top+recent)
    payload={'results':rows,'source':URL,'updated':datetime.now(timezone.utc).isoformat()}
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(payload));tmp.replace(path)
    return rows

def candidates(rows):
    ranked=sorted(rows,key=lambda x:-x.get('plays',0)); result=[]
    for rank,x in enumerate(ranked,1):
        t=x['track']; result.append({'artist':ARTIST_ALIASES.get(t['artists'][0],t['artists'][0]),'title':t['title'],'discovery_channel':'spectrum','genres':['Spectrum rock / alternative'],
          'evidence':[{'source':'xmplaylist observed The Spectrum airplay','kind':'current','rank':rank if x.get('plays') else 100,'unranked':not bool(x.get('plays')),'chart_scope':'station',
          'date':datetime.now(timezone.utc).date().isoformat(),'url':URL+'/most-heard?days=60' if x.get('plays') else URL,'independent_family':'xmplaylist','plays':x.get('plays'),'weight':0.8 if x.get('plays') else 0}]})
    return result

def publish(data,catalog,output,source_file='spectrum-source.json',slug='the-spectrum',name='The Spectrum',status_prefix='spectrum',section='channels',description=None):
    """Write only a playlist manifest; never modify the player's catalog database."""
    import sqlite3
    source=json.loads((data/source_file).read_text());rows=source['results'];source_url=source.get('source',URL)
    c=sqlite3.connect('file:'+str(catalog)+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='track_metadata'").fetchone():
        local=[dict(r) for r in c.execute("SELECT t.extension,t.title raw_title,t.artist raw_artist,t.album raw_album,coalesce(nullif(m.title,''),t.title) title,coalesce(nullif(m.artist,''),t.artist) artist,coalesce(nullif(m.album,''),t.album) album FROM tracks t LEFT JOIN track_metadata m ON m.track_id=t.id WHERE t.available=1")]
    else:
        local=[dict(r) for r in c.execute('SELECT title,artist,album FROM tracks WHERE available=1')]
    c.close()
    index={}
    for t in local:index.setdefault(key(t['artist'],t['title']),[]).append(t)
    tracks=[];owned=0;inventory=[]
    maximum=max((float(x.get('plays',0) or 0) for x in rows),default=1) or 1
    for x in rows:
        t=x['track']; choices=[]
        for credited in t['artists']:
            artist=ARTIST_ALIASES.get(credited,credited)
            choices.extend(v for v in index.get(key(artist,t['title']),[]) if Ownership([v]).owns({'artist':artist,'title':t['title']}))
        choices.sort(key=lambda v:v.get('extension','').lower() not in ('.flac','.alac','.wav','.aiff','.ape','.wv'))
        if choices:
            v=choices[0];tracks.append({'title':v.get('raw_title',v['title']),'artists':[v.get('raw_artist',v['artist'])],'album':v.get('raw_album',v['album'])});owned+=1
        else:tracks.append({'title':t['title'],'artists':t['artists']})
        tracks[-1]['weight']=round(1+11*(max(0,float(x.get('plays',0) or 0))/maximum)**0.5,2)
        inventory.append({'artist':t['artists'][0],'title':t['title'],'owned':bool(choices),'status':'owned' if choices else 'pending-album-resolution','observed_plays':x.get('plays')})
    manifest=[{'slug':slug,'name':name,'section':section,'description':description or f'Observed {name} airplay, with core songs in heavier rotation. Local music; independent of SiriusXM.','tracks':tracks}]
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    temp=output.with_suffix('.tmp');temp.write_text(json.dumps(manifest,ensure_ascii=False));temp.replace(output)
    report={'observed_songs':len(rows),'owned_songs':owned,'missing_songs':len(rows)-owned,'updated':datetime.now(timezone.utc).isoformat(),'source':source_url,'songs':inventory}
    dbpath=data/'discovery.sqlite3'
    if dbpath.exists():
        db=sqlite3.connect('file:'+str(dbpath)+'?mode=ro',uri=True)
        states=dict(db.execute('SELECT rg,state FROM requests'))
        for song in inventory:
            row=db.execute('SELECT payload FROM cache WHERE key=?',('resolution:'+key(song['artist'],song['title']),)).fetchone()
            if row:
                resolved=json.loads(row[0]); song['album']=resolved.get('album','');song['release_group_id']=resolved.get('release_group_id','')
                if not song['owned']:song['status']=states.get(song['release_group_id'],resolved.get('resolution_status','pending-album-resolution'))
        db.close()
    (data/(status_prefix+'-status.json')).write_text(json.dumps(report,indent=2))
    import csv
    with (data/(status_prefix+'-songs.csv')).open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['artist','title','owned','status','observed_plays','album','release_group_id']);writer.writeheader();writer.writerows(inventory)
    return report

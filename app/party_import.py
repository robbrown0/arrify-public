"""Owner-only playlist copying into the existing transactional party queue."""
import re
from collections import Counter
from .playlists import Playlists
from .parties import song_key
from .party_sessions import PartyGone


def native_source(service,uid,kind,identifier):
    if kind=='personal':
        if not re.fullmatch('[0-9]{1,20}',str(identifier)):raise ValueError('Choose an Arrify playlist.')
        try:p=Playlists(service.a,service.lib).get(uid,int(identifier))
        except LookupError:raise PermissionError('That playlist is not available to you.') from None
        return p['name'],p['track_ids']
    if kind=='collection':
        collections={p['slug']:p for p in service.lib.collections() if p.get('section')=='playlists'}
        if identifier not in collections:raise ValueError('Choose a playlist from the Playlists section.')
        return collections[identifier]['name'],service.lib.collection_ids(identifier)
    raise ValueError('Choose a playlist source.')


def native_choices(service,uid):
    personal=Playlists(service.a,service.lib);rows=[];seen=set()
    for scope in ('mine','shared'):
        for p in personal.rows(uid,scope):
            if p['id'] in seen:continue
            seen.add(p['id']);rows.append(dict(id=str(p['id']),kind='personal',name=p['name'],group='mine' if p['owner_id']==uid else 'shared',owner=p['owner'],count=len(p['track_ids']),artwork='/api/v1/artwork/'+str(p['track_ids'][0]) if p['track_ids'] else ''))
    rows.extend(dict(id=p['slug'],kind='collection',name=p['name'],group='collection',owner='Arrify',count=p.get('tracks',0),artwork=p.get('artwork') or '') for p in service.lib.collections() if p.get('section')=='playlists')
    return rows


def preview(service,uid,pid,kind,identifier):
    with service.a.transaction():
        p=service.row(pid,uid)
        if p['ended']:raise PartyGone('ended')
        if p['source']!='arrify':raise ValueError('This party imports Apple Music playlists.')
        name,ids=native_source(service,uid,kind,identifier);tracks=[]
        for tid in ids[:500]:
            try:tracks.append(service.lib.track(tid))
            except ValueError:continue
        return dict(name=name,tracks=tracks,total=len(ids),truncated=len(ids)>500)


def copy(service,uid,pid,data):
    # Slow catalog verification stays outside the SQLite write transaction.
    with service.a.lock:
        p=service.row(pid,uid)
        if p['ended']:raise PartyGone('ended')
    ids=data.get('track_ids')
    if not isinstance(ids,list) or not 1<=len(ids)<=500:raise ValueError('Choose between 1 and 500 songs.')
    if p['source']=='apple':
        if data.get('kind')!='apple':raise ValueError('This party imports Apple Music playlists.')
        tracks=service.apple.tracks(p['storefront'],ids)
    elif p['source']=='spotify':
        if data.get('kind')!='spotify':raise ValueError('This party imports Spotify playlists.')
        verified=service.spotify.playlist_tracks(uid,data.get('playlist_id'))['tracks']
        if any(not isinstance(i,str) for i in ids):raise ValueError('Choose Spotify songs from the playlist.')
        remaining=Counter(ids);tracks=[]
        for t in verified:
            if remaining[t['id']]>0:tracks.append(t);remaining[t['id']]-=1
    else:
        if data.get('kind') in ('apple','spotify'):raise ValueError('This party imports Arrify playlists.')
        with service.a.transaction():
            _,source_ids=native_source(service,uid,data.get('kind'),data.get('playlist_id'))
            for tid in ids:
                if isinstance(tid,bool) or not isinstance(tid,int) or tid not in source_ids:raise ValueError('A selected song is no longer in that playlist.')
            tracks=[];remaining=Counter(ids)
            for tid in source_ids:
                if remaining[tid]<=0:continue
                remaining[tid]-=1
                try:tracks.append(service.lib.track(tid))
                except ValueError:pass
    result={'added':0,'duplicates':0,'unavailable':len(ids)-len(tracks),'full':0}
    with service.a.transaction():
        current=service.row(pid,uid)
        if current['ended']:raise PartyGone('ended')
        if current['source']!=p['source']:raise ValueError('The party music source changed.')
        # Recheck playlist visibility/ownership at the actual write boundary.
        if p['source']=='arrify':
            _,allowed=native_source(service,uid,data.get('kind'),data.get('playlist_id'))
            if any(t['id'] not in allowed for t in tracks):raise ValueError('The playlist changed. Preview it again.')
        for t in tracks:
            key=p['source']+':'+(t.get('isrc') or str(t['id'])) if p['source']!='arrify' else song_key(t)
            try:service._insert(current,0 if p['source']!='arrify' else t['id'],key,charged=False,metadata=t if p['source']!='arrify' else None,sync=False)
            except ValueError as error:
                if str(error)=='Already in queue.':result['duplicates']+=1
                elif str(error)=='The party queue is full.':result['full']+=1
                else:raise
            else:result['added']+=1
        if result['added']:service.touch(pid);service.sync(current)
        result['party']=service.view(current,host=True)
    if result['added']:service.notify(uid)
    return result

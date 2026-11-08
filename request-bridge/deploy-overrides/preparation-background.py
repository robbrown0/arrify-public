"""Feed the acquisition queue from verified metadata without waiting for a full scan."""
import fcntl
import json
import logging
import time
from collections import Counter
from datetime import date,datetime,timezone
from .api import HTTP,Lidarr
from .store import Store,now
from .budget import Budget
from .matching import Ownership,key,primary,album_title
from .sources import score
from .coverage import select_balanced
from .acquire import Acquirer,album_groups,available_groups
from .request_priority import pending
from .channel_bootstrap import sparse_channels,select as select_bootstrap
log=logging.getLogger(__name__)

IN_FLIGHT={'reserved','adding-artist','adding-album','prepared','requested','search-pending'}

def pending_capacity(rows,target):
    return max(0,target-sum(r['state'] in IN_FLIGHT for r in rows))

def cached_songs(store):
    songs={}
    from .focus import for_store
    focus=for_store(store)
    for row in store.db.execute("SELECT fetched,payload FROM cache WHERE key LIKE 'resolution:%'"):
        if (datetime.now(timezone.utc)-datetime.fromisoformat(row['fetched'])).total_seconds()>86400*30: continue
        t=json.loads(row['payload'])
        if t.get('resolution_status')!='resolved' or not t.get('release_group_id') or not t.get('release_id') or not t.get('recording_id') or not t.get('artist_mbid') or not t.get('album'): continue
        channel=t.get('discovery_channel','')
        if focus is not None:
            target=focus.get(key(t['artist'],t['title']))
            if not target:continue
            t.update(discovery_channel=target.get('discovery_channel','arrify-chart-focus'),evidence=target['evidence'])
            channel='arrify-chart-focus'
        earliest='1900' if channel in ('spectrum','imported-playlist','arrify-chart-focus') or channel.startswith('sirius-') else '2015'
        if not earliest<=t.get('release_date','')[:4]<=str(date.today().year): continue
        if not t.get('evidence'): continue
        t['score']=score(t)
        songs[t['recording_id']]=t
    return list(songs.values())

def prepare(config):
    if not (config.expansion and config.acquire and config.reviewed): return []
    with open(config.data/'expansion.lock','a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return []
        s=Store(config.data/'discovery.sqlite3')
        try:
            if pending(s): return []
            budget=Budget.for_config(s,config)
            if budget.remaining()<100_000_000: return []
            requests=s.db.execute('SELECT rg,state FROM requests').fetchall()
            limit=min(config.batch_size,pending_capacity(requests,config.prepared_target))
            if not limit: return []
            l=Lidarr(config,HTTP(s)); l.capabilities()
            artists=l.get('artist'); names={a['id']:a['artistName'] for a in artists}
            artist_map={a['foreignArtistId']:a for a in artists}
            songs=cached_songs(s)
            from .album_index import preparation_albums
            albums={a['foreignAlbumId']:a for a in preparation_albums(l,s,{t['release_group_id'] for t in songs})}
            queue=l.get('queue',pageSize=1000,includeUnknownArtistItems=True)
            if queue.get('totalRecords',0)>1000: return []
            queued={q.get('albumId') for q in queue.get('records',[])}
            from .focus import for_store
            focus=for_store(s)
            needed_groups={t['release_group_id'] for t in songs} if focus is not None else None
            tracks=[json.loads(r[0]) for r in s.db.execute('SELECT payload FROM files')]
            for a in albums.values():
                if not a.get('statistics',{}).get('trackFileCount'): continue
                if needed_groups is not None and a['foreignAlbumId'] not in needed_groups: continue
                if pending(s): return []
                for t in l.get('track',albumId=a['id']):
                    tracks.append(t|{'artist':names[a['artistId']],'owned':bool(t.get('hasFile') or t.get('trackFileId')),
                                     'recording_ids':[t['foreignRecordingId']] if t.get('foreignRecordingId') else [],'isrcs':t.get('isrcs',[])})
            ownership=Ownership(tracks)
            covered=Counter(int(t['release_date'][:4]) for t in songs if ownership.owns(t))
            states={r['rg']:r['state'] for r in requests}
            for t in songs:
                if states.get(t['release_group_id']) in IN_FLIGHT and not ownership.owns(t): covered[int(t['release_date'][:4])]+=1
            groups=album_groups([t for t in songs if not ownership.owns(t)])
            local={(primary(t.get('artist','')),album_title(t.get('album',''))) for t in tracks if t.get('owned') and t.get('album')}
            for g in groups:
                g['local_album_present']=(primary(g['artist']),album_title(g['album'])) in local
                # Each recording is linked to this studio release group. Compatible-edition imports verify actual hits.
                g['release_validation']='recording-linked-album-group'
            eligible=available_groups(groups,s,albums,queued)
            profiles={p['id']:p for p in l.get('metadataprofile')}
            def usable(g):
                artist=artist_map.get(g['artist_mbid'])
                if not artist: return True
                if not artist.get('monitored'): return False
                profile=profiles.get(artist['metadataProfileId'],{})
                return any(t['allowed'] and t['albumType']['name']==g['album_type'] for t in profile.get('primaryAlbumTypes',[]))
            usable_groups=[g for g in eligible if usable(g)]
            spectrum=[g for g in usable_groups if any(t.get('discovery_channel') in ('spectrum','imported-playlist') or t.get('discovery_channel','').startswith('sirius-') for t in g['songs'])]
            jazz=[g for g in spectrum if any(t.get('discovery_channel')=='imported-playlist' for t in g['songs'])]
            chosen=select_balanced(jazz,min(10,limit),covered,config.core_rank)
            jazz_ids={g['release_group_id'] for g in chosen}
            chosen+=select_balanced([g for g in spectrum if g['release_group_id'] not in jazz_ids],min(5,limit-len(chosen)),covered,config.core_rank)
            selected={g['release_group_id'] for g in chosen}
            selected_artists={g['artist_mbid'] for g in chosen}
            chosen+=select_balanced([g for g in usable_groups if g['release_group_id'] not in selected and g['artist_mbid'] not in selected_artists],limit-len(chosen),covered,config.core_rank)
            from .focus import for_store,select_groups
            focus=for_store(s)
            if focus is not None:chosen=select_groups(usable_groups,focus,limit)
            bootstrap=select_bootstrap(usable_groups,sparse_channels(config.data),min(3,limit))
            boosted={g['release_group_id'] for g in bootstrap}
            chosen=(bootstrap+[g for g in chosen if g['release_group_id'] not in boosted])[:limit]
            acquirer=Acquirer(l,s,config); result=[]; started=time.monotonic()
            for g in chosen:
                if pending(s): break
                if time.monotonic()-started>config.preparation_seconds: break
                s.cache('prepared-group:'+g['release_group_id'],g)
                state=acquirer.request(g)
                s.decision(g['release_group_id'],g['artist'],g['album'],state,json.dumps([t['title'] for t in g['songs']]))
                result.append({'artist':g['artist'],'album':g['album'],'state':state})
            s.audit('preparation-batch',result)
            log.info('Acquisition preparation: %s albums considered; %s ready',len(result),sum(r['state']=='prepared' for r in result))
            return result
        finally: s.db.close()

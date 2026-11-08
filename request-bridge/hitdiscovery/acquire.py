import time
from .matching import norm
from .store import now
from .request_priority import explicit


def album_groups(songs):
    groups={}
    for s in songs:
        rg=s.get('release_group_id')
        if not rg: continue
        if rg not in groups:
            groups[rg]={'release_group_id':rg,'artist':s['artist'],'artist_mbid':s['artist_mbid'],
                        'album':s['album'],'album_type':s.get('album_type','Album'),'songs':[], 'genres':s.get('genres',[])}
        groups[rg]['songs'].append(s)
    for g in groups.values():
        g['missing_hits']=len(g['songs']); g['score']=round(max(s['score'] for s in g['songs'])+min(20,5*(len(g['songs'])-1)),2)
    return sorted(groups.values(),key=lambda g:(-g['score'],-g['missing_hits'],g['artist']))


def diversify(groups,limit):
    selected=[]; artists=set(); genre_count={}
    remaining=list(groups)
    while remaining and len(selected)<limit:
        eligible=[g for g in remaining if g['artist_mbid'] not in artists]
        if not eligible: break  # maximum one album per artist per cycle
        g=max(eligible,key=lambda g:g['score']-min(12,3*sum(genre_count.get(x,0) for x in g.get('genres',[])[:1])))
        selected.append(g); remaining.remove(g); artists.add(g['artist_mbid'])
        for genre in g.get('genres',[])[:1]: genre_count[genre]=genre_count.get(genre,0)+1
    return selected

class Acquirer:
    def __init__(self,lidarr,store,config): self.l=lidarr; self.s=store; self.c=config
    def reconcile(self):
        albums={a['foreignAlbumId']:a for a in self.l.get('album')}
        for r in self.s.db.execute('SELECT * FROM requests').fetchall():
            a=albums.get(r['rg'])
            if a and a.get('statistics',{}).get('percentOfTracks',0)>=100 and a.get('statistics',{}).get('trackFileCount',0)>0:
                self.s.request_state(r['rg'],'downloaded',a['id'])
    def request(self,g):
        if not (self.c.acquire and self.c.reviewed): return 'dry-run'
        if g.get('release_validation') in ('missing-release-metadata','no-verified-edition-containing-all-hits','metadata-failure'): return 'edition-review-required'
        if g.get('local_album_present'): return 'already-in-local-collection'
        rg=g['release_group_id']
        requested=explicit(self.s,rg)
        if self.c.expansion:
            from .budget import Budget
            if Budget.for_config(self.s,self.c).remaining()<100_000_000: return 'acquisition-budget-reached'
        if self.s.db.execute("SELECT 1 FROM decisions WHERE rg=? AND state='rejected'",(rg,)).fetchone(): return 'rejected'
        if self.s.db.execute('SELECT 1 FROM requests WHERE rg=?',(rg,)).fetchone(): return 'already-recorded'
        # Re-read immediately before acting; never trust a previous scan for write decisions.
        albums=self.l.get('album'); artists=self.l.get('artist')
        a=next((x for x in albums if x['foreignAlbumId']==rg),None)
        artist=next((x for x in artists if x['foreignArtistId']==g['artist_mbid']),None)
        if a and a.get('statistics',{}).get('trackFileCount',0): return 'existing-files-no-replacement'
        queue=self.l.get('queue',pageSize=1000,includeUnknownArtistItems=True)
        if queue.get('totalRecords',0)>1000: raise RuntimeError('Queue exceeds safe inspection limit')
        if a and any(x.get('albumId')==a['id'] or x.get('album',{}).get('id')==a['id'] for x in queue.get('records',[])): return 'already-queued'
        if artist and not artist.get('monitored') and not requested: return 'artist-unmonitored-review-required'
        roots=self.l.get('rootfolder'); root=next((r for r in roots if r['path']==self.c.root),None) if self.c.root else (roots[0] if len(roots)==1 else None)
        if not root: raise RuntimeError('Set ROOT_FOLDER to an existing Lidarr root')
        qid=self.c.quality_id or root['defaultQualityProfileId']; mid=self.c.metadata_id or root['defaultMetadataProfileId']
        if qid not in {x['id'] for x in self.l.get('qualityprofile')}: raise RuntimeError('Unknown quality profile')
        profiles=self.l.get('metadataprofile'); profile=next((x for x in profiles if x['id']==(artist['metadataProfileId'] if artist else mid)),None)
        profile_excludes=not profile or not any(x['allowed'] and x['albumType']['name']==g.get('album_type','Album') for x in profile['primaryAlbumTypes'])
        if profile_excludes and (artist or self.c.metadata_id) and not requested: return 'metadata-profile-excludes-release-type'
        if not self.s.reserve(rg,self.c.weekly_limit): return 'weekly-limit-or-duplicate'
        try:
            self.s.audit('request-reserved',{'release_group_id':rg,'album':g['album']})
            if artist and requested:
                artist=dict(artist,monitored=True)
                if profile_excludes:
                    from .metadata import ensure as ensure_metadata
                    artist['metadataProfileId']=ensure_metadata(self.l,artist['metadataProfileId'])
                self.l.write('artist/'+str(artist['id']),'PUT',artist)
            if not artist:
                if profile_excludes:
                    from .metadata import ensure as ensure_metadata
                    mid=ensure_metadata(self.l,root['defaultMetadataProfileId'])
                if not self.c.quality_id:
                    from .quality import ensure
                    qid=ensure(self.l,root['defaultQualityProfileId'])
                found=self.l.get('artist/lookup',term='lidarr:'+g['artist_mbid'])
                artist=next((x for x in found if x['foreignArtistId']==g['artist_mbid']),None)
                if not artist: raise RuntimeError('Artist MBID absent from Lidarr metadata provider')
                artist={**artist,'qualityProfileId':qid,'metadataProfileId':mid,'rootFolderPath':root['path'],
                        'monitored':True,'monitorNewItems':'none','addOptions':{'monitor':'none','searchForMissingAlbums':False}}
                artist.pop('id',None)
                self.s.request_state(rg,'adding-artist')
                artist=self.l.write('artist','POST',artist)
                if not artist.get('monitored'):
                    artist['monitored']=True
                    artist['monitorNewItems']='none'
                    self.l.write('artist/'+str(artist['id']),'PUT',artist)
                # Wait for refresh to create album records; never issue a broad artist search.
                for _ in range(12):
                    current=self.l.get('album',artistId=artist['id'])
                    a=next((x for x in current if x['foreignAlbumId']==rg),None)
                    if a: break
                    time.sleep(5)
            if not a:
                found=self.l.get('album/lookup',term='lidarr:'+rg)
                a=next((x for x in found if x['foreignAlbumId']==rg),None)
                if not a: raise RuntimeError('Release group absent from Lidarr metadata provider')
                a={**a,'artistId':artist['id'],'artist':artist,'monitored':False,'addOptions':{'searchForNewAlbum':False}}
                a.pop('id',None)
                self.s.request_state(rg,'adding-album')
                # Artist refresh can create the target between lookup and POST.
                current=lambda: next((x for x in self.l.get('album',artistId=artist['id']) if x['foreignAlbumId']==rg and x.get('artistId')==artist['id']),None)
                existing=current()
                if existing:a=existing
                else:
                    try:a=self.l.write('album','POST',a)
                    except Exception:
                        existing=current()
                        if not existing:raise
                        a=existing
            # Verify addOptions=none was respected before enabling anything.
            if not any(x['id']==artist['id'] for x in artists):
                unrelated=[x for x in self.l.get('album',artistId=artist['id']) if x['foreignAlbumId']!=rg and x.get('monitored')]
                if unrelated: raise RuntimeError('New artist has unrelated monitored albums; stopped before search')
            if self.c.expansion:
                a['anyReleaseOk']=True
                a['monitored']=requested
                self.l.write('album/'+str(a['id']),'PUT',a)
                self.s.request_state(rg,'prepared',a['id'])
                return 'prepared'
            if g.get('release_id'):
                releases=a.get('releases',[])
                if not any(r.get('foreignReleaseId')==g['release_id'] for r in releases):
                    raise RuntimeError('Verified edition not yet available in Lidarr; held for review')
                a['anyReleaseOk']=False
                for release in releases: release['monitored']=release.get('foreignReleaseId')==g['release_id']
                self.l.write('album/'+str(a['id']),'PUT',a)
            self.s.request_state(rg,'monitoring',a['id'])
            self.l.write('album/monitor','PUT',{'albumIds':[a['id']],'monitored':True})
            # Persist before command: ambiguous network failures are held for review, never blindly retried.
            self.s.request_state(rg,'search-pending',a['id'])
            command=self.l.write('command','POST',{'name':'AlbumSearch','albumIds':[a['id']]})
            self.s.request_state(rg,'requested',a['id'],str(command.get('id','')))
            self.s.audit('album-requested',{'release_group_id':rg,'album_id':a['id'],'command_id':command.get('id')})
            return 'requested'
        except Exception as e:
            self.s.request_state(rg,'held-for-review',detail=str(e)); self.s.audit('request-held',{'release_group_id':rg,'error':str(e)})
            return 'held-for-review'


def available_groups(groups,store,album_map,queued):
    """Spend each cycle's selection slots on albums not already handled."""
    result=[]
    for g in groups:
        rg=g['release_group_id']; album=album_map.get(rg)
        if g.get('local_album_present'): continue
        if g.get('release_validation') in ('missing-release-metadata','no-verified-edition-containing-all-hits','metadata-failure'): continue
        if store.db.execute('SELECT 1 FROM requests WHERE rg=?',(rg,)).fetchone(): continue
        if store.db.execute("SELECT 1 FROM decisions WHERE rg=? AND state='rejected'",(rg,)).fetchone(): continue
        if album and (album['id'] in queued or album.get('statistics',{}).get('trackFileCount',0)): continue
        result.append(g)
    return result


def handled_resolution(store,candidate):
    """Known queued/rejected songs stay reportable without exhausting new discovery."""
    from .matching import key
    cached=store.cached('resolution:'+key(candidate['artist'],candidate['title']),ttl=86400*365)
    if not cached or not cached.get('release_group_id'): return None
    rg=cached['release_group_id']
    handled=store.db.execute('SELECT 1 FROM requests WHERE rg=?',(rg,)).fetchone() or store.db.execute("SELECT 1 FROM decisions WHERE rg=? AND state='rejected'",(rg,)).fetchone()
    return cached|{'evidence':candidate['evidence']} if handled else None

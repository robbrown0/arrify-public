"""Bounded acquisition and import checks independent of an interactive session."""
import fcntl
import json
import logging
from datetime import datetime,timezone
from .api import HTTP,Lidarr
from .store import Store,now
from .budget import Budget,choose_release,tier
from .matching import title
from .request_priority import explicit,download_limit,pending as has_pending
log=logging.getLogger(__name__)

def search_releases(lidarr,budget,rg,album_id):
    # Record attempts before network IO so a slow indexer cannot starve other albums.
    budget.put('last-search:'+rg,now())
    try:
        return lidarr.get('release',albumId=album_id)
    except Exception as exc:
        log.warning('Release search deferred for album %s: %s',album_id,exc)
        budget.s.audit('release-search-deferred',{'rg':rg,'album_id':album_id,'error':str(exc)})
        return None

def age(stamp):
    return (datetime.now(timezone.utc)-datetime.fromisoformat(stamp)).total_seconds()

def missing_tracks_failure(item):
    return item.get('trackedDownloadState')=='importFailed' and 'missing tracks' in json.dumps(item.get('statusMessages',[])).lower()

def maintenance(config):
    if not (config.expansion and config.acquire and config.reviewed): return
    with open(config.data/'expansion.lock','a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return
        s=Store(config.data/'discovery.sqlite3'); budget=Budget.for_config(s,config); l=Lidarr(config,HTTP(s))
        try:
            l.capabilities()
            albums={a['foreignAlbumId']:a for a in l.get('album')}
            queue=l.get('queue',pageSize=1000,includeUnknownArtistItems=True)
            if queue.get('totalRecords',0)>1000: raise RuntimeError('Cannot safely inspect full queue')
            records=queue.get('records',[])
            requests=s.db.execute('SELECT * FROM requests ORDER BY reserved').fetchall()
            requests=sorted(requests,key=lambda r:(r['state'] not in ('prepared','requested'),budget.get('last-search:'+r['rg'],'') or '',r['reserved']))
            from .focus import for_store,release_groups
            focus=for_store(s)
            focused_groups=release_groups(s,focus) if focus is not None else None
            report=json.loads((config.reports/'latest.json').read_text()) if (config.reports/'latest.json').exists() else {}
            groups={g['release_group_id']:g for g in report.get('albums',[])}
            owned_ids={a['id'] for rg,a in albums.items() if any(r['rg']==rg for r in requests)}
            # Reconcile arrivals from the initial enabled run into the same cumulative budget.
            for q in records:
                if q.get('albumId') not in owned_ids: continue
                rg=next(rg for rg,a in albums.items() if a['id']==q['albumId'])
                did=q.get('downloadId','')
                existing=s.db.execute('SELECT id FROM transfers WHERE download_id=?',(did,)).fetchone() if did else None
                if not existing:
                    pending=s.db.execute("SELECT id FROM transfers WHERE rg=? AND download_id IS NULL AND state='queued' ORDER BY id DESC LIMIT 1",(rg,)).fetchone()
                    if pending:
                        budget.state(pending[0],'queued',did); continue
                    tid=budget.reserve(rg,'existing:'+did,0,int(q.get('size',0)),tier(q))
                    if tid: budget.state(tid,'queued',did,'Pre-existing authorized discovery transfer')
            # The controlled profile belongs only to artists created by discovery.
            for p in l.get('qualityprofile'):
                if p['name']!='Discovery - Lossless preferred': continue
                changed=False
                for item in p['items']:
                    if item.get('name')=='Poor Quality Lossy':
                        if not item.get('allowed'): item['allowed']=True; changed=True
                        for child in item.get('items',[]):
                            enabled=child.get('quality',{}).get('id') in (5,19,22,33)
                            if child.get('allowed')!=enabled: child['allowed']=enabled; changed=True
                if changed:
                    p['upgradeAllowed']=False; l.write('qualityprofile/'+str(p['id']),'PUT',p)
            active=sum(q.get('status') not in ('completed','failed','warning') for q in records)
            decisions=[]; searches=0
            for req in requests:
                if has_pending(s): break  # Yield the shared lock to interactive acquisition.
                rg=req['rg']; a=albums.get(rg)
                if not a: continue
                if req['state'] in ('reserved','adding-artist','adding-album'):
                    if age(req['reserved'])<600 or s.db.execute('SELECT 1 FROM transfers WHERE rg=?',(rg,)).fetchone(): continue
                    s.request_state(rg,'prepared',a['id'],'Recovered completed preparation after restart')
                # Only discovery-requested albums are controlled; originals and unrelated albums are untouched.
                monitored=explicit(s,rg)
                if a.get('monitored')!=monitored or not a.get('anyReleaseOk',True):
                    a['monitored']=monitored; a['anyReleaseOk']=True
                    l.write('album/'+str(a['id']),'PUT',a)
                q=next((x for x in records if x.get('albumId')==a['id']),None)
                files=a.get('statistics',{}).get('trackFileCount',0)
                if files and not q:
                    if not s.db.execute('SELECT 1 FROM transfers WHERE rg=?',(rg,)).fetchone():
                        tid=budget.reserve(rg,'initial-import:'+rg,0,int(a.get('statistics',{}).get('sizeOnDisk',0)),4)
                        if tid: budget.state(tid,'downloaded',detail='Imported from the initial authorized cycle')
                    tracks=l.get('track',albumId=a['id'])
                    actual={title(t['title']) for t in tracks if t.get('hasFile') or t.get('trackFileId')}
                    wanted_group=groups.get(rg) or s.cached('prepared-group:'+rg) or {}
                    wanted={title(t['title']) for t in wanted_group.get('songs',[])}
                    state='downloaded' if wanted.issubset(actual) else 'partial-owned'
                    s.request_state(rg,state,a['id'],'Existing imported tracks protected from replacement')
                    for t in s.db.execute('SELECT id FROM transfers WHERE rg=? AND state IN (?,?,?)',(rg,'queued','import-retry','reserved')).fetchall(): budget.state(t[0],'downloaded')
                    decisions.append({'album':a['title'],'status':state,'missing_hit_titles':sorted(wanted-actual)})
                    continue
                if files and not (q and missing_tracks_failure(q)):
                    decisions.append({'album':a['title'],'status':'existing-files-protected'})
                    continue
                if req['state']=='held-for-review' and 'Verified edition not yet available' not in req['detail']:
                    decisions.append({'album':a['title'],'status':'ambiguous-write-held'}); continue
                if q:
                    did=q.get('downloadId',''); tr=s.db.execute('SELECT * FROM transfers WHERE download_id=? ORDER BY id DESC LIMIT 1',(did,)).fetchone()
                    if q.get('trackedDownloadState')=='importFailed' or q.get('status')=='failed':
                        retrykey='import-retry:'+did; retry=budget.get(retrykey)
                        incomplete=missing_tracks_failure(q)
                        if not retry and q.get('status')!='failed' and not incomplete:
                            l.write('command','POST',{'name':'DownloadedAlbumsScan','path':q['outputPath'],'downloadClientId':did,'importMode':'copy'})
                            budget.put(retrykey,now())
                            if tr: budget.state(tr['id'],'import-retry',did)
                            decisions.append({'album':a['title'],'status':'retrying-import-compatible-edition'}); continue
                        if retry and age(retry)<max(300,config.maintenance_seconds) and not incomplete:
                            decisions.append({'album':a['title'],'status':'waiting-for-import-retry'}); continue
                        # Keep downloader contents and files. Removing this completed queue item permits an explicit alternative.
                        l.write('queue/'+str(q['id'])+'?removeFromClient=false&blocklist=true&skipRedownload=true&changeCategory=false','DELETE',{})
                        if tr: budget.state(tr['id'],'failed',did,'Import/download failed after compatible-edition retry; original files retained')
                        s.request_state(rg,'prepared',a['id'],'Retry at next lower quality; original download retained')
                        q=None
                    else:
                        decisions.append({'album':a['title'],'status':q.get('trackedDownloadState') or q.get('status')}); continue
                if explicit(s,rg):
                    decisions.append({'album':a['title'],'status':'handled-by-user-request-worker'})
                    continue
                if focused_groups is not None and rg not in focused_groups:
                    decisions.append({'album':a['title'],'status':'deferred-during-chart-focus'})
                    continue
                if active>=download_limit(s,config,rg) or budget.remaining()<10_000_000:
                    decisions.append({'album':a['title'],'status':'waiting-for-capacity-or-budget'}); continue
                if s.db.execute("SELECT 1 FROM transfers WHERE rg=? AND state='held-for-review'",(rg,)).fetchone(): continue
                attempted=s.db.execute('SELECT * FROM transfers WHERE rg=? ORDER BY id',(rg,)).fetchall()
                failures=[r for r in attempted if r['state']=='failed']
                targeted=s.cached('prepared-group:'+rg) or {}
                lossless_only=targeted.get('lossless_only',False)
                ceiling=max(0,min([r['quality_tier']-1 for r in failures],default=4))
                if lossless_only: ceiling=4
                if ceiling==0:
                    s.request_state(rg,'quality-options-exhausted',a['id']); continue
                last_search=budget.get('last-search:'+rg)
                new_failure=last_search and any(r['updated']>last_search for r in failures)
                if last_search and age(last_search)<1800 and not new_failure: continue
                if searches>=5:
                    decisions.append({'album':a['title'],'status':'waiting-for-next-search-batch'})
                    continue
                searches+=1
                releases=search_releases(l,budget,rg,a['id'])
                if releases is None:
                    decisions.append({'album':a['title'],'status':'search-deferred-after-error'})
                    continue
                if lossless_only: releases=[r for r in releases if tier(r)==4]
                seen={(r['guid'],r['indexer']) for r in s.db.execute('SELECT guid,indexer FROM transfers')}
                selected=choose_release(releases,ceiling,budget.remaining(),seen,reserve_factor=budget.factor)
                if not selected:
                    s.request_state(rg,'waiting-for-release',a['id'],f'No approved release within quality tier {ceiling} and budget')
                    decisions.append({'album':a['title'],'status':'waiting-for-approved-release'}); continue
                tid=budget.reserve(rg,selected['guid'],selected['indexerId'],selected['size'],tier(selected))
                if not tid: continue
                payload={k:selected[k] for k in ('guid','indexerId','artistId','albumId','downloadClientId') if k in selected}
                payload['albumId']=a['id']; payload['artistId']=a['artistId']
                try:
                    response=l.write('release','POST',payload) or {}
                    did=response.get('downloadId')
                    # The grab response may omit downloadId; refresh only our album's queue.
                    if not did:
                        found=next((x for x in l.get('queue',pageSize=1000).get('records',[]) if x.get('albumId')==a['id']),None)
                        if found: did=found.get('downloadId')
                    budget.state(tid,'queued',did)
                    s.request_state(rg,'requested',a['id'],f'Controlled grab: {selected["quality"]["quality"]["name"]}')
                    s.audit('budgeted-release-grabbed',{'album_id':a['id'],'title':selected['title'],'bytes':selected['size'],'tier':tier(selected)})
                    active+=1; decisions.append({'album':a['title'],'status':'requested','quality':selected['quality']['quality']['name'],'bytes':selected['size']})
                except Exception as e:
                    budget.state(tid,'held-for-review',detail=str(e)); s.request_state(rg,'held-for-review',a['id'],str(e))
                    decisions.append({'album':a['title'],'status':'ambiguous-grab-held'})
            output={'generated':now(),'budget':budget.report(),'decisions':decisions,
                    'mode':'bounded-expansion','max_concurrent_downloads':config.concurrent_downloads}
            path=config.reports/'expansion-status.json'; tmp=path.with_suffix('.tmp'); tmp.write_text(json.dumps(output,indent=2)); tmp.replace(path)
            log.info('Expansion check: %s decisions; %.2f GB charged of %.0f GB',len(decisions),budget.spent()/1e9,config.budget_bytes/1e9)
            return output
        finally: s.db.close()

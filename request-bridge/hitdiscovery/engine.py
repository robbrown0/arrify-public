import csv
import fcntl
import json
import logging
from datetime import date
from collections import Counter,defaultdict
from .api import HTTP,Lidarr
from .store import Store,now
from .inventory import scan
from .matching import Ownership,key,album_title,primary
from .sources import discover,score
from .resolver import Resolver
from .coverage import order_candidates,select_balanced,year_report
from .acquire import album_groups,diversify,Acquirer,available_groups,handled_resolution

log=logging.getLogger(__name__)

def inspect(l,store):
    caps=l.capabilities()
    settings={n:l.get(n) for n in ('qualityprofile','metadataprofile','rootfolder')}
    # Download-client responses contain credentials. Keep only this explicit safe subset.
    clients=l.get('downloadclient')
    settings['downloadclients']=[{k:c.get(k) for k in ('id','name','implementation','enable','protocol')} for c in clients]
    settings['health']=l.get('health')
    settings['capabilities']=caps
    store.cache('environment',settings)
    return settings

def run(config):
    config.validate()
    with open(config.data/'run.lock','w') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('Discovery already running')
        store=Store(config.data/'discovery.sqlite3'); http=HTTP(store); l=Lidarr(config,http)
        store.db.execute("UPDATE runs SET status='interrupted',finished=? WHERE status='running'",(now(),))
        cursor=store.db.execute('INSERT INTO runs(started,status) VALUES(?,?)',(now(),'running')); rid=cursor.lastrowid; store.db.commit()
        try:
            environment=inspect(l,store)
            log.info('Verified Lidarr %s; scanning inventory',environment['capabilities']['version'])
            if config.catalog_db:
                from .catalog import seed
                import os
                source_map=json.loads(os.getenv('EXISTING_CATALOG_SOURCES', '{}'))
                log.info('Reused %s unchanged embedded-tag entries',seed(store,config.catalog_db,source_map))
            artists,albums,tracks,summary=scan(l,store,config.music_paths)
            ownership=Ownership(tracks); candidates,health=discover(http,store,config.historical,config.contact,config.wikimedia)
            correction_file=config.data/'metadata-corrections.json'
            corrections=json.loads(correction_file.read_text()) if correction_file.exists() else {}
            resolver=Resolver(http,config.contact,corrections); resolved=[]; excluded=[]; unresolved=[]; owned=[]
            # Chart hints influence processing order only; eligibility requires first-release metadata.
            candidates.sort(key=lambda t:-score(t|{'release_date':t.get('release_date_hint') or '2015'}))
            remaining=order_candidates([t for t in candidates if not ownership.owns(t)],config.core_rank)
            remaining.sort(key=lambda t:0 if t.get('discovery_channel')=='imported-playlist' else 1 if t.get('discovery_channel')=='spectrum' or t.get('discovery_channel','').startswith('sirius-') else 2)
            budget_used=0
            for i,t in enumerate(remaining):
                known=store.cached('resolution:'+key(t['artist'],t['title']),ttl=86400*30)
                if known and known.get('resolution_status')=='unresolved':
                    known=store.cached('resolution:'+key(t['artist'],t['title']),ttl=86400)
                correction=corrections.get(key(t['artist'],t['title']))
                if known and correction and known.get('release_date')!=correction['release_date']: known=None
                if t.get('discovery_channel') in ('spectrum','imported-playlist') or t.get('discovery_channel','').startswith('sirius-'):
                    if known and known.get('resolution_status')=='out-of-window': known=None
                    if known: known=known|{'discovery_channel':t['discovery_channel']}
                if known: known=known|{'evidence':t['evidence']}
                if not known and budget_used>=config.resolve_limit:
                    unresolved.append(t|{'reason':'Resolution budget reached'}); continue
                if not known: budget_used+=1
                try: t=known or resolver.resolve(t)
                except Exception as e:
                    log.warning('Resolution failed for %s: %s',t['title'],e)
                    unresolved.append(t|{'reason':str(e)}); continue
                if not known: store.cache('resolution:'+key(t['artist'],t['title']),t)
                status=t.get('resolution_status')
                if status=='out-of-window': excluded.append(t); continue
                if status!='resolved': unresolved.append(t); continue
                match=ownership.owns(t)
                if match: owned.append(t|{'ownership_match':match}); continue
                t['score']=score(t); resolved.append(t)
                if (i+1)%10==0: log.info('Resolved %s/%s chart candidates; %s eligible missing',i+1,min(len(remaining),config.resolve_limit),len(resolved))
            for t in candidates:
                match=ownership.owns(t)
                if match: owned.append(t|{'ownership_match':match})
            # Deduplicate chart aliases that resolved to the same recording.
            unique={}
            for t in resolved:
                k=t['recording_id']
                if k not in unique: unique[k]=t
                else: unique[k]['evidence'].extend(t['evidence']); unique[k]['score']=score(unique[k])
            resolved=sorted(unique.values(),key=lambda t:-t['score'])
            groups=album_groups(resolved)
            local_albums={(primary(t.get('artist','')),album_title(t.get('album',''))) for t in tracks if t.get('owned') and t.get('album')}
            for g in groups:
                g['local_album_present']=(primary(g['artist']),album_title(g['album'])) in local_albums
                try: g.update(resolver.album_release(g))
                except Exception as e: g.update({'release_validation':'metadata-failure','release_error':str(e)})
            album_map={a['foreignAlbumId']:a for a in albums}
            acquirer=Acquirer(l,store,config); acquirer.reconcile()
            queue=l.get('queue',pageSize=1000,includeUnknownArtistItems=True)
            queued={x.get('albumId') or x.get('album',{}).get('id') for x in queue.get('records',[])}
            requested=0; addressed=0
            planned={r['rg'] for r in store.db.execute("SELECT rg FROM requests WHERE state IN ('prepared','requested','search-pending')")}
            year_counts=Counter(int(t['release_date'][:4]) for t in owned if t.get('release_date','')[:4].isdigit())
            for g in groups:
                if g['release_group_id'] in planned:
                    for song in g['songs']: year_counts[int(song['release_date'][:4])]+=1
            chosen={g['release_group_id'] for g in select_balanced(available_groups(groups,store,album_map,queued),config.batch_size if config.expansion else config.weekly_limit,year_counts,config.core_rank)}
            for g in groups:
                rg=g['release_group_id']; a=album_map.get(rg)
                req=store.db.execute('SELECT state FROM requests WHERE rg=?',(rg,)).fetchone()
                rejected=store.db.execute("SELECT 1 FROM decisions WHERE rg=? AND state='rejected'",(rg,)).fetchone()
                status='rejected' if rejected else req[0] if req else 'already-in-local-collection' if g['local_album_present'] else ('queued' if a and a['id'] in queued else 'existing-incomplete' if a and a.get('statistics',{}).get('trackFileCount',0) else 'monitored-missing' if a and a['monitored'] else 'in-lidarr-unmonitored' if a else 'not-in-lidarr')
                if rg in chosen and config.acquire and config.reviewed and not config.expansion:
                    status=acquirer.request(g)
                    if status=='requested': requested+=1; addressed+=g['missing_hits']
                g['status']=status; g['in_lidarr']=bool(a); g['selected_this_cycle']=rg in chosen
                store.decision(rg,g['artist'],g['album'],status,json.dumps([s['title'] for s in g['songs']]))
                for t in g['songs']:
                    t['album_status']=status; t['in_lidarr']=bool(a); t['other_missing_hits']=[s['title'] for s in g['songs'] if s is not t]
            by_year=defaultdict(list)
            for t in resolved: by_year[t['release_date'][:4]].append(t)
            eligible_owned=[t for t in owned if t.get('release_date','')[:4]>='2015']
            denominator=len(resolved)+len(eligible_owned)
            request_states={r['rg']:r['state'] for r in store.db.execute('SELECT rg,state FROM requests')}
            coverage_years=year_report(candidates,owned,resolved+excluded,request_states,2015,config.chart_year_target,config.core_rank)
            report={'generated':now(),'run_id':rid,'mode':'acquisition' if config.acquire and config.reviewed else 'dry-run',
                    'ownership_scope':config.ownership_scope,'environment':environment,'inventory':summary,'source_health':health,
                    'coverage':{'start':'2015-01-01','end':date.today().isoformat(),'historical_archive_configured':bool(config.historical),'wikimedia_historical_enabled':config.wikimedia,
                                'overall_major_hit_coverage':None,
                                'chart_candidate_ownership_match_percent':round(100*len(owned)/len(candidates),2) if candidates else None,
                                'chart_candidate_ownership_matches':len(owned),
                                'ownership_proxy_caveat':'Includes chart candidates whose original release year is unverified; not an estimate of all hits released since 2015.',
                                'note':'Sample of current Apple charts and available year-end transcriptions; genre charts and full weekly histories are incomplete.',
                                'verified_sample_owned':len(eligible_owned),'verified_sample_missing':len(resolved),
                                'owned_candidates_pending_first_release_verification':len(owned)-len(eligible_owned),
                                'sample_coverage_percent':round(100*len(eligible_owned)/denominator,2) if denominator and len(eligible_owned)==len(owned) else None},
                    'year_coverage':coverage_years,
                    'curation_policy':{'target_per_completed_chart_year':config.chart_year_target,'core_year_end_rank':config.core_rank,'selection':'Fill underrepresented release years with documented hits; one album per artist per batch; then deepen coverage.','count_note':'Chart-year placements are not unique songs or original-release-year quotas. Current-year charts remain rolling.'},
                    'candidate_songs':len(candidates),'missing_candidates_before_resolution':len(remaining),
                    'candidate_target_met':len(remaining)>=100,'unresolved_count':len(unresolved),'unresolved':unresolved,
                    'excluded_out_of_window':excluded,'top25':resolved[:25],'missing_by_year':dict(sorted(by_year.items())),
                    'missing_songs':resolved,'albums':groups,'newly_requested_albums':requested,'missing_hits_addressed':addressed,
                    'licenses':{'historical_transcription_exports':'CC BY-SA 4.0; Wikipedia contributors; revision attribution in evidence records'},
                    'limitations':['Unknown or unreadable tags require review before assuming ownership is complete.','Historical chart evidence is a secondary transcription; not a direct Billboard API verification.','Existing artists retain their artist-level quality profile; preferred fallback applies to new artists.']}
            export(config,report)
            store.db.execute('UPDATE runs SET finished=?,status=?,report=? WHERE id=?',(now(),'complete',json.dumps(report),rid)); store.db.commit()
            store.audit('run-complete',{'id':rid,'resolved_missing':len(resolved),'requested':requested})
            return report
        except Exception as e:
            store.db.execute('UPDATE runs SET finished=?,status=?,report=? WHERE id=?',(now(),'failed',json.dumps({'error':str(e)}),rid)); store.db.commit()
            store.audit('run-failed',{'id':rid,'error':str(e)}); raise
        finally: store.db.close()

def export(config,report):
    def atomic(path,text):
        tmp=path.with_suffix(path.suffix+'.tmp'); tmp.write_text(text); tmp.replace(path)
    atomic(config.reports/'latest.json',json.dumps(report,indent=2,ensure_ascii=False))
    atomic(config.reports/f"run-{report['run_id']}.json",json.dumps(report,indent=2,ensure_ascii=False))
    fields=['artist','title','release_date','score','album','release_group_id','in_lidarr','album_status','other_missing_hits','evidence','metadata_url']
    import io
    out=io.StringIO(); writer=csv.DictWriter(out,fieldnames=fields); writer.writeheader()
    for t in report['missing_songs']:
        writer.writerow({k:json.dumps(t.get(k,''),ensure_ascii=False) if isinstance(t.get(k),list) else t.get(k,'') for k in fields})
    atomic(config.reports/'missing-songs.csv',out.getvalue())
    year_rows=report.get('year_coverage',[])
    if year_rows:
        year_out=io.StringIO(); yw=csv.DictWriter(year_out,fieldnames=list(year_rows[0])); yw.writeheader(); yw.writerows(year_rows)
        atomic(config.reports/'year-coverage.csv',year_out.getvalue())
        atomic(config.reports/'year-coverage.json',json.dumps(year_rows,indent=2))
    lines=['# Music discovery report',f"Generated {report['generated']}",f"Mode: {report['mode']}",
           f"Ownership scope: {report['ownership_scope']}",
           f"Lidarr: {report['inventory']['lidarr_artists_with_files']} artists, {report['inventory']['lidarr_albums_with_files']} albums with files, {report['inventory']['lidarr_owned_tracks']} tracks.",
           f"Discovery sample: {len(report['missing_songs'])} verified missing songs from charts, radio, and requested playlists. Overall 2015-present hit coverage is unknown; current and year-end chart coverage is incomplete.",
           '\n| Artist | Song | First release | Album | Source evidence |', '|---|---|---|---|---|']
    for t in report['top25']:
        def evidence_label(e):
            if e.get('chart_scope')=='station':return e['source']+(f" ({e['plays']} observed plays)" if e.get('plays') else ' (observed airplay)')
            if e.get('unranked'):return e['source']+(f" (entry {e['playlist_position']})" if e.get('playlist_position') else '')
            return e['source']+f" #{e['rank']}"
        evidence='; '.join(f"[{evidence_label(e)}]({e['url']})" for e in t['evidence'][:3])
        lines.append('| '+' | '.join(str(v).replace('|','\\|') for v in [t['artist'],t['title'],t['release_date'],t['album'],evidence])+' |')
    lines+=['\n## Albums with multiple missing discovery songs']
    for g in report['albums']:
        if g['missing_hits']>1: lines.append(f"- {g['artist']} — {g['album']}: "+', '.join(t['title'] for t in g['songs']))
    lines += [f"\nRequested {report['newly_requested_albums']} albums; addressed {report['missing_hits_addressed']} missing hits."]
    atomic(config.reports/'latest.md','\n\n'.join(lines[:7])+'\n'+'\n'.join(lines[7:]))

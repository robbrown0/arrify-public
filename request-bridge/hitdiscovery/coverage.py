"""Breadth across chart years, with chart rank evidence kept separate from release dates."""
from collections import Counter,defaultdict
from datetime import date
from .matching import key
from .sources import score

def chart_rank(song):
    return min((int(e['rank']) for e in song['evidence'] if e.get('kind')=='year-end' and str(e.get('rank','')).isdigit() and not e.get('unranked')),default=101)

def core_hit(song,core_rank=50):
    return chart_rank(song)<=core_rank or any(e.get('kind')=='current' and e.get('chart_scope','national')=='national' and not e.get('unranked') and str(e.get('rank','')).isdigit() and 1<=int(e['rank'])<=20 for e in song['evidence'])

def candidate_year(song):
    value=song.get('release_date') or song.get('release_date_hint') or ''
    if value[:4].isdigit(): return int(value[:4])
    years=[int(e['chart_year']) for e in song['evidence'] if e.get('chart_year')]
    return min(years,default=date.today().year)

def order_candidates(songs,core_rank=50):
    buckets=defaultdict(list)
    deferred=[]
    for song in songs:
        year=candidate_year(song)
        if year<2015 or year>date.today().year: deferred.append(song)
        else: buckets[year].append(song)
    for bucket in buckets.values():
        bucket.sort(key=lambda s:(not core_hit(s,core_rank),chart_rank(s),-score(s|{'release_date':s.get('release_date_hint') or '2015'})))
    result=[]
    while buckets:
        for year in sorted(list(buckets)):
            result.append(buckets[year].pop(0))
            if not buckets[year]: del buckets[year]
    return result+sorted(deferred,key=chart_rank)

def group_years(group):
    return {int(s['release_date'][:4]) for s in group['songs'] if s.get('release_date','')[:4].isdigit()}

def select_balanced(groups,limit,counts=None,core_rank=50):
    covered=Counter(counts or {}); selected=[]; artists=set(); genres=Counter(); remaining=list(groups)
    while remaining and len(selected)<limit:
        eligible=[g for g in remaining if g['artist_mbid'] not in artists]
        if not eligible: break
        core=[g for g in eligible if any(core_hit(s,core_rank) for s in g['songs'])]
        pool=core or eligible
        def rank(g):
            years=group_years(g)
            pressure=min((covered[y] for y in years),default=10**6)
            repetition=min(12,3*sum(genres[x] for x in g.get('genres',[])[:1]))
            return (-pressure,g['score']-repetition,g.get('missing_hits',1))
        chosen=max(pool,key=rank); selected.append(chosen); remaining.remove(chosen); artists.add(chosen['artist_mbid'])
        for song in chosen['songs']:
            if song.get('release_date','')[:4].isdigit(): covered[int(song['release_date'][:4])]+=1
        genres.update(chosen.get('genres',[])[:1])
    return selected

def year_report(candidates,owned,known,states,start_year=2015,target=100,core_rank=50):
    by_chart=defaultdict(dict); owned_keys={key(t['artist'],t['title']) for t in owned}
    known_map={key(t['artist'],t['title']):t for t in known}
    for song in candidates:
        for e in song['evidence']:
            y=e.get('chart_year')
            if e.get('kind')!='year-end' or not y or int(e['rank'])>target: continue
            by_chart[int(y)][key(song['artist'],song['title'])]=(song,int(e['rank']))
    result=[]
    for year in range(start_year,date.today().year):
        row=Counter(); items=by_chart.get(year,{})
        for k,(song,rank) in items.items():
            resolved=known_map.get(k)
            status='owned' if k in owned_keys else 'pending_metadata'
            if resolved and resolved.get('resolution_status')=='out-of-window': status='outside_release_window'
            elif resolved and resolved.get('release_group_id') and k not in owned_keys:
                state=states.get(resolved['release_group_id'],'missing')
                status='requested_or_prepared' if state in ('prepared','requested','search-pending','adding-artist','adding-album') else 'owned' if state=='downloaded' and k in owned_keys else 'missing'
            row[status]+=1
            if rank<=core_rank: row['core_chart_rows']+=1
            if rank<=core_rank and status=='owned': row['core_owned']+=1
        result.append({'chart_year':year,'target_chart_rows':target,'available_chart_rows':len(items),
                       'core_rank_target':core_rank,**{k:row[k] for k in ('owned','missing','pending_metadata','outside_release_window','requested_or_prepared','core_chart_rows','core_owned')}})
    return result


def snapshot(config):
    """Refresh annual coverage from stored evidence and current imported Lidarr tracks."""
    import csv
    import io
    import json
    from .store import Store
    from .api import HTTP,Lidarr
    from .matching import Ownership
    s=Store(config.data/'discovery.sqlite3')
    try:
        merged={}
        for row in s.db.execute('SELECT payload FROM evidence'):
            t=json.loads(row[0]); k=key(t['artist'],t['title'])
            if k not in merged: merged[k]=t
            else: merged[k]['evidence'].extend(t['evidence'])
        tracks=[json.loads(r[0]) for r in s.db.execute('SELECT payload FROM files')]
        l=Lidarr(config,HTTP(s)); artists={a['id']:a['artistName'] for a in l.get('artist')}
        states={r['rg']:r['state'] for r in s.db.execute('SELECT rg,state FROM requests')}
        for album in l.get('album'):
            if album['foreignAlbumId'] not in states: continue
            for t in l.get('track',albumId=album['id']):
                tracks.append(t|{'artist':artists[album['artistId']], 'owned':bool(t.get('hasFile') or t.get('trackFileId')),
                                 'recording_ids':[t['foreignRecordingId']] if t.get('foreignRecordingId') else [],'isrcs':t.get('isrcs',[])})
        ownership=Ownership(tracks); candidates=list(merged.values())
        owned=[t for t in candidates if ownership.owns(t)]
        known=[json.loads(r[0]) for r in s.db.execute("SELECT payload FROM cache WHERE key LIKE 'resolution:%'")]
        rows=year_report(candidates,owned,known,states,2015,config.chart_year_target,config.core_rank)
        config.reports.mkdir(parents=True,exist_ok=True)
        for name,body in [('year-coverage.json',json.dumps(rows,indent=2))]:
            temp=config.reports/(name+'.tmp'); temp.write_text(body); temp.replace(config.reports/name)
        out=io.StringIO(); writer=csv.DictWriter(out,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
        temp=config.reports/'year-coverage.csv.tmp'; temp.write_text(out.getvalue()); temp.replace(config.reports/'year-coverage.csv')
        return rows
    finally: s.db.close()

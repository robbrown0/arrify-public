"""Operator-selected Arrify chart snapshot; no chart rankings are invented."""
import json
from pathlib import Path
from datetime import datetime,timezone
from .matching import key

def load(data):
    path=Path(data)/'arrify-chart-focus.json'
    if not path.exists():return None
    value=json.loads(path.read_text())
    return value if value.get('enabled') else None

def candidates(value):
    merged={}
    for chart in value.get('charts',[]):
        genre=chart['genre']
        for row in chart.get('items',[])[:40]:
            k=key(row['artist'],row['title'])
            item=merged.setdefault(k,{'artist':row['artist'],'title':row['title'],'release_date_hint':row.get('release_date','')[:10],
              'discovery_channel':'arrify-chart-focus','genres':[],'focus_memberships':[],'evidence':[]})
            item['genres'].append(chart['name'])
            item['focus_memberships'].append({'genre':genre,'rank':row['rank']})
            item['evidence'].append({'source':chart['source']+' / '+chart['name'],'rank':row['rank'],'kind':'current',
              'chart_scope':'national' if genre=='overall' else 'genre','date':datetime.fromtimestamp(chart['fetched'],timezone.utc).isoformat(),
              'url':chart['source_url'],'independent_family':'Apple','weight':1 if genre=='overall' else .65})
    return merged

def for_store(store):
    path=store.db.execute('PRAGMA database_list').fetchone()[2]
    value=load(Path(path).parent)
    if not value:return None
    result=candidates(value)
    for filename in ('jazz-targets.json','recording-correction-targets.json'):
        supplemental=Path(path).parent/filename
        if supplemental.exists():
            for song in json.loads(supplemental.read_text()):
                k=key(song['artist'],song['title'])
                if k in result:result[k]['evidence'].extend(song['evidence'])
                else:result[k]=song
    # Explicitly selected stations supplement the chart focus without reopening
    # every deferred discovery category.
    from .station_subscriptions import acquisition_channels
    selected=set(value.get('acquisition_stations',[]))|acquisition_channels(Path(path).parent)
    if selected:
        from .stations import stored_candidates
        for song in stored_candidates(Path(path).parent):
            if song['discovery_channel'] not in selected:continue
            k=key(song['artist'],song['title'])
            if k in result:
                result[k]['evidence'].extend(song['evidence'])
            else:
                song['focus_memberships']=[{'genre':song['discovery_channel'],'rank':min(100,int(song['evidence'][0]['rank']))}]
                result[k]=song
    return result

def release_groups(store,targets):
    groups=set()
    for k in targets:
        song=store.cached('resolution:'+k,ttl=86400*30)
        if song and song.get('resolution_status')=='resolved' and song.get('release_group_id'):groups.add(song['release_group_id'])
    return groups

def select_groups(groups,targets,limit):
    """Favor high ranks/multiple hits while spreading artists and chart genres."""
    chosen=[];artists=set();genres={};remaining=list(groups)
    def priority(group):
        memberships=[m for song in group['songs'] for m in targets.get(key(song['artist'],song['title']),{}).get('focus_memberships',[])]
        best=max((101-m['rank'] for m in memberships),default=0)
        diversity=min((genres.get(m['genre'],0) for m in memberships),default=0)
        return best+min(30,10*(len(group['songs'])-1))-45*diversity
    while remaining and len(chosen)<limit:
        eligible=[g for g in remaining if g['artist_mbid'] not in artists]
        if not eligible:break
        group=max(eligible,key=priority);chosen.append(group);remaining.remove(group);artists.add(group['artist_mbid'])
        memberships=[m for song in group['songs'] for m in targets.get(key(song['artist'],song['title']),{}).get('focus_memberships',[])]
        if memberships:
            genre=min(memberships,key=lambda m:(genres.get(m['genre'],0),m['rank']))['genre'];genres[genre]=genres.get(genre,0)+1
    return chosen

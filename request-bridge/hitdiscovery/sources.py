import json
from datetime import date
from .matching import key
from .store import now

APPLE='https://rss.marketingtools.apple.com/api/v2/us/music/most-played/100/songs.json'
SALES='https://itunes.apple.com/us/rss/topsongs/limit=100/json'

def apple(http):
    feed=http.json(APPLE,cache_ttl=3600)['feed']
    result=[]
    for rank,t in enumerate(feed['results'],1):
        result.append({'artist':t['artistName'],'title':t['name'],'release_date_hint':t.get('releaseDate',''),
                       'genres':[g['name'] for g in t.get('genres',[]) if g['name']!='Music'],
                       'apple_id':t['id'],'evidence':[{'source':'Apple Music US most played','rank':rank,'kind':'current',
                       'date':feed['updated'],'url':APPLE,'song_url':t['url'],'independent_family':'Apple'}]})
    return result

def sales(http,url=SALES,source='iTunes US song sales',genre=None):
    feed=http.json(url,cache_ttl=3600)['feed']; result=[]
    for rank,t in enumerate(feed.get('entry',[]),1):
        result.append({'artist':t['im:artist']['label'],'title':t['im:name']['label'],
                       'release_date_hint':t.get('im:releaseDate',{}).get('label','')[:10], 'genres':[t.get('category',{}).get('attributes',{}).get('label','Unknown')],
                       'apple_id':t['id']['attributes']['im:id'],
                       'album_hint':t.get('im:collection',{}).get('im:name',{}).get('label',''),
                       'evidence':[{'source':source,'rank':rank,'kind':'current','date':feed['updated']['label'],
                                    'url':url,'weight':.65 if genre else 1,'chart_scope':'genre' if genre else 'national','song_url':t['id']['label'],'independent_family':'Apple'}]})
    return result

def historical(path):
    if not path: return []
    with open(path) as stream: d=json.load(stream)
    if not d.get('authorized_use') or not d.get('license') or not d.get('provenance_url'):
        raise ValueError('Historical import requires authorized_use, license and provenance_url')
    result=[]
    for t in d['songs']:
        for e in t['evidence']:
            if not all(e.get(k) for k in ('source','url','date','kind','rank')): raise ValueError('Incomplete historical evidence')
            if not 1<=int(e['rank'])<=10000: raise ValueError('Invalid chart rank')
            if e['kind'] not in ('weekly','year-end','current'): raise ValueError('Invalid chart kind')
        result.append(t)
    return result

def discover(http,store,historical_path,contact="",wikimedia=False):
    rows=[]; health=[]
    import os
    if os.getenv('SPECTRUM_ENABLED','false').lower()=='true':
        from .spectrum import collect,candidates
        try:
            found=candidates(collect(http, __import__('pathlib').Path(os.getenv('DATA_DIR','/data'))))
            from .stations import stored_candidates,imported_candidates
            data_dir=__import__('pathlib').Path(os.getenv('DATA_DIR','/data'))
            found+=stored_candidates(data_dir)+imported_candidates(data_dir)
            rows.extend(found); health.append({'source':'The Spectrum observed airplay','ok':True,'rows':len(found)})
        except Exception as e:
            health.append({'source':'The Spectrum observed airplay','ok':False,'error':str(e)})
    feeds=[(f'iTunes US {genre}',lambda genre=genre,gid=gid:sales(http,f'https://itunes.apple.com/us/rss/topsongs/limit=100/genre={gid}/json',f'iTunes US {genre} sales',genre)) for genre,gid in [('Country',6),('Rock',21),('Alternative',20)]]
    for name,fn in [('Apple Music',lambda:apple(http)),('iTunes sales',lambda:sales(http)),('licensed historical',lambda:historical(historical_path))]+feeds:
        try:
            found=fn(); rows.extend(found); health.append({'source':name,'ok':True,'rows':len(found)})
        except Exception as e:
            health.append({'source':name,'ok':False,'error':str(e)})
    if wikimedia:
        from .wikimedia import fetch
        wiki_rows,wiki_health=fetch(http,contact)
        rows.extend(wiki_rows)
        health.append({'source':'Wikipedia CC BY-SA year-end transcriptions','ok':bool(wiki_rows),'rows':len(wiki_rows),'years':wiki_health})
    if not any(h['ok'] and h.get('rows') for h in health): raise RuntimeError('All chart sources failed or empty')
    # Preserve chart observations so weekly snapshots accumulate evidence.
    for t in rows:
        for e in t['evidence']:
            ek=key(t['artist'],t['title'])+'|'+e['source']+'|'+e['date']
            store.db.execute('INSERT OR REPLACE INTO evidence VALUES(?,?,?)',(ek,json.dumps(t|{'evidence':[e]}),now()))
    store.db.commit()
    merged={}
    for row in store.db.execute('SELECT payload FROM evidence'):
        t=json.loads(row[0]); k=key(t['artist'],t['title'])
        if k not in merged: merged[k]=t
        else:
            if t.get('discovery_channel'): merged[k]['discovery_channel']=t['discovery_channel']
            seen={(e['source'],e['date']) for e in merged[k]['evidence']}
            merged[k]['evidence'].extend(e for e in t['evidence'] if (e['source'],e['date']) not in seen)
    return list(merged.values()),health

def score(t):
    evidence=t['evidence']; today=date.today()
    best=max((100*(1-(min(int(e['rank']),100)-1)/100) * (1 if e['kind']=='year-end' else .9 if e['kind']=='weekly' else .75)*e.get('weight',1) for e in evidence if not e.get('unranked') and e.get('rank') is not None), default=0)
    independent=len({e.get('independent_family',e['source']) for e in evidence})
    weeks=len({e['date'][:10] for e in evidence})
    year=int(t.get('release_date','2015')[:4])
    # Recency boosts, never gates out older eligible hits. Sales and streaming from Apple are one family.
    recency=max(0,10-2*(today.year-year))
    return round(best+min(15,5*(independent-1))+min(10,2*(weeks-1))+recency,2)

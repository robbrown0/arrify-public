"""Cached library browsing and daily US charts; no provider I/O on request paths."""
import collections
import contextlib
import datetime as dt
import json
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import urllib.request
from tinytag import TinyTag
from .radio import recording_key, artist_key
from .search import normalize

# Apple taxonomy IDs verified against its public genre taxonomy endpoint.
GENRES=[('pop','Pop','14'),('rock','Rock','21'),('country','Country','6'),('bluegrass','Bluegrass','1035'),('hip-hop','Hip-Hop','18'),('soul','R&B / Soul','15'),('jazz','Jazz','11'),('blues','Blues','2'),('folk','Folk / Americana','1034'),('dance','Dance / Electronic','17'),('classical','Classical','5'),('reggae','Reggae','24'),('latin','Latin','12'),('metal','Metal','1153'),('alternative','Alternative / Indie','20'),('christian','Christian / Gospel','22'),('spoken-word', 'Spoken Word', '50000061'),('soundtrack', 'Soundtrack', '16'),('new-age', 'New Age', '13'),('holiday', 'Holiday', '8'),('world', 'World', '19'),('comedy', 'Comedy', '3'),('instrumental', 'Instrumental', '53'),('easy-listening', 'Easy Listening', '25'),('vocal', 'Vocal', '23'),('children', 'Children’s Music', '4'),('other','Other / unclassified',None)]
GENRE_NAMES={key:name for key,name,_ in GENRES}
CHART_REFRESH_SECONDS=86400
CACHE_ENTRIES=24
CACHE_ITEMS=150000

def chart_recording_key(track):
    # Providers and file tags use different separators for the same credits.
    # Keep every credited artist and the exact normalized recording title.
    feature_pattern=r'\s*(?:[\[(]|\b)(?:feat\.?|ft\.?|featuring)\s+([^\])]+)(?:[\])]|$)'
    featured=re.findall(feature_pattern,str(track.get('title','')),flags=re.I)
    credits=[]
    for value in [str(track.get('artist','')),*featured]:
        credits.extend(re.split(r'\s*(?:[;,&]|\band\b)\s*',value,flags=re.I))
    title=re.sub(feature_pattern,'',str(track.get('title','')),flags=re.I)
    return tuple(sorted({key for credit in credits if (key:=artist_key(credit))})),recording_key({**track,'title':title})[1]

def genres(value):
    text=' '+normalize(value)+' ';found=set()
    rules={'pop':('pop','britpop','synthpop','jpop','powerpop'),'rock':('rock','punk','grunge','new wave'),'country':('country','bluegrass','honky tonk','bakersfield'),'bluegrass':('bluegrass',),'hip-hop':('hip hop','rap','trap'),'soul':('r b','rnb','randb','soul','funk','motown'),'jazz':('jazz','bebop','swing'),'blues':('blues',),'folk':('folk','americana','singer songwriter','acoustic'),'dance':('dance','electronic','electronica','house','techno','trance','edm','disco','ambient'),'classical':('classical','baroque','opera','orchestral','chamber','symphony'),'reggae':('reggae','ska','dub'),'latin':('latin','salsa','bachata','reggaeton','bossa nova','mariachi'),'metal':('metal','thrash','deathcore'),'alternative':('alternative','indie','new wave','grunge'),'christian':('christian','gospel','worship')}
    rules.update({'spoken-word': ('spoken word', 'spoken audio', 'spoken'), 'soundtrack': ('soundtrack', 'soundtracks', 'film'), 'new-age': ('new age',), 'holiday': ('holiday', 'christmas'), 'world': ('world', 'worldwide', 'international', 'musiques du monde'), 'comedy': ('comedy', 'humour'), 'instrumental': ('instrumental',), 'easy-listening': ('easy listening',), 'vocal': ('vocal',), 'children': ('childrens music', 'children music')})
    for key,terms in rules.items():
        if any(' '+term+' ' in text for term in terms):found.add(key)
    return sorted(found) or ['other']

def year(value):
    match=re.search(r'(?<!\d)(19\d{2}|20\d{2})(?!\d)',str(value))
    result=int(match[1]) if match else 0
    return result if 1900<=result<=dt.date.today().year else 0

def tag_info(tag):
    other=getattr(tag,'other',{}) or {};original=0
    for key in ('originaldate','originalyear','original_date','original_year','original release date','original release year','original_release_date','original_release_year','tdor','tory'):
        values=other.get(key,[])
        if isinstance(values,str):values=[values]
        for v in values:
            original=year(v)
            if original:break
        if original:break
    return {'raw_genre':tag.genre or '', 'genres':genres(tag.genre or ''),'year':original or year(tag.year),'date_source':'original' if original else 'release-tag' if year(tag.year) else 'unknown'}

def fetch_json(url):
    req=urllib.request.Request(url,headers={'User-Agent':'ArrifyBrowse/1.0 (https://arrify.robnets.com)','Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=12) as response:
        data=response.read(4*1024*1024+1)
    if len(data)>4*1024*1024:raise ValueError('Chart too large.')
    return json.loads(data)

def chart_items(data,legacy=False):
    feed=data.get('feed',{});raw=feed.get('entry',[]) if legacy else feed.get('results',[])
    if isinstance(raw,dict):raw=[raw]
    items=[];seen=set()
    for row in raw:
        if not isinstance(row,dict):continue
        artist=row.get('im:artist',{}).get('label') if legacy else row.get('artistName')
        title=row.get('im:name',{}).get('label') if legacy else row.get('name')
        if not isinstance(artist,str) or not isinstance(title,str) or not artist.strip() or not title.strip() or len(artist)>500 or len(title)>500:continue
        item={'artist':artist,'title':title,'release_date':row.get('im:releaseDate',{}).get('label','') if legacy else row.get('releaseDate','')}
        key=recording_key(item)
        if key in seen:continue
        seen.add(key);item['rank']=len(items)+1;items.append(item)
        if len(items)==100:break
    if not items:raise ValueError('Chart contained no songs.')
    updated=feed.get('updated',{}).get('label','') if legacy else feed.get('updated','')
    return {'items':items,'source':'iTunes Store · US' if legacy else 'Apple Music · US','source_url':'https://music.apple.com/us/charts','provider_updated':str(updated)[:100]}

class Browse:
    def __init__(self,directory,library,start=True,fetch=fetch_json):
        self.lib=library;self.path=Path(directory)/'browse.sqlite3';self.lock=threading.RLock();self.fetch=fetch;self.tags={};self.charts={};self.index=None;self.tag_version=0;self.index_key=None;self.scanning=start;self.scanned=0;self.total=0;self.last_error=False
        self.build_lock=threading.Lock()
        with contextlib.closing(self.connect()) as db:
            self.tags={i:(modified,json.loads(payload)) for i,modified,payload in db.execute('SELECT * FROM browse_tags')}
            self.charts={key:json.loads(payload) for key,payload in db.execute('SELECT key,payload FROM browse_charts')}
        if start:
            threading.Thread(target=self.background,daemon=True,name='arrify-browse-tags').start()
            threading.Thread(target=self.chart_background,daemon=True,name='arrify-browse-charts').start()

    def connect(self):
        db=sqlite3.connect(self.path);os.chmod(self.path,0o600)
        db.execute('CREATE TABLE IF NOT EXISTS browse_tags(id INTEGER PRIMARY KEY,modified INTEGER,payload TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS browse_charts(key TEXT PRIMARY KEY,payload TEXT)');db.commit();return db

    def update_charts(self):
        for key,_,genre_id in [('overall','Overall',None)]+[g for g in GENRES if g[2]]:
            with self.lock:saved=self.charts.get(key,{})
            if time.time()-saved.get('fetched',0)<CHART_REFRESH_SECONDS:continue
            if time.time()-saved.get('attempted',0)<3600:continue
            try:
                url=('https://rss.marketingtools.apple.com/api/v2/us/music/most-played/100/songs.json' if key=='overall' else 'https://itunes.apple.com/us/rss/topsongs/limit=100/genre='+genre_id+'/json')
                value=chart_items(self.fetch(url),key!='overall');value.update(fetched=int(time.time()),attempted=int(time.time()),stale=False)
            except Exception:value={**saved,'attempted':int(time.time()),'stale':True}
            with contextlib.closing(self.connect()) as db:
                db.execute('INSERT OR REPLACE INTO browse_charts VALUES(?,?)',(key,json.dumps(value)));db.commit()
            with self.lock:self.charts[key]=value
            time.sleep(.2)

    def scan_tags(self):
        with self.lib.music.lock:rows=self.lib.music.db.execute('SELECT id,modified_ns FROM tracks WHERE available=1').fetchall()
        self.scanning=True;self.scanned=0;self.total=len(rows);pending=[]
        with contextlib.closing(self.connect()) as db:
            for tid,modified in rows:
                with self.lock:saved=self.tags.get(tid)
                if not saved or saved[0]!=modified:
                    try:
                        _,filename=self.lib.music._track_file(tid);info=tag_info(TinyTag.get(filename,duration=False,image=False))
                    except Exception:
                        # A temporarily unavailable file must be retried, not
                        # permanently remembered as an unclassified recording.
                        self.scanned+=1;continue
                    pending.append((tid,modified,json.dumps(info)))
                    with self.lock:self.tags[tid]=(modified,info);self.tag_version+=1
                self.scanned+=1
                if len(pending)>=128:
                    db.executemany('INSERT OR REPLACE INTO browse_tags VALUES(?,?,?)',pending);db.commit();pending=[];time.sleep(.03)
            if pending:db.executemany('INSERT OR REPLACE INTO browse_tags VALUES(?,?,?)',pending);db.commit()
            available={r[0] for r in rows}
            with self.lock:
                for tid in list(self.tags):
                    if tid not in available:self.tags.pop(tid,None);self.tag_version+=1
            obsolete=[(r[0],) for r in db.execute('SELECT id FROM browse_tags') if r[0] not in available]
            db.executemany('DELETE FROM browse_tags WHERE id=?',obsolete);db.commit()
        self.scanning=False

    def background(self):
        while True:
            try:self.scan_tags();self.last_error=False
            except Exception:self.last_error=True;self.scanning=False
            time.sleep(300)

    def chart_background(self):
        while True:
            try:self.update_charts()
            except Exception:pass
            time.sleep(300)

    def view(self):
        # Only one request builds a snapshot; playback/catalog locks are held
        # briefly to capture inputs, never during the expensive derived work.
        with self.build_lock:
            return self._view()

    def _view(self):
        with self.lib.index_lock:catalog=self.lib.radio_index
        with self.lock:
            key=(id(catalog),self.tag_version)
            if self.index_key==key:return self.index
            tags=dict(self.tags)
        tracks=catalog.tracks;groups=collections.defaultdict(list);decades=collections.defaultdict(list);metadata={}
        for tid,t in tracks.items():
            info=tags.get(tid,(0,{}))[1];local=genres(info['raw_genre']) if 'raw_genre' in info else info.get('genres',['other']);gs=t.get('catalog_genres',local);y=t.get('catalog_year',info.get('year',0))
            # Providers return display labels; Browse stores category IDs.
            # Normalize at this boundary so an unfamiliar label cannot break
            # the entire catalog snapshot.
            if isinstance(gs,str): gs=[gs]
            if not isinstance(gs,(list,tuple,set)): gs=[]
            gs=sorted({category for value in gs for category in ([value] if isinstance(value,str) and value in GENRE_NAMES else genres(str(value)))}) or ['other']

            source=t.get('catalog_date_source',info.get('date_source','unknown'))
            # Compilation/reissue dates are not evidence of a recording's era.
            # Keep the song available, but withhold a dubious decade assignment
            # until recording-level research verifies it.
            suspect=bool(re.search(r'\b(complete|compilation|greatest|best of|anthology|collection|anniversary|remaster|deluxe|live at|live in|live from)\b',normalize(t.get('album',''))))
            if suspect and source not in ('researched','recording-original'):
                y=0;source='pending-recording-date'
            if len(gs)>1 and 'other' in gs:gs.remove('other')
            metadata[tid]={'genres':gs,'year':y,'date_source':source}
            for g in gs:groups[g].append(tid)
            if y>=1960:decades[str(y//10*10)].append(tid)
        counts={}
        for decade,scope in [('',tracks),*decades.items()]:
            totals=collections.Counter(g for i in scope for g in metadata[i]['genres'])
            counts[decade]=tuple({'id':g,'name':GENRE_NAMES[g],'count':n} for g,n in sorted(totals.items(),key=lambda item:(-item[1],GENRE_NAMES[item[0]])))
        result={'catalog':catalog,'tracks':tracks,'groups':groups,'decades':decades,'metadata':metadata,'genre_counts':counts,
                'derived_lock':threading.RLock(),'cache':collections.OrderedDict(),'cache_items':0}
        with self.lock:self.index_key=key;self.index=result
        return result

    @staticmethod
    def _cached(v,key,build):
        # Cache only IDs or compact group summaries, with both count and size
        # bounds. Each generation owns its cache so invalidation drops it all.
        with v['derived_lock']:
            cache=v['cache']
            if key in cache:
                cache.move_to_end(key);return cache[key]
            value=tuple(build())
            if len(value)<=CACHE_ITEMS:
                while cache and (len(cache)>=CACHE_ENTRIES or v['cache_items']+len(value)>CACHE_ITEMS):
                    _,old=cache.popitem(last=False);v['cache_items']-=len(old)
                cache[key]=value;v['cache_items']+=len(value)
            return value

    def _ordered_ids(self,v,genre,decade):
        with v['derived_lock']:
            if 'ordered_ids' not in v:
                tracks=v['tracks']
                v['ordered_ids']=tuple(sorted(tracks,key=lambda i:(normalize(tracks[i]['artist']),normalize(tracks[i]['album']),normalize(tracks[i]['title']),i)))
            if not genre and not decade:return v['ordered_ids']
            def build():
                allowed=set(v['groups'][genre]) if genre else set(v['tracks'])
                if decade:allowed.intersection_update(v['decades'][decade])
                return (i for i in v['ordered_ids'] if i in allowed)
            return self._cached(v,('ids',genre,decade),build)

    @staticmethod
    def _chart_recordings(v):
        with v['derived_lock']:
            if 'chart_recordings' not in v:
                recordings=collections.defaultdict(set)
                for tid,track in v['tracks'].items():recordings[chart_recording_key(track)].add(tid)
                v['chart_recordings']=recordings
            return v['chart_recordings']

    def overview(self):
        v=self.view()
        return {'genres':[{'id':g,'name':name,'count':len(v['groups'][g])} for g,name,_ in GENRES], 'decades':[{'id':str(y),'name':str(y)+'s','count':len(v['decades'][str(y)])} for y in range(1960,dt.date.today().year//10*10+1,10)],'indexing':self.scanning,'scanned':self.scanned,'total':self.total,'error':self.last_error,'date_pending':sum(x['date_source']=='pending-recording-date' for x in v['metadata'].values())}

    def select(self,filters):
        # Callers may shuffle/mutate their selection; never expose cached IDs.
        return list(self._select(self.view(),filters))

    def _select(self,v,filters):
        genre=filters.get('genre','');decade=str(filters.get('decade',''));kind=filters.get('kind','songs')
        if genre and genre not in GENRE_NAMES:raise ValueError('Unknown genre.')
        if decade and decade not in [str(y) for y in range(1960,dt.date.today().year//10*10+1,10)]:raise ValueError('Unknown decade.')
        if kind not in ('songs','hits','albums','artists'):raise ValueError('Unknown browse view.')
        if kind!='hits':return self._ordered_ids(v,genre,decade)
        ids=v['groups'][genre] if genre else list(v['tracks'])
        if decade:
            allowed=set(v['decades'][decade]);ids=[i for i in ids if i in allowed]
        if kind=='hits':
            with self.lib.radio.similar.lock:popular=dict(self.lib.radio.similar.popular)
            recordings=v['catalog'].recordings
            ids=sorted(ids,key=lambda i:(-v['catalog'].hits[i],-popular.get(recordings[i],0),normalize(v['tracks'][i]['title'])))
            # Historical essentials require a popularity signal, not arbitrary
            # alphabetical deep cuts falsely presented as established hits.
            ids=[i for i in ids if v['catalog'].hits[i] or popular.get(recordings[i],0)][:200]
            seen=set();dedup=[]
            for i in ids:
                k=recordings[i]
                if k not in seen:seen.add(k);dedup.append(i)
            return dedup

    def page(self,filters,offset=0,limit=100):
        v=self.view();ids=self._select(v,filters);kind=filters.get('kind','songs');genre=filters.get('genre','');decade=str(filters.get('decade',''))
        base={'name':(''+decade+'s · ' if decade else '')+GENRE_NAMES.get(genre,'All music'),'total':len(ids),'offset':offset,'indexing':self.scanning,'genre':genre,'decade':decade,'kind':kind}
        base['genre_counts']=[dict(row) for row in v['genre_counts'].get(decade,())]
        if kind in ('albums','artists'):
            def build():
                field='album_id' if kind=='albums' else 'artist_id';buckets={}
                for tid in ids:
                    t=v['tracks'][tid]
                    refs=([{'id':t['album_id'],'name':t['album']}] if kind=='albums' else t.get('artist_refs',[{'id':t['artist_id'],'name':t['artist']}]))
                    for ref in refs:
                        b=buckets.setdefault(ref['id'],{'id':ref['id'],'name':ref['name'],'artist':t['artist'] if kind=='albums' else '', 'artwork':t['artwork'],'tracks':0});b['tracks']+=1
                return sorted(buckets.values(),key=lambda x:normalize(x['name']))
            items=self._cached(v,('groups',genre,decade,kind),build)
            return base|{'items':[dict(item) for item in items[offset:offset+limit]],'total':len(items),'next_offset':min(len(items),offset+limit)}
        return base|{'tracks':[v['tracks'][i] | {'browse_year':v['metadata'][i]['year'],'date_source':v['metadata'][i]['date_source']} for i in ids[offset:offset+limit]],'next_offset':min(len(ids),offset+limit)}

    def chart(self,genre='overall',new=False):
        if genre!='overall' and genre not in {g for g,_,i in GENRES if i}:raise ValueError('Chart unavailable for this genre.')
        with self.lock:saved=dict(self.charts.get(genre,{}))
        v=self.view();owned=v['catalog'].recording_ids;items=[];cutoff=dt.date.today()-dt.timedelta(days=90)
        for item in saved.get('items',[]):
            if new:
                try:
                    if not cutoff<=dt.date.fromisoformat(str(item.get('release_date',''))[:10])<=dt.date.today():continue
                except ValueError:continue
            ids=owned.get(recording_key(item),set()) or self._chart_recordings(v).get(chart_recording_key(item),set());tid=min(ids) if ids else None
            items.append({**item,'track':v['tracks'].get(tid),'request_key':self.request_key(item)})
            if len(items)>=40:break
        return {**{k:v for k,v in saved.items() if k!='items'},'items':items,'genre':genre,'new':new,'pending':not saved.get('items'),'stale':bool(saved.get('stale')) or time.time()-saved.get('fetched',0)>=CHART_REFRESH_SECONDS,'next_update':saved.get('fetched',0)+CHART_REFRESH_SECONDS if saved.get('fetched') else None}

    @staticmethod
    def request_key(item):return hashlib.sha256(json.dumps([recording_key(item)[0],normalize(item['title'])],ensure_ascii=False).encode()).hexdigest()[:24]

    def request_entry(self,genre,key):
        self.chart(genre)  # Validate the genre without fetching provider data.
        with self.lock:items=list(self.charts.get(genre,{}).get('items',[]))
        item=next((item for item in items if self.request_key(item)==key),None)
        if not item:raise ValueError('This chart changed. Refresh it and try Request again.')
        return dict(item)

"""Seed radio: local selection on the playback path, bounded public enrichment off it."""
import collections
import contextlib
import hashlib
import json
import math
import os
import queue
import random
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from .search import normalize


def artist_key(value):
    value=normalize(str(value).replace('&',' and '))
    parts=value.split()
    if len(parts)>1 and parts[-1]=='the':parts=parts[:-1]
    if len(parts)>1 and parts[0]=='the':parts=parts[1:]
    return ' '.join(parts)


def recording_key(t):
    title=re.sub(r'\s*[\[(][^\])]*(?:remaster|stereo|mono|live version|radio edit)[^\])]*[\])]','',t.get('title',''),flags=re.I)
    title=re.sub(r'\s*[-–]\s*(?:\d{4}\s*)?remaster.*$','',title,flags=re.I)
    return artist_key(t.get('artist','')),normalize(title)


def genre_profile(metadata):
    result={}
    for item in metadata.get('tag',{}).get('artist',[]):
        if not isinstance(item,dict):continue
        tag=normalize(item.get('tag',''));count=item.get('count',0)
        if not isinstance(count,(int,float)) or isinstance(count,bool) or not math.isfinite(count) or count<=0:continue
        family=None
        for key,terms in [('country',('country','americana','bluegrass','bakersfield','honky tonk')),('hip hop',('hip hop','rap','trap')),('metal',('metal','thrash','deathcore')),('jazz',('jazz','bebop','swing')),('blues',('blues',)),('soul',('soul','funk','r b','motown')),('classical',('classical','baroque','opera','romanticism')),('electronic',('electronic','techno','house','trance','edm','ambient')),('rock',('rock','punk','grunge','new wave','gothic','indie')),('folk',('folk','singer songwriter')),('reggae',('reggae','ska','dub')),('pop',('pop',))]:
            if any(term in tag for term in terms):family=key;break
        if family:result[family]=max(result.get(family,0),float(count))
    return result


def genre_compatible(seed,candidate):
    if not seed:return True
    if not candidate:return False
    dominant=max(seed,key=seed.get)
    if candidate.get(dominant,0)/sum(candidate.values())<.2:return False
    dot=sum(value*candidate.get(key,0) for key,value in seed.items())
    return dot/(math.sqrt(sum(v*v for v in seed.values()))*math.sqrt(sum(v*v for v in candidate.values())))>=.3


class RadioIndex:
    """Immutable derived catalog; rebuilt only when the library is reindexed."""
    def __init__(self,tracks,memberships):
        self.hits=collections.Counter(tid for _,tid in memberships);self.tracks=tracks;self.recordings={i:recording_key(t) for i,t in tracks.items()};self.recording_ids=collections.defaultdict(set)
        for i,key in self.recordings.items():self.recording_ids[key].add(i)
        self.known_recordings=set(self.recordings.values());self.owned_albums={(artist_key(t['artist']),normalize(t.get('album',''))) for t in tracks.values() if t.get('album')}
        self.artists=collections.defaultdict(list);self.ids={};self.names={}
        for t in tracks.values():
            key=artist_key(t['artist']);self.artists[key].append(t['id']);self.names.setdefault(key,t['artist'])
            self.ids[t.get('artist_credit_id',t['artist_id'])]=key
            for ref in t.get('artist_refs', [{'id':t['artist_id'],'name':t['artist']}]):
                credited=artist_key(ref['name'])
                self.ids[ref['id']]=credited;self.names.setdefault(credited,ref['name'])
                if credited!=key:self.artists[credited].append(t['id'])
        self.groups=collections.defaultdict(set);self.track_groups=collections.defaultdict(set);self.artist_groups=collections.defaultdict(set)
        for slug,tid in memberships:
            if tid in tracks:self.groups[slug].add(artist_key(tracks[tid]['artist']));self.track_groups[tid].add(slug)
        self.groups={slug:artists for slug,artists in self.groups.items() if 1<len(artists)<=250}
        for slug,artists in self.groups.items():
            for artist in artists:self.artist_groups[artist].add(slug)

    def seed(self,kind,seed):
        if kind=='track':
            try:t=self.tracks[int(seed)]
            except (KeyError,ValueError,TypeError):raise ValueError('Song unavailable.') from None
            return {'kind':kind,'seed':str(t['id']),'name':t['title']+' Radio','artist':t['artist'],'artwork':t['artwork'],'track_id':t['id']}
        if kind=='artist' and str(seed) in self.ids:
            key=self.ids[str(seed)];t=self.tracks[self.artists[key][0]]
            return {'kind':kind,'seed':str(seed),'name':self.names[key]+' Radio','artist':self.names[key],'artwork':t['artwork']}
        raise ValueError('Artist unavailable.')

    def weights(self,seed,related):
        artist=artist_key(seed['artist']);weights={artist:1.0};groups=self.artist_groups.get(artist,set());exact=self.track_groups.get(seed.get('track_id'),set())
        local=collections.defaultdict(float)
        for group in groups:
            members=self.groups.get(group,())
            for other in members:
                if other!=artist:local[other]+=(2.5 if group in exact else 1)/math.sqrt(len(members))
        peak=max(local.values(),default=1)
        for name,score in local.items():weights[name]=.7*score/peak
        for name in related:
            key=artist_key(name)
            if key in self.artists and key!=artist:weights[key]=2+min(.5,local.get(key,0)/peak*.5)
        external={artist_key(name) for name in related if artist_key(name) in self.artists and artist_key(name)!=artist}
        if related:weights={key:weight for key,weight in weights.items() if key==artist or key in external}
        return {key:weight for key,weight in weights.items() if key in self.artists}


class SimilarArtists:
    """No accounts/listens sent upstream. A single worker, memory reads, private cache."""
    def __init__(self,directory,fetch=None,enabled=True,now=time.time):
        self.lock=threading.RLock();self.now=now;self.fetch=fetch or self._fetch;self.enabled=enabled;self.jobs=queue.Queue(maxsize=32);self.pending=set();self.last_fetch=0;self.fetch_lock=threading.Lock()
        self.db=sqlite3.connect(str(Path(directory)/'radio.sqlite3'),check_same_thread=False,isolation_level=None)
        os.chmod(Path(directory)/'radio.sqlite3',0o600)
        self.db.execute('CREATE TABLE IF NOT EXISTS related_artists(artist TEXT PRIMARY KEY,checked INTEGER,names TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS radio_popularity(artist TEXT,title TEXT,listens INTEGER,checked INTEGER,PRIMARY KEY(artist,title))')
        self.db.execute('CREATE TABLE IF NOT EXISTS radio_recommendations(artist TEXT PRIMARY KEY,checked INTEGER,payload TEXT)')
        self.recommendations={a:json.loads(payload) for a,payload in self.db.execute('SELECT artist,payload FROM radio_recommendations ORDER BY checked DESC LIMIT 1000')}
        self.popular=collections.OrderedDict(((a,t),n) for a,t,n in reversed(self.db.execute('SELECT artist,title,listens FROM radio_popularity ORDER BY checked DESC LIMIT 20000').fetchall()))
        self.cache={key:(checked,json.loads(names)) for key,checked,names in self.db.execute('SELECT * FROM related_artists ORDER BY checked DESC LIMIT 1000')}
        if enabled:threading.Thread(target=self.background,daemon=True,name='arrify-radio-metadata').start()

    def get(self,artist):
        key=artist_key(artist)
        with self.lock:
            checked,names=self.cache.get(key,(0,[]));ttl=30*86400 if names else 3600
            if self.enabled and self.now()-checked>ttl and key not in self.pending:
                try:self.jobs.put_nowait((key,artist));self.pending.add(key)
                except queue.Full:pass
            return list(names),checked,key in self.pending

    def _fetch(self,url):
        # Fixed service URLs only; minimum one second between requests, outside
        # playback/account/catalog locks. Bound response bytes and socket time.
        with self.fetch_lock:
            time.sleep(max(0,1.1-(time.monotonic()-self.last_fetch)));self.last_fetch=time.monotonic()
            request=urllib.request.Request(url,headers={'User-Agent':'ArrifyRadio/1.0 (https://arrify.robnets.com)','Accept':'application/json'})
            with urllib.request.urlopen(request,timeout=6) as response:
                data=response.read(2*1024*1024+1)
                if len(data)>2*1024*1024:raise ValueError('Metadata response too large.')
                return json.loads(data)

    def resolve(self,name):
        escaped=name[:200].replace('\\','\\\\').replace('"','\\"')
        result=self.fetch('https://musicbrainz.org/ws/2/artist/?'+urllib.parse.urlencode({'query':'artist:"'+escaped+'"','fmt':'json','limit':5}))
        matches=[a for a in result.get('artists',[]) if artist_key(a.get('name',''))==artist_key(name) and int(a.get('score',0))>=90 and re.fullmatch('[a-f0-9-]{36}',a.get('id',''))]
        if len({a['id'] for a in matches})!=1:return [],[],[]
        mbid=matches[0]['id'];result=self.fetch('https://api.listenbrainz.org/1/lb-radio/artist/'+mbid+'?mode=easy&max_similar_artists=40&max_recordings_per_artist=4&pop_begin=50&pop_end=100')
        names=[];recordings_by_id={}
        if not isinstance(result,dict):return names,[],[]
        for recordings in result.values():
            if not isinstance(recordings,list):continue
            for row in recordings:
                value=row.get('similar_artist_name') if isinstance(row,dict) else None
                if isinstance(value,str) and 0<len(value)<=200 and artist_key(value)!=artist_key(name) and value not in names:names.append(value)
                if isinstance(value,str) and 0<len(value)<=200:
                    rid=row.get('recording_mbid','');count=row.get('total_listen_count',0)
                    if re.fullmatch('[a-f0-9-]{36}',rid) and isinstance(count,(int,float)) and not isinstance(count,bool) and math.isfinite(count) and 0<count<1e12:recordings_by_id[rid]=(value,int(count),row.get('similar_artist_mbid',''))
        # Public radio counts identify popular recordings; metadata maps those
        # identifiers to song titles. No authenticated popularity endpoint needed.
        popularity=[];suggestions=[];profiles={}
        seed_ids=[rid for rid,row in recordings_by_id.items() if artist_key(row[0])==artist_key(name)]
        ids=(seed_ids+sorted((rid for rid in recordings_by_id if rid not in seed_ids),key=lambda rid:recordings_by_id[rid][1],reverse=True))[:100]
        for offset in range(0,len(ids),50):
            try:metadata=self.fetch('https://api.listenbrainz.org/1/metadata/recording/?'+urllib.parse.urlencode({'recording_mbids':','.join(ids[offset:offset+50]),'inc':'artist tag release'}))
            except Exception:continue
            if not isinstance(metadata,dict):continue
            for rid,data in metadata.items():
                if rid not in recordings_by_id or not isinstance(data,dict):continue
                artist,count,artist_id=recordings_by_id[rid];title=data.get('recording',{}).get('name');credit=data.get('artist',{}).get('name','');credits=data.get('artist',{}).get('artists',[])
                identity=artist_key(credit)==artist_key(artist) or any(a.get('artist_mbid')==artist_id and artist_id for a in credits if isinstance(a,dict))
                if isinstance(title,str) and 0<len(title)<=500 and identity:
                    profile=genre_profile(data);key=artist_key(artist)
                    saved=profiles.setdefault(key,{})
                    for family,value in profile.items():saved[family]=max(saved.get(family,0),value)
                    popularity.append((key,recording_key({'artist':artist,'title':title})[1],count))
                    release=data.get('release',{});album=release.get('name','')
                    suggestions.append({'recording_id':rid,'artist':artist,'title':title,'album':album[:500] if isinstance(album,str) else '', 'listens':count})
        seed_profile=profiles.get(artist_key(name),{})
        allowed=lambda artist:artist_key(artist)==artist_key(name) or genre_compatible(seed_profile,profiles.get(artist_key(artist),{}))
        return [artist for artist in names[:80] if allowed(artist)],popularity,[item for item in suggestions if allowed(item['artist'])]

    def update(self,key,name):
        failed=False
        try:names,popularity,suggestions=self.resolve(name)
        except Exception:names,popularity,suggestions=[],[],[];failed=True
        checked=int(self.now());rows=[];evicted=[];old=None
        with self.lock:
            if not names:names=self.cache.get(key,(0,[]))[1]
            if failed:checked-=((30*86400 if names else 3600)-60)
            self.cache[key]=(checked,names)
            if suggestions:self.recommendations[key]=suggestions
            for artist,title,count in popularity:
                identity=(artist,title);self.popular[identity]=max(count,self.popular.get(identity,0));self.popular.move_to_end(identity)
                rows.append((artist,title,self.popular[identity],checked))
            while len(self.popular)>20000:evicted.append(self.popular.popitem(last=False)[0])
            if len(self.cache)>1000:
                old=min(self.cache,key=lambda k:self.cache[k][0]);self.cache.pop(old,None);self.recommendations.pop(old,None)
        # Disk work is deliberately outside the hot memory lock. Slow storage
        # must not delay a station pick while it holds the account transaction.
        try:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.executemany('INSERT OR REPLACE INTO radio_popularity VALUES(?,?,?,?)',rows)
            self.db.executemany('DELETE FROM radio_popularity WHERE artist=? AND title=?',evicted)
            if suggestions:self.db.execute('INSERT OR REPLACE INTO radio_recommendations VALUES(?,?,?)',(key,checked,json.dumps(suggestions)))
            self.db.execute('INSERT OR REPLACE INTO related_artists VALUES(?,?,?)',(key,checked,json.dumps(names)))
            if old:
                self.db.execute('DELETE FROM radio_recommendations WHERE artist=?',(old,));self.db.execute('DELETE FROM related_artists WHERE artist=?',(old,))
            self.db.commit()
        except Exception:self.db.rollback();raise

    def background(self):
        while True:
            key,name=self.jobs.get()
            try:self.update(key,name)
            except Exception:pass
            finally:
                with self.lock:self.pending.discard(key)
                self.jobs.task_done()


class Radio:
    def __init__(self,directory,library,similar=None):
        self.lib=library;self.similar=similar or SimilarArtists(directory,enabled=os.environ.get('ARRIFY_RADIO_ENRICH','1')=='1');self.rng=random.SystemRandom();self.lock=threading.RLock();self.pools=collections.OrderedDict()
        self.directory=Path(directory)
        with contextlib.closing(sqlite3.connect(str(self.directory/'radio.sqlite3'))) as db:
            db.execute('CREATE TABLE IF NOT EXISTS radio_seeds(seed TEXT PRIMARY KEY,payload TEXT,created INTEGER)')
            self.seeds={key:json.loads(payload) for key,payload in reversed(db.execute('SELECT seed,payload FROM radio_seeds ORDER BY created DESC LIMIT 1000').fetchall())}


    def register(self,artist,title=''):
        if not isinstance(artist,str) or not 2<=len(artist)<=200 or not artist_key(artist):raise ValueError('Choose an artist from the catalog.')
        if not isinstance(title,str) or len(title)>500:raise ValueError('Invalid song title.')
        with self.lib.index_lock:index=self.lib.radio_index
        if title:
            local=index.recording_ids.get(recording_key({'artist':artist,'title':title}),set())
            if local:return index.seed('track',min(local))
        elif artist_key(artist) in index.artists:
            ident=next(key for key,value in index.ids.items() if value==artist_key(artist));return index.seed('artist',ident)
        key=hashlib.sha256(json.dumps([artist_key(artist),normalize(title)]).encode()).hexdigest()[:24]
        descriptor={'kind':'catalog','seed':key,'artist':artist,'title':title,'name':(title or artist)+' Radio','artwork':'/assets/icon-192.png'}
        with self.lock:
            self.seeds.pop(key,None);self.seeds[key]=descriptor
            if len(self.seeds)>1000:self.seeds.pop(next(iter(self.seeds)))
        # Separate connection: metadata-worker disk transactions never hold the
        # memory locks used by active playback.
        with contextlib.closing(sqlite3.connect(str(self.directory/'radio.sqlite3'))) as db:
            db.execute('INSERT OR REPLACE INTO radio_seeds VALUES(?,?,?)',(key,json.dumps(descriptor),int(time.time())))
            db.execute('DELETE FROM radio_seeds WHERE seed NOT IN (SELECT seed FROM radio_seeds ORDER BY created DESC LIMIT 1000)')
            db.commit()
        return descriptor

    def describe(self,kind,seed):
        if kind=='catalog':
            with self.lock:descriptor=self.seeds.get(str(seed))
            if descriptor:return dict(descriptor)
            raise ValueError('Station not found. Open it from the catalog again.')
        with self.lib.index_lock:return self.lib.radio_index.seed(kind,seed)

    def pool(self,seed):
        with self.lib.index_lock:index=self.lib.radio_index
        seed=self.describe('catalog',seed.get('seed')) if seed.get('kind')=='catalog' else index.seed(seed.get('kind'),seed.get('seed'));related,checked,pending=self.similar.get(seed['artist']);key=(id(index),seed['kind'],seed['seed'],checked)
        with self.lock:
            cached=self.pools.get(key)
        if cached is None:
            weights=index.weights(seed,related);cached=(index,seed,weights)
            with self.lock:
                for old in list(self.pools):
                    if old[0]!=id(index):self.pools.pop(old,None)
                self.pools[key]=cached
                while len(self.pools)>128:self.pools.popitem(last=False)
        return *cached,pending

    def choose(self,index,weights,history,blocked=()):
        recent=[index.tracks[i] for i in history[-50:] if i in index.tracks];seen={index.recordings[t['id']] for t in recent};last=index.recordings[recent[-1]['id']] if recent else None;last_artists={artist_key(t['artist']) for t in recent[-2:]};last_artist={artist_key(recent[-1]['artist'])} if recent else set();blocked=set(blocked)
        for avoid_seen,avoid_artists in ((True,last_artists),(True,last_artist),(True,set()),(False,last_artist),(False,set())):
            choices={}
            for artist in weights:
                ids=[i for i in index.artists[artist] if i not in blocked and index.recordings[i]!=last and (not avoid_seen or index.recordings[i] not in seen)]
                if ids and artist not in avoid_artists:choices[artist]=ids
            if choices:
                artist=self.rng.choices(list(choices),weights=[weights[a] for a in choices],k=1)[0]
                ids=choices[artist]
                with self.similar.lock:counts={i:self.similar.popular.get(index.recordings[i],0) for i in ids}
                familiar=[i for i in ids if index.hits[i] or counts[i]]
                # Familiarity is a preference within the similarity-qualified
                # artist pool, never a way for unrelated hits to enter radio.
                if familiar and self.rng.random()<.8:ids=familiar
                return self.rng.choices(ids,weights=[1+2*math.log1p(index.hits[i])+math.log1p(counts[i]) for i in ids],k=1)[0]
        return None

    def pick(self,seed,history):
        try:index,_,weights,_=self.pool(seed)
        except ValueError:return None
        return self.choose(index,weights,history)

    def start(self,seed,selected=None):
        index,seed,weights,_=self.pool(seed)
        if selected is not None:
            try:selected=int(selected);track=index.tracks[selected]
            except (KeyError,ValueError,TypeError):raise ValueError('Song unavailable.') from None
            if not any(artist_key(n) in weights for n in [track['artist']]+[r['name'] for r in track.get('artist_refs',[])]):raise ValueError('Song is outside this station.')
            return selected
        own=artist_key(seed['artist']);chosen=seed.get('track_id') or self.choose(index,{own:weights[own]} if own in weights else weights,[])
        if chosen is None:raise ValueError('No related songs are available yet. Review the station recommendations to add music.')
        return chosen

    def suggestions(self,seed,index,limit=20):
        with self.similar.lock:raw=list(self.similar.recommendations.get(artist_key(seed['artist']),[]))
        owned=index.known_recordings;albums=index.owned_albums;seen=set();artists=collections.Counter();result=[]
        for item in sorted(raw,key=lambda x:x.get('listens',0),reverse=True):
            key=recording_key(item);album=(key[0],normalize(item.get('album','')));identity=album if album[1] else key
            if key in owned or album[1] and album in albums or identity in seen or artists[key[0]]>=2:continue
            seen.add(identity);artists[key[0]]+=1;result.append(item)
            if len(result)>=limit:break
        return result

    def recommendation(self,kind,seed,recording_id):
        descriptor=self.describe(kind,seed);index,descriptor,_,_=self.pool(descriptor)
        found=next((x for x in self.suggestions(descriptor,index) if x['recording_id']==recording_id),None)
        if not found:raise ValueError('Refresh the station recommendations; this song may already be available.')
        return found

    def preview(self,kind,seed,limit=30):
        descriptor=self.describe(kind,seed);index,descriptor,weights,pending=self.pool(descriptor);ids=[];history=[]
        own=artist_key(descriptor['artist']);first=descriptor.get('track_id') or self.choose(index,{own:weights[own]} if own in weights else weights,[])
        if first is not None:ids.append(first);history.append(first)
        blocked=set()
        for i in ids:blocked.update(index.recording_ids[index.recordings[i]])
        # Active picks use a fresh index; this list is only a page preview.
        for _ in range(min(50,limit)-len(ids)):
            tid=self.choose(index,weights,history,blocked)
            if tid is None:break
            ids.append(tid);history.append(tid);blocked.update(index.recording_ids[index.recordings[tid]])
        total=len({index.recordings[i] for a in weights for i in index.artists[a]});sparse=len(weights)<5 or total<30
        return {'suggestions':self.suggestions(descriptor,index) if sparse else [],'sparse':sparse,'station':descriptor,'tracks':[index.tracks[i] for i in ids],'total':total,'artists':len(weights),'enriching':pending,'limited':len(weights)<2}

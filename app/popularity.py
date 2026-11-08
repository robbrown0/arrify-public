"""Cached, artist-scoped ListenBrainz community listening counts."""
from html.parser import HTMLParser
import math
from pathlib import Path
import datetime as dt, urllib.request
import json, queue, sqlite3, threading, time, unicodedata, urllib.parse, re

def identity(value):
    # Keep version words (live, remix, acoustic) distinct.
    return ''.join(c for c in unicodedata.normalize('NFKC',value).casefold() if c.isalnum())

class Popularity:
    def __init__(self,directory,fetch,artists=lambda:[]):
        self.signals=Signals(directory,fetch,artists);self.fetch=fetch;self.lock=threading.RLock();self.pending=set();self.jobs=queue.PriorityQueue(maxsize=100);self.artists=artists
        self.db=sqlite3.connect(str(directory)+'/song-popularity.sqlite3',check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS artists(name TEXT PRIMARY KEY,checked INTEGER,payload TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS refresh_state(name TEXT PRIMARY KEY,attempt INTEGER,viewed INTEGER,status TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS snapshots(name TEXT,day INTEGER,payload TEXT,PRIMARY KEY(name,day))')
        self.refresh={n:(a,v,st) for n,a,v,st in self.db.execute('SELECT * FROM refresh_state')}
        self.cache={name:(checked,json.loads(payload)) for name,checked,payload in self.db.execute('SELECT * FROM artists')}
        threading.Thread(target=self.worker,daemon=True,name='arrify-song-popularity').start()
        threading.Thread(target=self.schedule,daemon=True,name='arrify-popularity-scheduler').start()

    def get(self,name,tracks):
        key=identity(name)
        with self.lock:
            checked,counts=self.cache.get(key,(0,{}))
            attempt,viewed,status=self.refresh.get(key,(0,0,''))
            self.refresh[key]=(attempt,int(time.time()),status)
            self.db.execute('INSERT OR REPLACE INTO refresh_state VALUES(?,?,?,?)',(key,attempt,int(time.time()),status));self.db.commit()
            self.enqueue(key,name,0)
            pending=key in self.pending
            values=[counts.get(identity(t['title'])) for t in tracks]
        self.signals.request_catalog(name,0)
        return [dict(t,popularity=self.signals.score(t,v)) for t,v in zip(tracks,values)],pending or identity(name) in self.signals.catalog_pending

    def resolve(self,name):
        query='artist:"'+name.replace('\\','\\\\').replace('"','\\"')+'"'
        data=self.fetch('https://musicbrainz.org/ws/2/artist/?'+urllib.parse.urlencode({'query':query,'fmt':'json','limit':5}))
        matches={a['id'] for a in data.get('artists',[]) if identity(a.get('name',''))==identity(name) and int(a.get('score',0))>=90 and re.fullmatch('[a-f0-9-]{36}',a.get('id',''))}
        if len(matches)!=1:return {}
        mbid=matches.pop()
        data=self.fetch('https://api.listenbrainz.org/1/lb-radio/artist/'+mbid+'?mode=easy&max_similar_artists=0&max_recordings_per_artist=250&pop_begin=0&pop_end=100')
        recordings={r['recording_mbid']:r['total_listen_count'] for r in data.get(mbid,[]) if r.get('similar_artist_mbid')==mbid and isinstance(r.get('total_listen_count'),int) and r['total_listen_count']>0}
        counts={};ids=list(recordings)
        for offset in range(0,len(ids),50):
            metadata=self.fetch('https://api.listenbrainz.org/1/metadata/recording/?'+urllib.parse.urlencode({'recording_mbids':','.join(ids[offset:offset+50]),'inc':'artist'}))
            for rid,item in metadata.items():
                if rid not in recordings:continue
                artist=item.get('artist',{})
                if not any(a.get('artist_mbid')==mbid for a in artist.get('artists',[])):continue
                title=item.get('recording',{}).get('name')
                if title:counts[identity(title)]=max(counts.get(identity(title),0),recordings[rid])
        return counts

    def enqueue(self,key,name,priority):
        checked,counts=self.cache.get(key,(0,{}));attempt,viewed,status=self.refresh.get(key,(0,0,''))
        ttl=86400 if time.time()-viewed<7*86400 else 7*86400
        if key in self.pending or time.time()-checked<ttl or time.time()-attempt<3600:return
        try:self.jobs.put_nowait((priority,key,name));self.pending.add(key)
        except queue.Full:pass

    def schedule(self):
        while True:
            try:
                names=list(self.artists())
                with self.lock:
                    names.sort(key=lambda n:self.cache.get(identity(n),(0,{}))[0])
                    for name in names:
                        if len(self.pending)>=6:break
                        self.enqueue(identity(name),name,1)
            except Exception:pass
            time.sleep(30)

    def worker(self):
        while True:
            priority,key,name=self.jobs.get()
            counts=None
            try:
                counts=self.resolve(name)
                status='ready' if counts else 'no_data'
            except Exception:status='retry'
            try:
                with self.lock:
                    now=int(time.time());viewed=self.refresh.get(key,(0,0,''))[1]
                    self.refresh[key]=(now,viewed,status)
                    self.db.execute('INSERT OR REPLACE INTO refresh_state VALUES(?,?,?,?)',(key,now,viewed,status))
                    # Never erase prior good counts on failures or empty responses.
                    if counts or (counts is not None and not self.cache.get(key,(0,{}))[1]):
                        self.cache[key]=(now,counts)
                        self.db.execute('INSERT OR REPLACE INTO artists VALUES(?,?,?)',(key,now,json.dumps(counts)))
                    if counts:
                        self.db.execute('INSERT OR REPLACE INTO snapshots VALUES(?,?,?)',(key,now//86400,json.dumps(counts)))
                        self.db.execute('DELETE FROM snapshots WHERE day<?',(now//86400-35,))
                    self.db.commit()
            finally:
                with self.lock:self.pending.discard(key)
                self.jobs.task_done()
            time.sleep(2)


class Signals:
    """Cross-artist evidence, with source-specific thresholds and dated history."""
    STATIONS='thepulse thespectrum altnation thecoffeehouse siriusxmhits1 thehighway bluegrassjunction thebridge classicvinyl classicrewind 60sgold 80son8 90son9 pop2k the10sspot theblend poprocks lithium octane theheat hiphopnation heartsoul outlawcountry primecountry y2kountry williesroadhouse realjazz watercolors bbkingsbluesville thevillage siriusxmu bpm themessage 1stwave'.split()
    def __init__(self,directory,fetch,artists=lambda:[]):
        self.directory=directory;self.fetch=fetch;self.lock=threading.RLock();self.index={}
        self.db=sqlite3.connect(str(directory)+'/popularity-signals.sqlite3',check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,fetched INTEGER,payload TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS history(id TEXT,day INTEGER,payload TEXT,PRIMARY KEY(id,day))')
        self.sources={k:(stamp,json.loads(data)) for k,stamp,data in self.db.execute('SELECT * FROM sources')}
        self.catalog_artists=artists;self.catalog_queue=queue.PriorityQueue(maxsize=100);self.catalog_pending=set();self.catalog_map={};self.catalog_map_checked=0
        self.db.execute('CREATE TABLE IF NOT EXISTS catalog_attempts(name TEXT PRIMARY KEY,checked INTEGER,status TEXT)')
        self.catalog_attempts={name:(checked,status) for name,checked,status in self.db.execute('SELECT * FROM catalog_attempts')}
        try:
            cached=json.loads((Path(directory)/'catalog-provider-artists.json').read_text());self.catalog_map=cached['artists'];self.catalog_map_checked=cached['checked']
        except (OSError,ValueError,KeyError):pass
        self.load_history()
        self.rebuild()
        threading.Thread(target=self.run,daemon=True,name='arrify-popularity-signals').start()
        threading.Thread(target=self.catalog_worker,daemon=True,name='arrify-catalog-reach').start()

    def request_catalog(self,name,priority):
        key=identity(name)
        with self.lock:
            checked,status=self.catalog_attempts.get(key,(0,''))
            ttl=3600 if status=='retry' else 86400 if priority==0 else 7*86400
            if key in self.catalog_pending or time.time()-checked<ttl:return
            try:self.catalog_queue.put_nowait((priority,key,name));self.catalog_pending.add(key)
            except queue.Full:pass

    def refresh_catalog_map(self):
        if self.catalog_map and time.time()-self.catalog_map_checked<7*86400:return
        parser=ChartHTML();parser.feed(catalog_html('https://kworb.net/spotify/artists.html'));mapping={}
        for path,name in parser.links:
            if re.fullmatch(r'/spotify/artist/[A-Za-z0-9]{22}_songs.html',path):mapping.setdefault(identity(name),set()).add(path)
        mapping={k:list(v)[0] for k,v in mapping.items() if len(v)==1}
        if len(mapping)<100:raise ValueError('Incomplete artist index')
        self.catalog_map=mapping;self.catalog_map_checked=int(time.time())
        path=Path(self.directory)/'catalog-provider-artists.json';temp=path.with_suffix('.tmp');temp.write_text(json.dumps({'checked':self.catalog_map_checked,'artists':mapping}));temp.replace(path)

    def catalog_worker(self):
        while True:
            try:priority,key,name=self.catalog_queue.get(timeout=3)
            except queue.Empty:
                for name in self.catalog_artists():
                    if len(self.catalog_pending)>=6:break
                    self.request_catalog(name,1)
                continue
            status='retry'
            try:
                self.refresh_catalog_map();path=self.catalog_map.get(key)
                if path:
                    url='https://kworb.net'+path;items=parse_catalog(catalog_html(url),name,url)
                    self.save('catalog:'+key,int(time.time()),items);status='ready'
                else:status='unlisted'
            except Exception:pass
            finally:
                with self.lock:
                    checked=int(time.time());self.catalog_attempts[key]=(checked,status)
                    self.db.execute('INSERT OR REPLACE INTO catalog_attempts VALUES(?,?,?)',(key,checked,status));self.db.commit();self.catalog_pending.discard(key)
                self.catalog_queue.task_done()
            time.sleep(2)

    def load_history(self):
        try:rows=json.loads((Path(self.directory)/'verified-chart-history.json').read_text())
        except (OSError,ValueError):rows=[]
        items=[]
        for row in rows:
            if not isinstance(row,dict):continue
            peak=row.get('rank');year=row.get('year')
            if not isinstance(peak,int) or not 1<=peak<=100 or not isinstance(year,int) or not 1950<=year<=dt.datetime.now().year:continue
            if not all(isinstance(row.get(k),str) and row[k] for k in ('artist','title','source','url')):continue
            items.append(dict(row,kind='historical',score=85 if peak<=10 else 60 if peak<=40 else 25))
        with self.lock:
            self.sources['verified:history']=(int(time.time()),items)

    def save(self,key,stamp,items):
        if not items:return
        with self.lock:
            self.sources[key]=(stamp,items)
            self.db.execute('INSERT OR REPLACE INTO sources VALUES(?,?,?)',(key,stamp,json.dumps(items)))
            self.db.execute('INSERT OR REPLACE INTO history VALUES(?,?,?)',(key,stamp//86400,json.dumps(items)))
            self.db.execute('DELETE FROM history WHERE day<?',(int(time.time())//86400-35,));self.db.commit()
        self.rebuild()

    def rebuild(self):
        index={};now=time.time()
        with self.lock:
            for source,(stamp,items) in self.sources.items():
                if source!='verified:history' and now-stamp>(30 if source.startswith('catalog:') else 7)*86400:continue
                previous=self.db.execute('SELECT payload FROM history WHERE id=? AND day<? AND day>=? ORDER BY day DESC LIMIT 1',(source,stamp//86400,stamp//86400-8)).fetchone()
                weekly=self.db.execute('SELECT payload FROM history WHERE id=? AND day<=? AND day>=? ORDER BY day DESC LIMIT 1',(source,stamp//86400-7,stamp//86400-14)).fetchone() if source.startswith('catalog:') else None
                week_counts={identity(x['title']):x.get('daily') for x in json.loads(weekly[0])} if weekly else {}
                prior={ (identity(x['artist']),identity(x['title'])):x['rank'] for x in json.loads(previous[0])} if previous else {}
                for item in items:
                    if item.get('kind')=='catalog_reach' and now-item.get('reported_ts',0)>30*86400:continue
                    if item.get('published_ts') and now-item['published_ts']>14*86400:continue
                    key=(identity(item['artist']),identity(item['title']));e=dict(item,updated=stamp,stale=now-stamp>2*86400)
                    if e.get('kind') not in ('historical','catalog_reach') and not e.get('rising') and key in prior and prior[key]-item['rank']>=5:e['rising']=prior[key]-item['rank']
                    if e.get('kind')=='catalog_reach':
                        daily=e.get('daily') or 0;before=week_counts.get(identity(e['title'])) or 0
                        if now-e.get('reported_ts',0)<=3*86400 and before>=10000 and daily>=max(25000,before*1.5) and daily-before>=10000:
                            e['rising']=True;e['growth_pct']=round(100*(daily/before-1));e['movement_period']='roughly one week ago'
                    index.setdefault(key,[]).append(e)
            self.index=index

    @staticmethod
    def evidence_score(e):
        kind=e.get('kind');rank=e.get('rank',9999)
        if kind=='catalog':return 0
        if kind=='catalog_reach':return reach_score(e.get('total',0),e.get('daily') if time.time()-e.get('reported_ts',0)<=3*86400 else None)
        if kind=='satellite':
            plays=e.get('value',0)
            return 90 if plays>=150 else 70 if plays>=60 else 50 if plays>=30 else 0
        if kind=='streaming':return 90 if rank<=10 else 70 if rank<=40 else 50 if rank<=100 else 0
        if kind=='downloads':return 50 if rank<=10 else 0
        if kind=='radio':
            if isinstance(e.get('stations'),int) and e['stations']<3:return 0
            return 90 if rank<=10 else 70 if rank<=25 else 50 if rank<=40 else 0
        if kind=='historical':return 90 if rank<=10 else 70 if rank<=40 else 50 if rank<=100 else 0
        return 0

    def score(self,t,listens=None):
        # Exact normalized title and credited artist; do not merge live/remix versions.
        artists={identity(t.get('artist',''))}|{identity(a['name']) for a in t.get('artist_refs',[])}
        evidence=[];seen=set()
        with self.lock:
            for artist in artists:
                for e in self.index.get((artist,identity(t['title'])),[]):
                    token=(e['source'],e['rank'])
                    if token not in seen:evidence.append(dict(e,score=self.evidence_score(e)));seen.add(token)
        if listens:
            # Community lifetime counts support discovery, never establish a current hit alone.
            evidence.append({'source':'ListenBrainz lifetime listens','value':listens,'score':0,'kind':'catalog'})
        if not evidence:return None
        best=max(e['score'] for e in evidence);kinds={e['kind'] for e in evidence if e['score']>=50}
        score=min(100,best+(5 if len(kinds)>=2 else 0))
        coins=3 if score>=90 else 2 if score>=70 else 1 if score>=50 else 0
        return {'score':score,'coverage':'measured' if any(e['kind']!='catalog' for e in evidence) else 'limited','doubloons':coins,'standout':coins>0,'listens':listens,'evidence':sorted(evidence,key=lambda e:-e['score']),'rising':any(e.get('rising') for e in evidence),'source':'Arrify multi-source popularity'}

    def mediabase(self):
        if time.time()-getattr(self,'radio_attempt',0)<86400:return
        self.radio_attempt=time.time()
        req=urllib.request.Request('https://home.mediabase.com/',headers={'User-Agent':'ArrifyPopularity/1.0 (https://arrify.robnets.com)'})
        with urllib.request.urlopen(req,timeout=15) as response:html=response.read(2*1024*1024).decode('utf-8')
        marker='window.mbChartData = '
        if marker not in html:raise ValueError('Mediabase public chart unavailable')
        data,_=json.JSONDecoder().raw_decode(html.split(marker,1)[1])
        for family,info in data.get('families',{}).items():
            for fmt in info.get('formats',[]):
                key=family+':'+fmt.get('code',fmt.get('key',''));date=data.get('weekDates',{}).get(key,data.get('weekDate',''))
                published=dt.datetime.strptime(date,'%b %d, %Y').replace(tzinfo=dt.timezone.utc).timestamp()
                if not 0<=time.time()-published<=14*86400:continue
                items=[]
                for row in data.get('items',{}).get(key,[]):
                    rank=int(row['rank']);last=row.get('lw');gain=last-rank if isinstance(last,int) and last>rank else 0
                    if not row.get('title') or not row.get('artist'):continue
                    items.append({'artist':row['artist'],'title':row['title'],'rank':rank,'source':'Mediabase '+info.get('label',family)+' '+fmt['label'],'kind':'radio','score':90 if rank<=10 else 70 if rank<=25 else 50,'url':'https://home.mediabase.com/','published':date,'published_ts':published,'stations':row.get('stationsOn'),'rising':gain,'movement_period':'previous published chart'})
                self.save('mediabase:'+key,int(time.time()),items)

    def update(self):
        self.load_history()
        try:self.mediabase()
        except Exception:pass
        # Reuse independently refreshed, public Apple charts; retain their true fetch dates.
        try:
            with sqlite3.connect('file:'+str(self.directory)+'/browse.sqlite3?mode=ro',uri=True) as db:
                charts=list(db.execute('SELECT key,payload FROM browse_charts'))
            for key,payload in charts:
                data=json.loads(payload);stamp=data.get('fetched',0);sid='apple:'+key
                if not stamp or self.sources.get(sid,(0,[]))[0]>=stamp:continue
                items=[]
                for row in data.get('items',[]):
                    rank=row['rank'];stream=key=='overall'
                    score=(90 if rank<=10 else 70 if rank<=40 else 50) if stream else (60 if rank<=10 else 45 if rank<=40 else 25)
                    items.append(dict(row,source='Apple Music US most played' if stream else 'iTunes US '+key+' downloads',kind='streaming' if stream else 'downloads',score=score,url=data.get('source_url','https://music.apple.com/us/charts')))
                self.save(sid,stamp,items)
        except Exception:pass
        for station in self.STATIONS:
            sid='xm:'+station
            if time.time()-self.sources.get(sid,(0,[]))[0]<86400:continue
            try:
                data=self.fetch('https://xmplaylist.com/api/station/'+station+'/most-heard');name=data.get('channel',{}).get('name',station);items=[]
                for rank,row in enumerate(data.get('results',[]),1):
                    track=row.get('track',{});plays=row.get('plays',0)
                    if not track.get('title') or not track.get('artists') or not isinstance(plays,int):continue
                    score=90 if plays>=150 else 70 if plays>=60 else 50 if plays>=20 else 25 if plays>=5 else 0
                    items.append({'artist':', '.join(track['artists']),'title':track['title'],'rank':rank,'value':plays,'window':'30 days','source':'SiriusXM '+name+' via xmplaylist','kind':'satellite','score':score,'url':'https://xmplaylist.com/station/'+station+'/most-heard'})
                self.save(sid,int(time.time()),items)
            except Exception:pass
            time.sleep(1.2)
        self.rebuild()

    def run(self):
        while True:
            try:self.update()
            except Exception:pass
            time.sleep(3600)

class ChartHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.rows=[];self.row=None;self.cell=None;self.links=[];self.link=None
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='tr':self.row=[]
        if tag in ('td','th') and self.row is not None:self.cell={'text':'','href':''}
        if tag=='a':
            self.link=[attrs.get('href',''),'']
            if self.cell is not None:self.cell['href']=attrs.get('href','')
    def handle_data(self,data):
        if self.cell is not None:self.cell['text']+=data
        if self.link is not None:self.link[1]+=data
    def handle_endtag(self,tag):
        if tag=='a' and self.link is not None:self.links.append(tuple(self.link));self.link=None
        if tag in ('td','th') and self.cell is not None:
            if self.row is not None:self.row.append(self.cell)
            self.cell=None
        if tag=='tr' and self.row is not None:self.rows.append(self.row);self.row=None

def catalog_html(url):
    req=urllib.request.Request(url,headers={'User-Agent':'ArrifyPopularity/1.0 (personal music library; cached daily)'})
    with urllib.request.urlopen(req,timeout=15) as r:data=r.read(3*1024*1024+1)
    if len(data)>3*1024*1024:raise ValueError('Catalog page too large')
    return data.decode('utf-8')

def parse_catalog(html,artist,url):
    parser=ChartHTML();parser.feed(html)
    date=re.search(r'Last updated:\s*(\d{4}/\d{2}/\d{2})',html)
    if not date:raise ValueError('Missing provider date')
    stamp=int(dt.datetime.strptime(date[1],'%Y/%m/%d').replace(tzinfo=dt.timezone.utc).timestamp())
    if not -86400<=time.time()-stamp<=30*86400:raise ValueError('Stale provider page')
    rows=[];seen=set()
    for cells in parser.rows:
        if len(cells)!=3 or not re.fullmatch(r'https://open.spotify.com/track/[A-Za-z0-9]{22}',cells[0]['href']):continue
        title=cells[0]['text'].strip().lstrip('*').strip()
        total=cells[1]['text'].strip().replace(',','');daily=cells[2]['text'].strip().replace(',','')
        if not total.isdigit() or not (daily.isdigit() or daily in ('','-')):continue
        key=identity(title)
        if not title or key in seen:continue
        seen.add(key)
        rows.append({'artist':artist,'title':title,'rank':len(rows)+1,'total':int(total),'daily':int(daily) if daily.isdigit() else None,'kind':'catalog_reach','source':'Spotify streams reported by Kworb','url':url,'recording_url':cells[0]['href'],'reported':date[1],'reported_ts':stamp,'score':0})
    if not rows:raise ValueError('No validated song rows')
    return rows

def reach_score(total,daily):
    # Fixed cross-artist anchors; log interpolation differentiates songs within tiers.
    # Lifetime: 100M / 300M / 750M. Daily: 100K / 300K / 750K.
    def scale(value,anchors):
        if not value or value<anchors[0]:return 0
        for i in range(2):
            if value<anchors[i+1]:return [50,70][i]+20*math.log(value/anchors[i])/math.log(anchors[i+1]/anchors[i])
        return min(99,90+9*math.log(max(1,value/anchors[2]))/math.log(4))
    return round(max(scale(total,[100000000,300000000,750000000]),scale(daily,[100000,300000,750000])),2)

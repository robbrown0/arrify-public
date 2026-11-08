"""Immutable in-memory local search index. No network calls or catalog mutations."""
import bisect
import collections
import hashlib
import re
import unicodedata

APOSTROPHES=str.maketrans({'’':'',"'":'','`':''})
# Explicit known spellings only; never rewrite punctuation globally into letters.
ARTIST_ALIASES = {
    'p!nk': 'pink',
    'ke$ha': 'kesha',
    'a$ap rocky': 'asap rocky',
    'a$ap ferg': 'asap ferg',
    'ty dolla $ign': 'ty dolla sign',
    'chvrches': 'churches',
    'ac/dc': 'acdc',
    'ac dc': 'acdc',
    'r.e.m.': 'rem',
    'r.e.m': 'rem',
}
ALIAS_PATTERN = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(a) for a in sorted(ARTIST_ALIASES,key=len,reverse=True)) + r')(?!\w)')

def normalize(value):
    value=unicodedata.normalize('NFKD',str(value).casefold().translate(APOSTROPHES))
    value=ALIAS_PATTERN.sub(lambda match:ARTIST_ALIASES[match.group()],value)
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in value if not unicodedata.combining(c)).split())

def near(a,b):
    """One insertion, deletion, substitution or adjacent transposition."""
    if a==b:return True
    if abs(len(a)-len(b))>1:return False
    if len(a)==len(b):
        different=[i for i in range(len(a)) if a[i]!=b[i]]
        return len(different)==1 or (len(different)==2 and different[1]==different[0]+1 and a[different[0]]==b[different[1]] and a[different[1]]==b[different[0]])
    if len(a)>len(b):a,b=b,a
    i=0
    while i<len(a) and a[i]==b[i]:i+=1
    return a[i:]==b[i+1:]

class RankedIndex:
    def __init__(self,items,fields):
        self.items={x['id']:x for x in items}
        self.fields=fields
        self.docs={};self.postings=collections.defaultdict(set)
        self.compact=collections.defaultdict(set)
        # Construction-local memo: share repeated artist/album strings and avoid
        # normalizing them for every track. No cross-catalog/global retention.
        normalized={}
        def shared(value):
            key=str(value)
            if key not in normalized:normalized[key]=normalize(value)
            return normalized[key]
        for key,item in self.items.items():
            values=tuple(shared(item.get(f,'')) for f in fields)
            self.docs[key]=values
            for value in values:
                if value:self.compact[value.replace(' ','')].add(key)
                for token in set(value.split()):self.postings[token].add(key)
        self.compact_words=sorted(self.compact)
        self.words=sorted(self.postings)
        self.lengths=collections.defaultdict(list)
        for word in self.words:self.lengths[len(word)].append(word)

    def matches(self,word,fuzzy=False):
        ids=set(self.postings.get(word,()))
        at=bisect.bisect_left(self.words,word)
        # Prefix expansion is bounded by the finite index, never a regex or SQL expression.
        for candidate in self.words[at:]:
            if not candidate.startswith(word):break
            ids.update(self.postings[candidate])
        if fuzzy and len(word)>=4:
            checked=0;options=[]
            for length in (len(word),len(word)-1,len(word)+1):
                for candidate in self.lengths.get(length,()):
                    checked+=1
                    if checked>12000:break
                    if near(word,candidate):options.append(candidate)
                if checked>12000:break
            for candidate in sorted(options,key=lambda x:(not x.startswith(word[:2]),abs(len(x)-len(word)),x))[:24]:ids.update(self.postings[candidate])
        return ids

    def search(self,query,offset=0,limit=60):
        q=normalize(query[:200]);tokens=list(dict.fromkeys(q.split()))
        if len(tokens)>16:return {'items':[],'total':0,'fuzzy':False}
        filtered=[w for w in tokens if w not in ('the','and')]
        if filtered:tokens=filtered
        if not tokens:return {'items':[],'total':0,'fuzzy':False}
        def candidates(fuzzy):
            groups=[self.matches(w,fuzzy) for w in tokens]
            result=set.intersection(*groups) if groups else set()
            result.update(self.compact.get(q.replace(' ',''),()))
            if ' ' not in q and len(q)>=4:
                at=bisect.bisect_left(self.compact_words,q)
                for word in self.compact_words[at:]:
                    if not word.startswith(q):break
                    result.update(self.compact[word])
            return result
        ids=candidates(False);fuzzy=False
        if not ids and all(len(w)>=2 for w in tokens):ids=candidates(True);fuzzy=bool(ids)
        def score(key):
            values=self.docs[key];points=0
            for i,value in enumerate(values):
                weight=max(1,4-i)
                if value==q:points=max(points,10000-i*1000)
                elif value.replace(' ','')==q.replace(' ',''):points=max(points,9500-i*1000)
                elif value.startswith(q):points=max(points,7000-i*500)
                elif q in value:points=max(points,5000-i*300)
                words=value.split()
                points+=weight*sum(30 if t in words else 15 if any(w.startswith(t) for w in words) else 0 for t in tokens)
            return (-points,tuple(values),str(key))
        ordered=sorted(ids,key=score)
        return {'items':[self.items[k] for k in ordered[offset:offset+limit]],'total':len(ordered),'fuzzy':fuzzy}

class Search:
    def __init__(self,tracks,albums,artists):
        self.revision=hashlib.sha256(('search-v3:'+repr(sorted((t['id'],t.get('title',''),t.get('artist',''),t.get('album',''),t.get('album_artist','')) for t in tracks))).encode()).hexdigest()[:24]
        self.tracks=RankedIndex(tracks,('title','artist','album','album_artist'))
        self.albums=RankedIndex(albums,('name','artist'))
        self.artists=RankedIndex(artists,('name',))
    def query(self,q,offset=0,limit=60):
        songs=self.tracks.search(q,offset,limit)
        albums=self.albums.search(q,0,12)
        artists=self.artists.search(q,0,12)
        return {'revision':self.revision,'tracks':songs['items'],'total':songs['total'],'albums':[{k:v for k,v in x.items() if k!='track_ids'} | {'tracks':len(x['track_ids'])} for x in albums['items']], 'artists':[{k:v for k,v in x.items() if k!='track_ids'} | {'tracks':len(x['track_ids'])} for x in artists['items']], 'fuzzy':songs['fuzzy'],'next_offset':offset+len(songs['items'])}

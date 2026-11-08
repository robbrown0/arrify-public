import re
import unicodedata

def norm(s):
    s=''.join(c for c in unicodedata.normalize('NFKD',s or '') if not unicodedata.combining(c)).casefold()
    return re.sub(r'[^\w]+',' ',s).strip()

def title(s):
    s=re.sub(r'\s*[\[(](?:feat\.?|ft\.?|featuring)\s+.*?[\])]','',s or '',flags=re.I)
    s=re.sub(r'\s*[-–(\[]\s*(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?[)\]]?\s*$','',s,flags=re.I)
    return norm(s)

def primary(s):
    s=re.sub(r'\s+\(.*\)$','',s or '')
    return norm(re.split(r'\s+(?:feat\.?|ft\.?|featuring|&|with|and|x)\s+|,\s*',s,flags=re.I)[0])

def key(artist,song): return primary(artist)+'|'+title(song)

def versions(s):
    return set(re.findall(r'\b(?:live|acoustic|remix|instrumental|karaoke|sped|slowed|demo|rerecorded)\b',norm(s)))

def recording_versions(track):
    result=versions(track.get('title',''))
    album=norm(track.get('album',''))
    # This live album has studio-looking titles in common embedded tags.
    if primary(track.get('artist',''))=='blue oyster cult' and album=='some enchanted evening':result.add('live')
    return result

class Ownership:
    def __init__(self,tracks):
        self.ids=set(); self.isrcs=set(); self.names=set()
        for t in tracks:
            if not t.get('owned',True): continue
            self.ids.update(filter(None,t.get('recording_ids',[])))
            self.isrcs.update(filter(None,t.get('isrcs',[])))
            if primary(t.get('artist','')) and title(t.get('title','')):
                self.names.add((key(t.get('artist',''),t.get('title','')),frozenset(recording_versions(t))))
    def owns(self,candidate):
        if candidate.get('recording_id') in self.ids: return 'recording-mbid'
        if set(candidate.get('isrcs',[])) & self.isrcs: return 'isrc'
        if (key(candidate['artist'],candidate['title']),frozenset(versions(candidate['title']))) in self.names: return 'normalized-artist-title'
        return None

def album_title(s):
    return norm(re.sub(r'\s*[\[(](?:deluxe|expanded|special|anniversary|remaster).*?[\])]','',s or '',flags=re.I))

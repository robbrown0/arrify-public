"""Choose the best indexed local copy without hiding genuine catalog tracks."""
import re
from .search import normalize

def artist_key(value):return re.sub(r'[^\w]','',value.casefold())
def attach_local(result,tracks,technical):
    local={}
    for t in tracks:
        identity=(artist_key(t.get('artist','')),normalize(t.get('title','')))
        local.setdefault(identity,[]).append(t)
    for item in result['items']:
        artist=result.get('requested_artist',item['artist']) if item['artist']==result['album']['artist'] else item['artist']
        names={artist_key(artist),artist_key(item['artist'])}
        if item['artist']==result['album']['artist']:names.update(artist_key(n) for n in result.get('artist_aliases',[]))
        candidates=[t for name in names for t in local.get((name,normalize(item['title'])),[])]
        # Metadata-backed lossless first, then bitrate. Stable ties avoid file-order changes.
        def rank(t):
            q=technical.get(t['id'],{})
            return (bool(q.get('lossless')),int(q.get('bitrate_kbps') or 0),-t['id'])
        chosen=max(candidates,key=rank) if candidates else None
        item['local_track_id']=chosen['id'] if chosen else None
        item['local_quality']=chosen.get('quality','') if chosen else ''
    return result

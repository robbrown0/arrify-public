"""Bounded live album reads using installed Lidarr's albumIds filter."""
import json
import logging
from .api import APIError

log=logging.getLogger(__name__)

def read_ids(lidarr,ids):
    ids=sorted(set(int(i) for i in ids if i))
    result=[]
    for start in range(0,len(ids),50):
        try:
            result.extend(lidarr.get('album',albumIds=ids[start:start+50]))
        except APIError as exc:
            if not str(exc).endswith('HTTP 500'):
                raise
            # Lidarr rejects the whole batch if even one saved ID was deleted.
            # A live inventory avoids retrying every ID individually. Only use
            # this recovery if the failed batch really contains missing IDs.
            recovered=[];missing=[]
            for album_id in ids[start:start+50]:
                try:recovered.append(lidarr.get('album/'+str(album_id)))
                except APIError as single:
                    if not str(single).endswith('HTTP 404'):raise
                    missing.append(album_id)
            if not missing:raise
            log.warning('Skipping deleted Lidarr album IDs: %s',missing)
            result.extend(recovered)
    return result

def preparation_albums(lidarr,store,groups):
    snapshot=[json.loads(r[0]) for r in store.db.execute("SELECT payload FROM inventory WHERE kind='album'")]
    if not snapshot:return lidarr.get('album')
    ids={a['id'] for a in snapshot if groups is None or a['foreignAlbumId'] in groups}
    ids.update(r[0] for r in store.db.execute('SELECT lidarr_id FROM requests WHERE lidarr_id IS NOT NULL'))
    return read_ids(lidarr,ids)

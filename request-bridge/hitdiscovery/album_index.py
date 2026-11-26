"""Bounded album reads; deleted IDs are reconciled without changing request history.

Only a singleton 404 proves deletion. Durable cache entries remember that an ID
was deleted, so expiry probes the release group rather than the dead ID again.
"""
import hashlib
import json
import logging
import time
from .api import APIError

log = logging.getLogger(__name__)
RECHECK_SECONDS = 3600
BATCH_SIZE = 50
SQL_BATCH_SIZE = 500  # Below SQLite's older 999 bound-variable limit.


def _status(exc):
    # Compatibility with older HTTP adapters during a coordinated rollout.
    return getattr(exc, 'status', None) or next(
        (code for code in (404, 500) if str(exc).endswith('HTTP '+str(code))), None)


def read_ids(lidarr, ids, store=None):
    store = store or getattr(getattr(lidarr, 'http', None), 'store', None)
    ids = sorted(set(int(i) for i in ids if i))
    if not ids:
        return []
    groups = {}
    if store:
        for start in range(0, len(ids), SQL_BATCH_SIZE):
            batch = ids[start:start+SQL_BATCH_SIZE]
            placeholders = ','.join('?' for _ in batch)
            # Keep id uncast so SQLite can use inventory's (kind,id) key.
            for row in store.db.execute(
                    "SELECT payload FROM inventory WHERE kind='album' AND id IN ("+placeholders+')',
                    tuple(str(i) for i in batch)):
                album = json.loads(row[0])
                if album.get('foreignAlbumId'):
                    groups[int(album['id'])] = album['foreignAlbumId']
            for row in store.db.execute(
                    'SELECT lidarr_id,rg FROM requests WHERE lidarr_id IN ('+placeholders+')', batch):
                groups[int(row[0])] = row[1]
    endpoint = getattr(getattr(lidarr, 'c', None), 'url', '').rstrip('/')
    prefix = 'album-tombstone-v1:'+hashlib.sha256(endpoint.encode()).hexdigest()+':'
    memory = getattr(lidarr, '_album_tombstones', None)
    if memory is None:
        memory = lidarr._album_tombstones = {}

    def saved(i):
        if store:
            row = store.db.execute('SELECT payload FROM cache WHERE key=?', (prefix+str(i),)).fetchone()
            return json.loads(row[0]) if row else None
        return memory.get(i)

    def remember(i, value):
        if store:
            store.cache(prefix+str(i), value)
        else:
            memory[i] = value

    def reconcile(i, previous=None, confirmed_missing=False):
        rg = groups.get(i) or (previous or {}).get('rg')
        if rg:
            matches = lidarr.get('album', foreignAlbumId=rg)
            if not isinstance(matches, list) or any(a.get('foreignAlbumId') != rg for a in matches) or len(matches)>1:
                raise APIError('Ambiguous Lidarr release-group reconciliation')
            album = matches[0] if matches else None
        elif confirmed_missing:
            album = None
        else:
            try:
                album = lidarr.get('album/'+str(i))
            except APIError as exc:
                if _status(exc) != 404:
                    raise
                album = None
        remember(i, {'rg': rg, 'id': album['id'] if album else None,
                     'checked': time.time()})
        return album

    result = {}
    pending = {}
    for i in ids:
        entry = saved(i)
        if entry and (not groups.get(i) or entry.get('rg') == groups[i]):
            if time.time()-entry['checked'] >= RECHECK_SECONDS:
                album = reconcile(i, entry)
                if album:
                    result[album['id']] = album
            elif entry['id']:
                pending.setdefault(entry['id'], []).append(i)
        else:
            pending.setdefault(i, []).append(i)

    numeric = sorted(pending)
    for start in range(0, len(numeric), BATCH_SIZE):
        batch = numeric[start:start+BATCH_SIZE]
        try:
            albums = lidarr.get('album', albumIds=batch)
        except APIError as exc:
            if _status(exc) != 500:
                raise
            albums = []
            # One failed batch, then at most one singleton and one RG lookup per
            # ID. An unrelated singleton 500 aborts immediately, never cached.
            for i in batch:
                try:
                    albums.append(lidarr.get('album/'+str(i)))
                except APIError as single:
                    if _status(single) != 404:
                        raise
                    for original in pending[i]:
                        album = reconcile(original, saved(original), confirmed_missing=True)
                        if album:
                            albums.append(album)
                    log.warning('Deleted Lidarr album ID %s reconciled or deferred', i)
        for album in albums:
            # Numeric IDs can be reused; never attach another RG to a request.
            originals = pending.get(album['id'], [])
            mismatched = [i for i in originals if groups.get(i) and groups[i] != album.get('foreignAlbumId')]
            if mismatched:
                for i in mismatched:
                    replacement = reconcile(i, saved(i))
                    if replacement:
                        result[replacement['id']] = replacement
            else:
                result[album['id']] = album
    return list(result.values())


def preparation_albums(lidarr, store, groups):
    snapshot = [json.loads(r[0]) for r in store.db.execute("SELECT payload FROM inventory WHERE kind='album'")]
    if not snapshot:
        return lidarr.get('album')
    ids = {a['id'] for a in snapshot if groups is None or a['foreignAlbumId'] in groups}
    ids.update(r[0] for r in store.db.execute('SELECT lidarr_id FROM requests WHERE lidarr_id IS NOT NULL'))
    return read_ids(lidarr, ids, store)

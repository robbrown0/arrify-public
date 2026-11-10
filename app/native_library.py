"""Native browsing adapters over the same library and per-account collections as web."""
import hashlib
import json
import random
from urllib.parse import parse_qs, urlparse
from .native_auth import NativeError
from .channels import PersonalStations
from .playlists import Playlists
from .native_moods import MOOD_GROUPS


def track(row):
    return {'id': row['id'], 'title': row.get('title') or '', 'artist': row.get('artist') or '',
            'album': row.get('album') or '', 'artwork': row.get('artwork')}


def entry(row, kind, section=''):
    return {'id': str(row.get('slug', row.get('id', ''))), 'kind': kind, 'name': row['name'],
            'subtitle': row.get('artist') or row.get('description') or '', 'section': section,
            'artwork': row.get('artwork'),
            'track_count': row.get('tracks', len(row.get('track_ids', [])))}


def overview(library, accounts, uid):
    collections = PersonalStations(accounts).collections(uid, library.collections())
    items = [entry(x, 'channels' if x.get('section') == 'channels' else 'collections', x.get('section', 'playlists'))
             for x in collections if x['slug'] != 'jazz-for-sleep']
    for p in Playlists(accounts, library).rows(uid):
        item = entry(p, 'playlists', 'personal')
        if p['track_ids']: item['artwork'] = '/api/v1/artwork/' + str(p['track_ids'][0])
        items.append(item)
    available = {x['id'] for x in items if x['track_count'] > 0 and x['kind'] == 'collections'}
    moods = [{**g, 'slugs': [s for s in g['slugs'] if s in available]} for g in MOOD_GROUPS]
    with accounts.lock:
        recent = accounts.db.execute('SELECT track_id,max(played_at) played FROM history WHERE user_id=? GROUP BY track_id ORDER BY played DESC LIMIT 12', (uid,)).fetchall()
    with library.index_lock:
        album_ids = list(dict.fromkeys(library.tracks[r[0]].get('album_id') for r in recent if r[0] in library.tracks))
        albums = [library.albums[i] for i in album_ids if i in library.albums][:6]
        if not albums:
            albums = [library.albums[x['id']] for x in library.suggestions() if x['id'] in library.albums]
        home_albums = [entry(x, 'albums') for x in albums]
        return {'home_albums': home_albums, 'items': items, 'moods': [g for g in moods if g['slugs']],
                'tracks': len(library.tracks), 'albums': len(library.albums), 'artists': len(library.artists)}


def selection(library, accounts, uid, kind, key):
    if kind == 'favorites' and key == 'liked':
        with accounts.lock:
            ids = [r[0] for r in accounts.db.execute('SELECT track_id FROM favorites WHERE user_id=? ORDER BY rowid DESC', (uid,))]
        with library.index_lock:
            ids = [i for i in ids if i in library.tracks]
        return {'id': 'liked', 'kind': 'favorites', 'name': 'Liked songs', 'subtitle': 'Your saved songs',
                'section': 'personal', 'artwork': '/api/v1/artwork/' + str(ids[0]) if ids else None, 'track_count': len(ids)}, ids
    if kind in ('albums', 'artists'):
        with library.index_lock:
            row = getattr(library, kind).get(key)
            if not row: raise NativeError(404, 'not_found', 'This item is no longer available.')
            ids = list(row['track_ids'])
            if kind == 'albums':
                def order(i):
                    t = library.tracks[i]
                    try: return (int(str(t.get('disc_number') or 1).split('/')[0]), int(str(t.get('track_number') or 0).split('/')[0]))
                    except ValueError: return (0, 0)
                ids.sort(key=order)
            return entry(row, kind), ids
    if kind in ('collections', 'channels'):
        row = next((x for x in library.collections() if x['slug'] == key), None)
        if not row or (kind == 'channels') != (row.get('section') == 'channels'):
            raise NativeError(404, 'not_found', 'This collection is no longer available.')
        return entry(row, kind, row.get('section', 'playlists')), library.collection_ids(key)
    if kind == 'playlists':
        try: row = Playlists(accounts, library).get(uid, int(key))
        except (ValueError, LookupError): raise NativeError(404, 'not_found', 'Playlist not found.') from None
        return entry(row, kind, 'personal'), row['track_ids']
    raise NativeError(400, 'invalid_request', 'Choose a supported library item.')


def detail(library, accounts, uid, path):
    q = parse_qs(urlparse(path).query, keep_blank_values=True)
    if any(len(v) != 1 for v in q.values()): raise NativeError(400, 'invalid_request', 'Duplicate query parameters.')
    try: offset, limit = int(q.get('offset', ['0'])[0]), int(q.get('limit', ['50'])[0])
    except ValueError: raise NativeError(400, 'invalid_request', 'Invalid pagination.') from None
    if not 0 <= offset <= 100000 or not 1 <= limit <= 100: raise NativeError(400, 'invalid_request', 'Invalid pagination.')
    kind, key = q.get('kind', [''])[0], q.get('id', [''])[0]
    if len(key) > 200: raise NativeError(400, 'invalid_request', 'Invalid library item.')
    item, ids = selection(library, accounts, uid, kind, key)
    with library.index_lock:
        ids = [i for i in ids if i in library.tracks]
        revision = hashlib.sha256(json.dumps([kind, key, library.search.revision, ids]).encode()).hexdigest()[:24]
        supplied = q.get('revision', [None])[0]
        if (offset and not supplied) or (supplied is not None and supplied != revision):
            raise NativeError(409, 'catalog_changed', 'This list changed. Refresh to continue.')
        items = [track(library.tracks[i]) for i in ids[offset:offset + limit]]
    item['track_count'] = len(ids)
    next_offset = offset + len(items)
    return {'item': item, 'items': items, 'revision': revision, 'total': len(ids),
            'next_offset': next_offset if next_offset < len(ids) else None}


def expand_playback(library, accounts, uid, data):
    source = data.get('source')
    if not isinstance(source, dict) or set(source) != {'kind', 'id'} or not all(isinstance(v, str) for v in source.values()):
        raise NativeError(400, 'invalid_request', 'Choose a library playback source.')
    if any(k in data for k in ('ids', 'channel', 'collection', 'browse', 'radio')) or len(source['id']) > 200:
        raise NativeError(400, 'invalid_request', 'Choose one playback source.')
    item, ids = selection(library, accounts, uid, source['kind'], source['id'])
    if not ids: raise NativeError(409, 'empty_collection', 'No local songs are available yet.')
    if len(ids) > 50000: raise NativeError(400, 'invalid_request', 'This selection is too large.')
    if 'track_id' in data and (type(data['track_id']) is not int or data['track_id'] not in ids):
        raise NativeError(409, 'catalog_changed', 'That song is no longer in this list.')
    if type(data.get('shuffle', False)) is not bool: raise NativeError(400, 'invalid_request', 'Invalid shuffle setting.')
    command = {k: v for k, v in data.items() if k not in ('source', 'shuffle')}
    command['playback_context'] = {'kind': source['kind'], 'name': str(item.get('name') or item.get('title') or 'Library')[:200]}
    if source['kind'] == 'channels':
        command['channel'] = source['id']
    else:
        if data.get('shuffle'): random.SystemRandom().shuffle(ids)
        command['ids'] = ids
    return command


def favorites(library, accounts, uid, data=None):
    if data is not None:
        if set(data) != {'track_id', 'liked'} or type(data['track_id']) is not int or type(data['liked']) is not bool:
            raise NativeError(400, 'invalid_request', 'Choose a song and whether to save it.')
        tid = data['track_id']
        with library.index_lock:
            if tid not in library.tracks:
                raise NativeError(404, 'not_found', 'Song no longer available.')
        with accounts.lock, accounts.db:
            if data['liked']:
                accounts.db.execute('INSERT OR IGNORE INTO favorites VALUES (?,?)', (uid, tid))
            else:
                accounts.db.execute('DELETE FROM favorites WHERE user_id=? AND track_id=?', (uid, tid))
    _, ids = selection(library, accounts, uid, 'favorites', 'liked')
    return {'track_ids': ids}


def edit_playlist(library, accounts, uid, data):
    if not isinstance(data, dict) or set(data) - {'id', 'name', 'visibility', 'add_track_id'}:
        raise NativeError(400, 'invalid_request', 'Invalid playlist fields.')
    if 'id' in data and (type(data['id']) is not int or data['id'] <= 0):
        raise NativeError(400, 'invalid_request', 'Choose a playlist.')
    if ('id' not in data or 'name' in data) and (not isinstance(data.get('name'), str) or not 1 <= len(data['name'].strip()) <= 120):
        raise NativeError(400, 'invalid_request', 'Use a playlist name between 1 and 120 characters.')
    if 'visibility' in data and data['visibility'] not in ('private', 'shared'):
        raise NativeError(400, 'invalid_request', 'Choose private or shared.')
    if 'add_track_id' in data:
        if type(data['add_track_id']) is not int or data['add_track_id'] not in library.tracks:
            raise NativeError(404, 'not_found', 'Song no longer available.')
    playlists = Playlists(accounts, library)
    try:
        result = playlists.edit(uid, data)
        row = playlists.get(uid, result['id'])
    except LookupError:
        raise NativeError(404, 'not_found', 'Playlist not found.') from None
    item = entry(row, 'playlists', 'personal')
    if row['track_ids']: item['artwork'] = '/api/v1/artwork/' + str(row['track_ids'][0])
    return item

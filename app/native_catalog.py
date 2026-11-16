"""Bounded native catalog pages from one immutable search snapshot."""
import hashlib
import bisect
from urllib.parse import parse_qs, urlparse
from .native_auth import NativeError
from .native_library import track, enrich_popularity


def ordered_ids(index, kind, query='', sort='title', artist='', liked=None):
    if kind not in ('tracks', 'albums', 'artists') or sort not in (None, 'title', 'artist', 'recent', 'relevance'):
        raise NativeError(400, 'invalid_request', 'Invalid library ordering.')
    catalog = getattr(index, kind)
    ids = catalog.browse_orders['title' if sort in (None, 'relevance') else sort]
    if query.strip():
        found = catalog.search(query, 0, len(catalog.items))['items']
        if sort in (None, 'relevance'):
            ids = tuple(x['id'] for x in found)  # Preserve legacy relevance order.
        else:
            matches = {x['id'] for x in found}
            ids = tuple(i for i in ids if i in matches)
    if artist:
        row = index.artists.items.get(artist)
        members = set(row.get('track_ids', ())) if row else set()
        ids = tuple(i for i in ids if members.intersection(catalog.items[i].get('track_ids', ())))
    if liked is not None:
        ids = tuple(i for i in ids if i in liked)
    return ids


def liked_ids(accounts, uid):
    if accounts is None: return set()
    with accounts.lock:
        return {r[0] for r in accounts.db.execute('SELECT track_id FROM favorites WHERE user_id=?', (uid,))}


def page(library, request_path: str, accounts=None, uid=None) -> dict:
    q = parse_qs(urlparse(request_path).query, keep_blank_values=True)
    if any(len(v) != 1 for v in q.values()):
        raise NativeError(400, 'invalid_request', 'Duplicate query parameters.')
    kind = q.get('kind', ['tracks'])[0]
    query = q.get('q', [''])[0]
    try:
        offset, limit = int(q.get('offset', ['0'])[0]), int(q.get('limit', ['50'])[0])
    except ValueError:
        raise NativeError(400, 'invalid_request', 'Invalid pagination.') from None
    if kind not in ('tracks', 'albums', 'artists') or len(query) > 200 or not 0 <= offset <= 100000 or not 1 <= limit <= 100:
        raise NativeError(400, 'invalid_request', 'Invalid catalog query.')
    with library.index_lock:
        index = library.search
    sort = q.get('sort', [None])[0]
    artist = q.get('artist', [''])[0]
    liked = q.get('liked', ['false'])[0]
    if liked not in ('true', 'false') or (liked == 'true' and kind != 'tracks') or len(artist) > 100:
        raise NativeError(400, 'invalid_request', 'Invalid library filter.')
    ids = ordered_ids(index, kind, query, sort, artist, liked_ids(accounts, uid) if liked == 'true' else None)
    page_revision = index.revision
    if liked == 'true':
        page_revision = hashlib.sha256((index.revision + repr(ids)).encode()).hexdigest()[:24]
    revision = q.get('revision', [None])[0]
    if (offset and not revision) or (revision is not None and revision != page_revision):
        raise NativeError(409, 'catalog_changed', 'Restart paging from the first page.')
    catalog = getattr(index, kind)
    around = q.get('around', [None])[0]
    letter = q.get('letter', [None])[0]
    if around is not None:
        try: target = int(around) if kind == 'tracks' else around
        except ValueError: raise NativeError(400, 'invalid_request', 'Invalid song.') from None
        if target in ids: offset = max(0, ids.index(target) - limit // 2)
    if letter is not None:
        if len(letter) != 1 or letter not in '#ABCDEFGHIJKLMNOPQRSTUVWXYZ' or sort not in (None, 'title'):
            raise NativeError(400, 'invalid_request', 'Choose an alphabetical section.')
        names = [(catalog.items[i].get('title') or catalog.items[i].get('name') or '').casefold() for i in ids]
        offset = 0 if letter == '#' else min(bisect.bisect_left(names, letter.lower()), max(0, len(ids) - 1))
    result = {'items': [catalog.items[i] for i in ids[offset:offset + limit]], 'total': len(ids)}
    items = []
    for row in result['items']:
        if kind == 'tracks':
            items.append(track(row))
        else:
            items.append({'id': row['id'], 'name': row.get('name', ''),
                          'artist': row.get('artist', ''), 'track_count': len(row.get('track_ids', [])), 'kind': kind, 'subtitle': row.get('artist') or '', 'section': '', 'artwork': row.get('artwork')})
    pending = False
    if kind == 'tracks' and query.strip():
        items, pending = enrich_popularity(library, items)
    next_offset = offset + len(items)
    return {'kind': kind, 'revision': page_revision, 'offset': offset, 'previous_offset': max(0, offset - limit) if offset else None, 'items': items,
            'popularity_pending': pending, 'total': result['total'], 'next_offset': next_offset if next_offset < result['total'] else None}

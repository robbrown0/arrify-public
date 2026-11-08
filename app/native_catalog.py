"""Bounded native catalog pages from one immutable search snapshot."""
from urllib.parse import parse_qs, urlparse
from .native_auth import NativeError


def page(library, request_path: str) -> dict:
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
    revision = q.get('revision', [None])[0]
    if (offset and not revision) or (revision is not None and revision != index.revision):
        raise NativeError(409, 'catalog_changed', 'Restart paging from the first page.')
    catalog = getattr(index, kind)
    if query.strip():
        result = catalog.search(query, offset, limit)
    else:
        values = sorted(catalog.items.values(), key=lambda x: ((x.get('title') or x.get('name') or '').casefold(), str(x['id'])))
        result = {'items': values[offset:offset + limit], 'total': len(values)}
    items = []
    for row in result['items']:
        if kind == 'tracks':
            items.append({'id': row['id'], 'title': row.get('title', ''),
                          'artist': row.get('artist', ''), 'album': row.get('album', ''),
                          'availability': 'local', 'artwork': row.get('artwork')})
        else:
            items.append({'id': row['id'], 'name': row.get('name', ''),
                          'artist': row.get('artist', ''), 'track_count': len(row.get('track_ids', [])), 'kind': kind, 'subtitle': row.get('artist') or '', 'section': '', 'artwork': row.get('artwork')})
    next_offset = offset + len(items)
    return {'kind': kind, 'revision': index.revision, 'items': items,
            'total': result['total'], 'next_offset': next_offset if next_offset < result['total'] else None}

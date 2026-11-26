import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from hitdiscovery.album_index import read_ids, preparation_albums
from hitdiscovery.api import APIError, HTTP, Lidarr
from hitdiscovery.store import Store


def album(i, rg):
    return {'id': i, 'foreignAlbumId': rg}


class FakeLidarr:
    def __init__(self, store=None):
        self.http = SimpleNamespace(store=store)
        self.c = SimpleNamespace(url='http://lidarr')
        self.albums = {2: album(2, 'valid')}
        self.calls = []
        self.failure = None

    def get(self, path, **params):
        self.calls.append((path, params))
        if self.failure:
            raise APIError('unrelated failure', status=self.failure)
        if path == 'album':
            if 'foreignAlbumId' in params:
                return [a for a in self.albums.values() if a['foreignAlbumId'] == params['foreignAlbumId']]
            if any(i not in self.albums for i in params['albumIds']):
                raise APIError('batch failure', status=500)
            return [self.albums[i] for i in params['albumIds']]
        i = int(path.split('/')[1])
        if i not in self.albums:
            raise APIError('missing', status=404)
        return self.albums[i]


class AlbumIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name)/'test.db')
        self.addCleanup(self.store.db.close)
        self.store.db.execute("INSERT INTO requests VALUES('deleted','reserved-at','week','waiting-for-release',1,'history')")
        self.store.db.commit()
        self.original = tuple(self.store.db.execute('SELECT * FROM requests').fetchone())
        self.l = FakeLidarr(self.store)

    def test_only_relevant_inventory_payloads_and_requests_are_returned(self):
        self.store.inventory('album', [album(2, 'valid')])
        # Invalid JSON must never reach the parser, nor even the row factory.
        self.store.db.execute("INSERT INTO inventory VALUES('album','99999','','invalid JSON','')")
        self.store.db.execute("INSERT INTO inventory VALUES('artist','2','','invalid artist JSON','')")
        self.store.db.commit()
        rows = []
        def record_row(cursor, values):
            rows.append((tuple(c[0] for c in cursor.description), values))
            return sqlite3.Row(cursor, values)
        self.store.db.row_factory = record_row
        with patch('hitdiscovery.album_index.json.loads', wraps=json.loads) as parse:
            self.assertEqual(read_ids(self.l, [2]), [album(2, 'valid')])
        self.assertEqual(parse.call_count, 1)
        self.assertEqual(rows, [(('payload',), (json.dumps(album(2, 'valid'), sort_keys=True),))])
        plan = self.store.db.execute(
            "EXPLAIN QUERY PLAN SELECT payload FROM inventory WHERE kind='album' AND id IN (?)", ('2',))
        detail = ' '.join(row[3] for row in plan)
        self.assertIn('SEARCH inventory', detail)
        self.assertIn('kind=? AND id=?', detail)

    def test_more_than_999_ids_with_legacy_sqlite_variable_limit(self):
        ids = list(range(2, 1207))
        self.l.albums = {i: album(i, 'rg-'+str(i)) for i in ids}
        self.store.inventory('album', list(self.l.albums.values()))
        # Exercise request precedence in each SQL batch, including its last one.
        for i in (2, 502, 1206):
            self.store.db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?)',
                                  ('request-'+str(i), '', '', 'prepared', i, ''))
            self.l.albums[i] = album(i, 'request-'+str(i))
        self.store.db.commit()
        self.store.db.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
        queries = []
        self.store.db.set_trace_callback(queries.append)
        result = read_ids(self.l, ids)
        self.assertEqual({a['id'] for a in result}, set(ids))
        self.assertEqual(len(self.l.calls), 25)  # Only healthy API batches.
        for table in ('inventory', 'requests'):
            selected = [q for q in queries if 'FROM '+table+' WHERE' in q]
            self.assertEqual(len(selected), 3)
            self.assertTrue(all(' IN (' in q for q in selected))

    def test_mixed_stale_and_valid_persist_across_clients_without_history_change(self):
        self.assertEqual(read_ids(self.l, [1, 2, 1]), [album(2, 'valid')])
        self.assertEqual(len(self.l.calls), 4)  # batch, singleton404, RG, valid singleton
        other = FakeLidarr(self.store)
        self.assertEqual(read_ids(other, [1, 2]), [album(2, 'valid')])
        self.assertEqual(other.calls, [('album', {'albumIds': [2]})])
        self.assertEqual(tuple(self.store.db.execute('SELECT * FROM requests').fetchone()), self.original)

    def test_reconcile_replacement_and_expiry_never_probe_dead_numeric_id(self):
        self.l.albums[3] = album(3, 'deleted')
        self.assertEqual({a['id'] for a in read_ids(self.l, [1, 2])}, {2, 3})
        self.l.calls.clear()
        self.assertEqual({a['id'] for a in read_ids(self.l, [1, 2])}, {2, 3})
        self.assertEqual(self.l.calls, [('album', {'albumIds': [2, 3]})])
        with patch('hitdiscovery.album_index.time.time', return_value=10**11):
            self.l.calls.clear()
            read_ids(self.l, [1])
            self.assertEqual(self.l.calls, [('album', {'foreignAlbumId': 'deleted'})])

    def test_deleted_group_reappears_after_expiry(self):
        read_ids(self.l, [1])
        self.l.albums[4] = album(4, 'deleted')
        self.assertEqual(read_ids(self.l, [1]), [])
        with patch('hitdiscovery.album_index.time.time', return_value=10**11):
            self.assertEqual(read_ids(self.l, [1]), [album(4, 'deleted')])

    def test_unrelated_500_aborts_after_two_calls_without_tombstone(self):
        self.l.failure = 500
        with self.assertRaises(APIError):
            read_ids(self.l, range(1, 51))
        self.assertEqual(len(self.l.calls), 2)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM cache').fetchone()[0], 0)

    def test_batch_404_is_not_deletion(self):
        self.l.failure = 404
        with self.assertRaises(APIError):
            read_ids(self.l, [1, 2])
        self.assertEqual(len(self.l.calls), 1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM cache').fetchone()[0], 0)

    def test_unknown_missing_has_only_one_404_and_expires(self):
        l = FakeLidarr()
        self.assertEqual(read_ids(l, [9]), [])
        self.assertEqual(len(l.calls), 2)
        read_ids(l, [9])
        self.assertEqual(len(l.calls), 2)
        l.albums[9] = album(9, 'new')
        with patch('hitdiscovery.album_index.time.time', return_value=10**11):
            self.assertEqual(read_ids(l, [9]), [album(9, 'new')])

    def test_all_missing_batch_call_bound(self):
        self.l.albums.clear()
        read_ids(self.l, range(1, 51))
        self.assertEqual(len(self.l.calls), 52)  # one RG known, 50 singletons, one batch
        self.l.calls.clear()
        self.assertEqual(read_ids(self.l, range(1, 51)), [])
        self.assertEqual(self.l.calls, [])

    def test_all_known_missing_worst_case_bound(self):
        self.store.inventory('album', [album(i, 'rg-'+str(i)) for i in range(2, 51)])
        self.l.albums.clear()
        self.assertEqual(read_ids(self.l, range(1, 51)), [])
        self.assertEqual(len(self.l.calls), 101)

    def test_replacement_deleted_again_reconciles_original_group(self):
        self.l.albums[3] = album(3, 'deleted')
        read_ids(self.l, [1])
        del self.l.albums[3]
        self.l.albums[4] = album(4, 'deleted')
        self.assertEqual(read_ids(self.l, [1]), [album(4, 'deleted')])

    def test_endpoint_namespaces_do_not_share_tombstones(self):
        read_ids(self.l, [1])
        other = FakeLidarr(self.store)
        other.c.url = 'http://other-lidarr'
        other.albums[1] = album(1, 'deleted')
        self.assertEqual(read_ids(other, [1]), [album(1, 'deleted')])

    def test_valid_batch_size_and_empty_input(self):
        self.l.albums = {i: album(i, str(i)) for i in range(2, 123)}
        self.assertEqual(len(read_ids(self.l, range(2, 123))), 121)
        self.assertEqual([len(p['albumIds']) for _, p in self.l.calls], [50, 50, 21])
        self.l.calls.clear()
        self.assertEqual(read_ids(self.l, []), [])
        self.assertEqual(self.l.calls, [])

    def test_numeric_id_reuse_does_not_attach_wrong_group(self):
        self.l.albums[1] = album(1, 'different')
        self.l.albums[3] = album(3, 'deleted')
        self.assertEqual(read_ids(self.l, [1]), [album(3, 'deleted')])

    def test_inventory_stale_id_uses_release_group(self):
        self.store.inventory('album', [album(8, 'inventory')])
        self.l.albums[10] = album(10, 'inventory')
        self.assertEqual(preparation_albums(self.l, self.store, {'inventory'}), [album(10, 'inventory')])

    def test_reconciliation_failure_does_not_cache_deletion(self):
        original = self.l.get
        def get(path, **params):
            if 'foreignAlbumId' in params:
                raise APIError('outage', status=500)
            return original(path, **params)
        self.l.get = get
        with self.assertRaises(APIError):
            read_ids(self.l, [1])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM cache').fetchone()[0], 0)


class HTTPBoundsTests(unittest.TestCase):
    def test_deployed_metadata_adapter_is_preserved(self):
        from unittest.mock import Mock
        adapter = SimpleNamespace(fetch_json=Mock(return_value={'ok': True}))
        url = 'https://musicbrainz.org/ws/2/recording/id'
        with patch.dict('sys.modules', {'hitdiscovery.metadata_transport': adapter}), patch('hitdiscovery.api.urlopen') as network:
            self.assertEqual(HTTP().json(url), {'ok': True})
        adapter.fetch_json.assert_called_once_with(url, headers=None, store=None, cache_ttl=0)
        network.assert_not_called()


    def test_album_500_not_retried_at_transport(self):
        error = HTTPError('http://lidarr/api/v1/album', 500, 'error', {}, io.BytesIO())
        with patch('hitdiscovery.api.urlopen', side_effect=error) as send, patch('hitdiscovery.api.time.sleep'):
            with self.assertRaises(APIError) as raised:
                HTTP().json('http://lidarr/api/v1/album', retry_500=False)
        self.assertEqual(raised.exception.status, 500)
        self.assertEqual(send.call_count, 1)

    def test_other_requests_keep_outage_retries(self):
        error = HTTPError('http://lidarr/api/v1/queue', 503, 'error', {}, io.BytesIO())
        with patch('hitdiscovery.api.urlopen', side_effect=error) as send, patch('hitdiscovery.api.time.sleep'):
            with self.assertRaises(APIError):
                HTTP().json('http://lidarr/api/v1/queue')
        self.assertEqual(send.call_count, 4)

    def test_guard_and_album_retry_policy(self):
        l = Lidarr.__new__(Lidarr)
        l.c = SimpleNamespace(url='http://lidarr'); l.headers = {}
        from unittest.mock import Mock
        l.http = Mock()
        for path in ('track', 'trackfile'):
            with self.assertRaises(APIError):
                l.get(path)
        l.http.json.assert_not_called()
        l.get('track', albumId=2)
        self.assertTrue(l.http.json.call_args.kwargs['retry_500'])
        l.get('album', albumIds=[1, 2])
        self.assertFalse(l.http.json.call_args.kwargs['retry_500'])

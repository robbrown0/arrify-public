"""Catalog extraction preserves stored identities and bounded media access."""
import sqlite3
import tempfile
import unittest
import wave
import threading
import json
from unittest.mock import patch
from pathlib import Path

from app.catalog_engine import Catalog, _metadata, _public_row
from app.errors import CatalogError


class CatalogEngineTests(unittest.TestCase):
    def test_disc_directories_keep_album_and_artist_with_disc_order(self):
        for folder, number in [('CD 01', '1'), ('cd02', '2'),
                               ('Digital Media 01', '1'), ('12 Vinyl 02', '2'),
                               ('7 Vinyl 01', '1'), ('Disc 2 of 3', '2')]:
            with self.subTest(folder=folder):
                path = 'Artist/Album (2008)/' + folder + '/01 - Song.flac'
                self.assertEqual(_metadata(path), ('01 - Song', 'Artist', 'Album (2008)'))
                row = _public_row({'relative_path': path, 'disc_number': ''})
                self.assertEqual(row['disc_number'], number)
                self.assertNotIn('relative_path', row)
                self.assertEqual(_public_row({'relative_path': path, 'disc_number': '3/3'})['disc_number'], '3/3')

    def test_album_titles_and_shallow_disc_names_are_preserved(self):
        for path, expected in [
            ('Artist/Volume 1/01.flac', ('01', 'Artist', 'Volume 1')),
            ('Artist/CD 01/01.flac', ('01', 'Artist', 'CD 01')),
            ('Artist/Discovery/01.flac', ('01', 'Artist', 'Discovery')),
            ('ITUNES/Music/Various Artists/cd4/13 Song.m4a', ('13 Song', 'Various Artists', 'cd4')),
        ]:
            self.assertEqual(_metadata(path), expected)

    def test_multidisc_rescan_preserves_playlist_track_ids(self):
        audio = self.music / 'Artist' / 'Album' / 'CD 02' / '01 - Song.wav'
        audio.parent.mkdir(parents=True)
        audio.write_bytes(self.audio.read_bytes())
        catalog = self.catalog()
        catalog.scan()
        row = catalog.db.execute("SELECT * FROM tracks WHERE relative_path LIKE '%CD 02%'").fetchone()
        self.assertEqual((row['artist'], row['album']), ('Artist', 'Album'))
        catalog.db.execute("INSERT INTO playlists VALUES('saved','Saved','',0)")
        catalog.db.execute("INSERT INTO playlist_tracks VALUES('saved',?,0)", (row['id'],))
        with patch.object(catalog, '_refresh_playlists'):
            catalog.scan()
        self.assertEqual(catalog.db.execute("SELECT id FROM tracks WHERE relative_path=?", (row['relative_path'],)).fetchone()[0], row['id'])
        self.assertEqual(catalog.db.execute("SELECT track_id FROM playlist_tracks WHERE playlist_slug='saved'").fetchone()[0], row['id'])

    def test_disc_repair_preserves_existing_curated_and_station_manifest_matches(self):
        audio = self.music / 'Artist' / 'Album' / 'CD 02' / '01 - Song.wav'
        audio.parent.mkdir(parents=True)
        audio.write_bytes(self.audio.read_bytes())
        manifest = self.root / 'playlists.json'
        manifest.write_text(json.dumps([
            {'slug': 'old-curated', 'name': 'Old curated', 'description': '',
             'tracks': [{'artists': ['Album'], 'title': 'Song', 'album': 'CD 02'}]},
            {'slug': 'old-station', 'name': 'Old station', 'description': '', 'section': 'channels',
             'tracks': [{'artists': ['Album'], 'title': '01 - Song', 'album': 'CD 02'}]},
            {'slug': 'corrected', 'name': 'Corrected', 'description': '',
             'tracks': [{'artists': ['Artist'], 'title': 'Song', 'album': 'Album'}]},
        ]))
        catalog = Catalog(self.db_path, {'local': str(self.music)}, playlists_file=str(manifest))
        self.addCleanup(catalog.db.close)
        catalog.scan()
        row = catalog.db.execute("SELECT * FROM tracks WHERE relative_path LIKE '%CD 02%'").fetchone()
        matches = list(catalog.db.execute('SELECT playlist_slug,track_id FROM playlist_tracks ORDER BY playlist_slug'))
        self.assertEqual([(r[0], r[1]) for r in matches], [('corrected', row['id']), ('old-curated', row['id']), ('old-station', row['id'])])
        self.assertEqual((row['artist'], row['album']), ('Artist', 'Album'))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.music = self.root / 'music'
        self.music.mkdir()
        self.audio = self.music / 'Track.wav'
        with wave.open(str(self.audio), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            stream.writeframes(b'\0\0' * 8000)
        self.db_path = str(self.root / 'catalog.db')

    def catalog(self):
        catalog = Catalog(self.db_path, {'local': str(self.music)})
        self.addCleanup(catalog.db.close)
        return catalog

    def test_scan_reopen_preserves_track_identity_and_legacy_tables(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute('CREATE TABLE playback_state(account_id INTEGER PRIMARY KEY, state_json TEXT)')
            db.execute("INSERT INTO playback_state VALUES(9, '{}')")
        catalog = self.catalog()
        self.assertEqual(catalog.scan()['tracks'], 1)
        track_id = catalog.db.execute('SELECT id FROM tracks').fetchone()[0]
        reopened = self.catalog()
        self.assertEqual(reopened.scan()['tracks'], 1)
        self.assertEqual(reopened.db.execute('SELECT id FROM tracks').fetchone()[0], track_id)
        self.assertEqual(reopened.db.execute('SELECT state_json FROM playback_state WHERE account_id=9').fetchone()[0], '{}')
        self.assertEqual(reopened._track_file(track_id)[1], str(self.audio))

    def test_new_database_contains_no_legacy_player_tables(self):
        catalog = self.catalog()
        tables = {r[0] for r in catalog.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertFalse(tables & {'playback_state', 'player_settings', 'track_spectra'})

    def test_reopen_saved_catalog_does_not_resolve_network_mounts(self):
        catalog=self.catalog();catalog.scan()
        with patch('app.catalog_engine.os.path.realpath',side_effect=AssertionError('network path lookup during startup')):
            reopened=self.catalog()
        self.assertEqual(reopened.db.execute('SELECT count(*) FROM tracks WHERE available=1').fetchone()[0],1)

    def test_artwork_falls_back_and_confirmed_artwork_takes_priority(self):
        catalog = self.catalog()
        catalog.scan()
        track_id = catalog.db.execute('SELECT id FROM tracks').fetchone()[0]
        mime, data, source = catalog.artwork(track_id)
        self.assertEqual((mime, source), ('image/svg+xml', 'fallback'))
        self.assertIn(b'<svg', data)
        catalog.db.execute("UPDATE tracks SET artist='Artist', album='Album' WHERE id=?", (track_id,))
        key = catalog._artwork_key({'artist': 'Artist', 'album': 'Album'})
        catalog.db.execute('INSERT INTO album_artwork VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (key, 'Artist', 'Album', 'reviewed', '', 'image/png', b'confirmed', 'hash', 1, 'admin'))
        self.assertEqual(catalog.artwork(track_id), ('image/png', b'confirmed', 'confirmed'))

    def test_changed_file_requires_rescan(self):
        catalog = self.catalog()
        catalog.scan()
        track_id = catalog.db.execute('SELECT id FROM tracks').fetchone()[0]
        self.audio.write_bytes(b'changed')
        with self.assertRaisesRegex(CatalogError, 'catalog refresh required'):
            catalog._track_file(track_id)

    def test_interrupted_directory_scan_keeps_the_last_catalog_snapshot(self):
        catalog = self.catalog(); catalog.scan()
        def failed_walk(root, **options):
            options['onerror'](OSError('NAS disconnected'))
            yield
        with patch('app.catalog_engine.os.walk', side_effect=failed_walk):
            result = catalog.scan()
        self.assertTrue(result['errors'])
        self.assertEqual(catalog.db.execute('SELECT count(*) FROM tracks WHERE available=1').fetchone()[0],1)

    def test_partial_stat_failure_does_not_remove_a_source_catalog(self):
        catalog = self.catalog(); catalog.scan()
        original = __import__('os').stat
        def failed_stat(path, *args, **kwargs):
            if str(path) == str(self.audio): raise OSError('NAS disconnected')
            return original(path, *args, **kwargs)
        with patch('app.catalog_engine.os.stat',side_effect=failed_stat): result = catalog.scan()
        self.assertTrue(result['errors'])
        self.assertEqual(catalog.db.execute('SELECT count(*) FROM tracks WHERE available=1').fetchone()[0],1)

    def test_confirmed_artwork_is_read_without_touching_a_nas_file(self):
        catalog = self.catalog(); catalog.scan()
        catalog.db.execute("UPDATE tracks SET artist='Artist',album='Album'")
        key=catalog._artwork_key({'artist':'Artist','album':'Album'})
        catalog.db.execute('INSERT INTO album_artwork VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (key,'Artist','Album','reviewed','','image/png',b'confirmed','hash',1,'admin'))
        track=catalog.db.execute('SELECT id FROM tracks').fetchone()[0]
        with patch.object(catalog,'_track_file',side_effect=AssertionError('must not touch NAS')):
            self.assertEqual(catalog.artwork(track),('image/png',b'confirmed','confirmed'))
            # A saturated NAS queue must not hide a cover already in the database.
            from app.artwork_reads import ArtworkReads
            reader=ArtworkReads(workers=1,capacity=1,timeout=0.025)
            catalog._artwork_reads={'local':reader}
            gate=threading.Event()
            try:
                reader.read('stalled',lambda:gate.wait(3),None)
                self.assertEqual(len(reader.pending),1)
                self.assertEqual(catalog.bounded_artwork(track),('image/png',b'confirmed','confirmed'))
            finally:
                gate.set();reader.queue.join()

    def test_stalled_source_does_not_block_other_source_artwork(self):
        catalog=self.catalog();catalog.scan()
        first=catalog.db.execute('SELECT id FROM tracks').fetchone()[0]
        catalog.db.execute("INSERT INTO tracks(source,relative_path,title,artist,album,extension,mime_type,size,modified_ns,available) SELECT 'healthy',relative_path,title,artist,album,extension,mime_type,size,modified_ns,available FROM tracks WHERE id=?",(first,))
        second=catalog.db.execute("SELECT id FROM tracks WHERE source='healthy'").fetchone()[0]
        gate=threading.Event()
        def artwork(track):
            if track==first:gate.wait(3)
            return ('image/png',b'cover','embedded')
        try:
            with patch.object(catalog,'artwork',side_effect=artwork):
                self.assertEqual(catalog.bounded_artwork(first)[2],'deferred')
                self.assertEqual(catalog.bounded_artwork(second),('image/png',b'cover','embedded'))
        finally:
            gate.set()
            for reader in catalog._artwork_reads.values():reader.queue.join()

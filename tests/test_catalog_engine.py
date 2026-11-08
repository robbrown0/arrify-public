"""Catalog extraction preserves stored identities and bounded media access."""
import sqlite3
import tempfile
import unittest
import wave
from pathlib import Path

from app.catalog_engine import Catalog
from app.errors import CatalogError


class CatalogEngineTests(unittest.TestCase):
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

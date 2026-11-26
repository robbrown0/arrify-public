import hashlib
from pathlib import Path
import tempfile
import unittest

from tools.audiobook_publication import publish_chapters


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source_id = 'a' * 64
        self.cover = self.root / 'original.jpg'; self.cover.write_bytes(b'reviewed-cover')

    def entry(self, track, **change):
        path = self.root / f'original-{track}.m4b'; path.write_bytes(f'reviewed-audio-{track}'.encode())
        return {'track': track, 'track_total': 4, 'source_upload_id': self.source_id,
                'path': str(path), 'cover': str(self.cover), 'title': f'Chapter {track}', **change}

    def test_one_book_preserves_source_order_audio_and_stable_files_when_extended(self):
        first, second, third = self.entry(1), self.entry(2), self.entry(3)
        original = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir() if p.is_file()}
        result = publish_chapters(self.source_id, [second, first], self.root / 'books')
        book = Path(result['path']); inode = (book / '0001.m4b').stat().st_ino
        self.assertEqual([x['track'] for x in result['chapters']], [1, 2])
        result = publish_chapters(self.source_id, [third, first, second], self.root / 'books')
        self.assertEqual(result['available'], 3); self.assertEqual(len(list((self.root / 'books').glob('[a-f0-9]' * 64))), 1)
        self.assertEqual((book / '0001.m4b').stat().st_ino, inode)
        for entry in (first, second, third): self.assertEqual((book / f'{entry["track"]:04d}.m4b').read_bytes(), Path(entry['path']).read_bytes())
        for name, checksum in original.items(): self.assertEqual(hashlib.sha256((self.root / name).read_bytes()).hexdigest(), checksum)

    def test_duplicate_edition_mismatch_and_destructive_shrink_are_refused(self):
        first, second = self.entry(1), self.entry(2)
        for rows in ([first, first], [first, {**second, 'source_upload_id': 'b' * 64}], [first, {**second, 'track_total': 5}]):
            with self.assertRaises(ValueError): publish_chapters(self.source_id, rows, self.root / 'books')
        publish_chapters(self.source_id, [first, second], self.root / 'books')
        with self.assertRaises(ValueError): publish_chapters(self.source_id, [first], self.root / 'books')

    def test_later_chapters_wait_for_the_missing_source_chapter(self):
        result = publish_chapters(self.source_id, [self.entry(3), self.entry(1)], self.root / 'books')
        self.assertEqual(result['available'], 1); self.assertEqual(result['waiting_tracks'], [3])
        self.assertFalse((Path(result['path']) / '0003.m4b').exists())

    def test_invalid_source_and_symlinks_never_publish(self):
        entry = self.entry(1)
        with self.assertRaises(ValueError): publish_chapters('../escape', [entry], self.root / 'books')
        link = self.root / 'link.m4b'; link.symlink_to(entry['path'])
        with self.assertRaises(ValueError): publish_chapters(self.source_id, [{**entry, 'path': str(link)}], self.root / 'books')

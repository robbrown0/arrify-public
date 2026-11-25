import base64
from pathlib import Path
import tempfile
import unittest

from app.media_type import audio_mime, original_mime


class OriginalMediaTypeTests(unittest.TestCase):
    def test_mp3_in_m4a_named_file_uses_actual_container_and_preserves_cursor(self):
        encoded = Path(__file__).resolve().parents[1] / 'tools/media-probe/silence.mp3.base64'
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / 'incorrect.m4a'
            filename.write_bytes(base64.b64decode(encoded.read_text()))
            self.assertEqual(original_mime(filename, 'audio/mp4'), 'audio/mpeg')
            with filename.open('rb') as audio:
                audio.seek(31)
                self.assertEqual(audio_mime(audio, 'audio/mp4'), 'audio/mpeg')
                self.assertEqual(audio.tell(), 31)

    def test_known_containers_override_incorrect_extension_type(self):
        fixtures = [(b'fLaC'+bytes(20), 'audio/flac'),
                    (b'\x00\x00\x00\x18ftypM4A ', 'audio/mp4'),
                    (b'RIFF\x10\x00\x00\x00WAVE', 'audio/wav'),
                    (b'\xff\xfb\x90\x00'+bytes(20), 'audio/mpeg')]
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / 'original.audio'
            for body, expected in fixtures:
                with self.subTest(expected=expected):
                    filename.write_bytes(body)
                    self.assertEqual(original_mime(filename, 'application/octet-stream'), expected)

    def test_unknown_and_truncated_or_malformed_id3_keep_catalog_type(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / 'original.m4a'
            for body in (b'unknown', b'ID3', b'ID3\x04\x00\x00\x7f\x7f\x7f\x7f', b'ID3\x04\x00\x00\x80\x00\x00\x00'):
                filename.write_bytes(body)
                self.assertEqual(original_mime(filename, 'audio/mp4'), 'audio/mp4')

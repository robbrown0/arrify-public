import tempfile
import unittest
from unittest.mock import patch,MagicMock
from urllib.error import HTTPError
from app.catalog_artwork import thumbnail,FALLBACK

class CatalogArtworkTests(unittest.TestCase):
    group='69ee3720-a7cb-4402-b48d-a02c366f2bcf'
    def test_success_is_cached_and_does_not_fetch_again(self):
        with tempfile.TemporaryDirectory() as root,patch('app.catalog_artwork.urllib.request.urlopen') as fetch:
            fetch.return_value.__enter__.return_value.read.return_value=b'\xff\xd8\xfftest'
            self.assertEqual(thumbnail(self.group,root)[1],b'\xff\xd8\xfftest')
            self.assertEqual(thumbnail(self.group,root)[1],b'\xff\xd8\xfftest')
            self.assertEqual(fetch.call_count,1)
    def test_missing_cover_uses_cached_fallback(self):
        with tempfile.TemporaryDirectory() as root,patch('app.catalog_artwork.urllib.request.urlopen',side_effect=HTTPError('',404,'missing',{},None)) as fetch:
            self.assertEqual(thumbnail(self.group,root)[1],FALLBACK)
            self.assertEqual(thumbnail(self.group,root)[1],FALLBACK)
            self.assertEqual(fetch.call_count,1)
    def test_invalid_ids_and_non_images_are_rejected(self):
        with self.assertRaises(ValueError):thumbnail('../escape')
        with tempfile.TemporaryDirectory() as root,patch('app.catalog_artwork.urllib.request.urlopen') as fetch:
            fetch.return_value.__enter__.return_value.read.return_value=b'<html>not artwork</html>'
            self.assertEqual(thumbnail(self.group,root)[1],FALLBACK)

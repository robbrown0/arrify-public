import base64
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from app.activity_artwork import ActivityArtwork

class ActivityArtworkTests(unittest.TestCase):
    def test_real_encoder_bounds_caches_and_invalidates_cover(self):
        image = subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc=size=128x128','-frames:v','1','-threads','1','-f','image2pipe','-vcodec','png','pipe:1'], capture_output=True, check=True).stdout
        catalog = SimpleNamespace(artwork_version=Mock(return_value='v1'), artwork=Mock(return_value=('image/png',image,'embedded')))
        cache = ActivityArtwork(catalog)
        cover = cache.get(1)
        self.assertIsNotNone(cover)
        self.assertLessEqual(len(base64.b64decode(cover)),1400)
        self.assertEqual(cache.get(1),cover)
        self.assertEqual(catalog.artwork.call_count,1)
        catalog.artwork_version.return_value='v2';cache.get(1)
        self.assertEqual(catalog.artwork.call_count,2)
    def test_missing_cover_does_not_block_push(self):
        catalog = SimpleNamespace(artwork_version=lambda _: 'v1', artwork=lambda _: ('image/svg+xml',b'<svg/>','fallback'))
        self.assertIsNone(ActivityArtwork(catalog).get(1))

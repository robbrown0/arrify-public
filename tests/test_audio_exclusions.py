import json,tempfile,unittest
from pathlib import Path
from app.catalog_engine import Catalog

class AudioExclusionTests(unittest.TestCase):
    def test_exclusion_is_scoped_to_source_path_and_file_fingerprint(self):
        with tempfile.TemporaryDirectory() as folder:
            music=Catalog.__new__(Catalog);music.audio_exclusions_file=Path(folder)/'audio-exclusions.json'
            self.assertEqual(music._audio_exclusions(),{})
            music.audio_exclusions_file.write_text(json.dumps([{'source':'nas01','relative_path':'Artist/song.m4a','size':123,'modified_ns':456}]))
            exclusions=music._audio_exclusions()
            self.assertEqual(exclusions[('nas01','Artist/song.m4a')],(123,456))
            self.assertNotEqual(exclusions[('nas01','Artist/song.m4a')],(124,789))
            self.assertNotIn(('unr01','Artist/song.m4a'),exclusions)
            music.audio_exclusions_file.write_text('broken');self.assertEqual(music._audio_exclusions(),{})

import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'request-bridge'))
from hitdiscovery.matching import Ownership

class LiveAlbumOwnershipTests(unittest.TestCase):
    def test_unlabelled_live_reaper_does_not_satisfy_studio_request(self):
        song={'artist':'Blue Öyster Cult','title':"(Don't Fear) The Reaper"}
        self.assertFalse(Ownership([dict(song,album='Some Enchanted Evening')]).owns(song))
        self.assertTrue(Ownership([dict(song,album='Agents of Fortune')]).owns(song))
    def test_unrelated_artist_is_unchanged(self):
        song={'artist':'Another Artist','title':'Song','album':'Some Enchanted Evening'}
        self.assertTrue(Ownership([song]).owns(song))

import unittest
from app.catalog_engine import Catalog
class PlaylistVersionTests(unittest.TestCase):
 def test_standard_playlist_rejects_acoustic_album_with_plain_title(self):
  t={'artist':'Jackson Browne','title':"Somebody's Baby",'album':'Solo Acoustic, Vol. 2'}
  w={'artists':['Jackson Browne'],'title':"Somebody's Baby"}
  self.assertEqual(Catalog._playlist_match(t,w),-1)
  self.assertGreaterEqual(Catalog._playlist_match(t,w|{'_allow_alternate':True}),0)
 def test_explicit_acoustic_album_and_original_hit_remain_selectable(self):
  t={'artist':'Jackson Browne','title':"Somebody's Baby",'album':'Solo Acoustic, Vol. 2'}
  self.assertGreaterEqual(Catalog._playlist_match(t,{'artists':['Jackson Browne'],'title':"Somebody's Baby",'album':'Solo Acoustic, Vol. 2'}),0)
  self.assertGreaterEqual(Catalog._playlist_match({'artist':'Tesla','title':'Signs','album':'Five Man Acoustical Jam'},{'artists':['Tesla'],'title':'Signs'}),0)

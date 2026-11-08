import tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
from hitdiscovery.store import Store
from hitdiscovery.inventory import scan
from hitdiscovery.album_index import read_ids
from hitdiscovery.api import APIError
class InventoryTests(unittest.TestCase):
 def test_full_snapshot_but_only_owned_tracks_retained(self):
  class Lidarr:
   def get(self,path,**kw):
    if path=='artist':return [{'id':1,'artistName':'Artist'}]
    if path=='album':return []
    if path=='trackfile':return [{'id':8,'large':'x'*10000}]
    return [{'id':i,'artistId':1,'title':'Song'+str(i),'hasFile':i==2,'foreignRecordingId':'r'+str(i),'large':'x'*10000} for i in range(100)]
  with tempfile.TemporaryDirectory() as d:
   store=Store(Path(d)/'db')
   with patch('hitdiscovery.inventory.scan_files',return_value=([],{})), patch('hitdiscovery.inventory.tempfile.TemporaryFile', wraps=tempfile.TemporaryFile) as spool:
    _,_,tracks,summary=scan(Lidarr(),store,[])
   self.assertEqual(spool.call_count,2)
   for call in spool.call_args_list:self.assertEqual(call.kwargs['dir'],Path(d))
   self.assertEqual(len(tracks),1);self.assertNotIn('large',tracks[0]);self.assertEqual(tracks[0]['recording_ids'],['r2'])
   self.assertEqual(summary['lidarr_catalog_tracks'],100);self.assertEqual(summary['lidarr_track_files'],1)
   self.assertEqual(store.db.execute("select count(*) from inventory where kind='track'").fetchone()[0],100)
   self.assertIn('large',json.loads(store.db.execute("select payload from inventory where kind='track' limit 1").fetchone()[0]))
   store.db.close()
 def test_missing_album_does_not_fetch_entire_library(self):
  class Lidarr:
   def get(self,path,**kw):
    if path=='album':
     assert 'albumIds' in kw
     raise APIError('GET album HTTP 500')
    if path=='album/2':raise APIError('GET album HTTP 404')
    return {'id':int(path.split('/')[1])}
  self.assertEqual(read_ids(Lidarr(),[1,2,3]),[{'id':1},{'id':3}])
 def test_transient_failure_is_not_silently_treated_as_deleted(self):
  class Lidarr:
   def get(self,path,**kw):raise APIError('GET album HTTP 500')
  with self.assertRaises(APIError):read_ids(Lidarr(),[1])

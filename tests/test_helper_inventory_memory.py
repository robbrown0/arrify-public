import tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
from hitdiscovery.store import Store
from hitdiscovery.inventory import scan
from hitdiscovery.album_index import read_ids
from hitdiscovery.api import APIError
class InventoryTests(unittest.TestCase):
 def test_album_snapshot_but_only_owned_tracks_retained(self):
  class Lidarr:
   def get(self,path,**kw):
    if path=='artist':return [{'id':1,'artistName':'Artist'}]
    if path=='album':
     self_outer.assertEqual(kw,{'artistId':1})
     return [{'id':7,'artistId':1,'title':'Album','monitored':True,'statistics':{'trackFileCount':1,'totalTrackCount':100}}]
    if path=='trackfile':return [{'id':8,'albumId':7,'large':'x'*10000}]
    self_outer.assertEqual(path,'track');self_outer.assertEqual(kw,{'albumId':7})
    return [{'id':i,'artistId':1,'title':'Song'+str(i),'hasFile':i==2,'foreignRecordingId':'r'+str(i),'large':'x'*10000} for i in range(100)]
  self_outer=self
  with tempfile.TemporaryDirectory() as d:
   store=Store(Path(d)/'db')
   with patch('hitdiscovery.inventory.scan_files',return_value=([],{})), patch('hitdiscovery.inventory.tempfile.TemporaryFile', wraps=tempfile.TemporaryFile) as spool:
    _,_,tracks,summary=scan(Lidarr(),store,[])
   self.assertEqual(spool.call_count,2)
   for call in spool.call_args_list:self.assertEqual(call.kwargs['dir'],Path(d))
   self.assertEqual(len(tracks),1);self.assertNotIn('large',tracks[0]);self.assertEqual(tracks[0]['recording_ids'],['r2'])
   self.assertEqual(summary['lidarr_catalog_tracks'],100);self.assertEqual(summary['lidarr_track_files'],1)
   self.assertEqual(summary['lidarr_track_scope'],'albums-with-files')
   self.assertEqual(store.db.execute("select count(*) from inventory where kind='track'").fetchone()[0],100)
   self.assertIn('large',json.loads(store.db.execute("select payload from inventory where kind='track' limit 1").fetchone()[0]))
   store.db.close()
 def test_large_missing_catalog_never_requests_artist_tracks(self):
  class Lidarr:
   def get(self,path,**kw):
    if path=='artist':return [{'id':1809,'artistName':'Large missing catalog'}]
    if path=='album':return [{'id':i,'artistId':1809,'title':'Album'+str(i),'monitored':True,'statistics':{'trackFileCount':0,'totalTrackCount':100}} for i in range(4500)]
    if path=='trackfile':return []
    raise AssertionError('No ownership track request is needed for an empty artist')
  with tempfile.TemporaryDirectory() as d:
   store=Store(Path(d)/'db')
   with patch('hitdiscovery.inventory.scan_files',return_value=([],{})):
    _,albums,tracks,summary=scan(Lidarr(),store,[])
   self.assertEqual(len(albums),4500);self.assertEqual(tracks,[])
   self.assertEqual(summary['lidarr_track_albums_scanned'],0)
   self.assertEqual(store.db.execute("select count(*) from inventory where kind='album'").fetchone()[0],4500)
   store.db.close()
 def test_file_reference_and_statistics_both_preserve_partial_album_ownership(self):
  calls=[]
  class Lidarr:
   def get(self,path,**kw):
    calls.append((path,kw))
    if path=='artist':return [{'id':1,'artistName':'Artist'}]
    if path=='album':return [
     {'id':7,'artistId':1,'title':'Lagging album','monitored':True,'statistics':{'trackFileCount':0}},
     {'id':9,'artistId':1,'title':'Partial album','monitored':True,'statistics':{'trackFileCount':1,'totalTrackCount':2}}]
    if path=='trackfile':return [{'id':8,'albumId':7},{'id':10,'albumId':7}]
    if path=='track':return [{'id':kw['albumId'],'artistId':1,'trackFileId':8,'foreignRecordingId':'owned'},
                             {'id':100+kw['albumId'],'hasFile':False}]
    raise AssertionError(path)
  with tempfile.TemporaryDirectory() as d:
   store=Store(Path(d)/'db')
   with patch('hitdiscovery.inventory.scan_files',return_value=([{'owned':True,'artist':'NAS','title':'Local'}],{})):
    _,_,tracks,summary=scan(Lidarr(),store,[])
   self.assertEqual([kw for path,kw in calls if path=='track'],[{'albumId':7},{'albumId':9}])
   self.assertEqual(len(tracks),3);self.assertEqual(summary['lidarr_catalog_tracks'],4)
   self.assertEqual(summary['lidarr_track_files'],2)
   store.db.close()
 def test_invalid_file_identity_keeps_previous_inventory(self):
  class Lidarr:
   def get(self,path,**kw):
    if path=='artist':return [{'id':1,'artistName':'Artist'}]
    if path=='album':return []
    if path=='trackfile':return [{'id':8}]
    raise AssertionError(path)
  with tempfile.TemporaryDirectory() as d:
   store=Store(Path(d)/'db');store.inventory('track',[{'id':1,'owned':True}])
   with self.assertRaisesRegex(ValueError,'album identity'):scan(Lidarr(),store,[])
   self.assertEqual(store.db.execute("select count(*) from inventory where kind='track'").fetchone()[0],1)
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

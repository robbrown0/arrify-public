import unittest,copy
from hitdiscovery.request_catalog import album_tracks,choose_release
from tests.test_request_catalog import recording,RID,AID,GID,REL
class AlbumTests(unittest.TestCase):
 def setUp(self):
  self.selected={'artist':'Test Artist','resolved':choose_release(recording())}
  self.release={'id':REL,'title':'Original Album','status':'Official','release-group':{'id':GID,'primary-type':'Album'},'artist-credit':[{'artist':{'id':AID,'name':'Test Artist'}}],'media':[{'position':1,'track-count':2,'tracks':[{'number':'1','recording':{'id':RID,'title':'Test Song'}},{'number':'2','recording':{'id':'55555555-5555-5555-5555-555555555555','title':'Second Song'}}]}]}
 def test_track_order_and_pinned_requests(self):
  result=album_tracks(self.selected,lambda *a,**kw:self.release)
  self.assertEqual([x['title'] for x in result['items']],['Test Song','Second Song'])
  self.assertNotEqual(result['items'][0]['selection'],result['items'][1]['selection'])
  self.assertEqual(result['items'][1]['resolved']['release_group_id'],GID)
 def test_wrong_release_or_incomplete_metadata_rejected(self):
  for mutate in [lambda r:r.update(status='Bootleg'),lambda r:r['release-group'].update(id=RID),lambda r:r['media'][0].update(**{'track-count':3}),lambda r:r['release-group'].update(**{'secondary-types':['Compilation']})]:
   r=copy.deepcopy(self.release);mutate(r)
   with self.assertRaises(ValueError):album_tracks(self.selected,lambda *a,**kw:r)
if __name__=='__main__':unittest.main()

class LocalCopyTests(unittest.TestCase):
 def test_best_quality_and_no_extra_rows(self):
  from app.catalog_album import attach_local
  result={'album':{'artist':'P!nk'},'requested_artist':'Pink','items':[{'title':'Trouble','artist':'P!nk'},{'title':'Other','artist':'P!nk'}]}
  tracks=[{'id':1,'title':'Trouble','artist':'Pink','quality':'128 kbps'},{'id':2,'title':'Trouble','artist':'Pink','quality':'Lossless'},{'id':3,'title':'Trouble (Live)','artist':'Pink','quality':'Lossless'}]
  out=attach_local(result,tracks,{1:{'bitrate_kbps':128},2:{'lossless':1,'bitrate_kbps':900},3:{'lossless':1,'bitrate_kbps':1400}})
  self.assertEqual(len(out['items']),2);self.assertEqual(out['items'][0]['local_track_id'],2);self.assertIsNone(out['items'][1]['local_track_id'])

class ArtistAliasTests(AlbumTests):
 def test_song_disambiguates_artist_alias(self):
  from hitdiscovery.request_catalog import album_for_song
  record=recording();record['artist-credit'][0]['artist']['name']='P!nk'
  self.release['artist-credit']=record['artist-credit']
  def get(path,**kw):
   if path=='artist/':return {'artists':[{'id':AID,'name':'P!nk','aliases':[{'name':'Pink'}]},{'id':GID,'name':'Pink'}]}
   if path=='recording/':return {'recordings':[record]}
   if path=='recording/'+RID:return record
   if path=='release/':return {'releases':record['releases']}
   if path=='release/'+REL:return self.release
   raise AssertionError(path)
  out=album_for_song('Pink','Test Song',get)
  self.assertEqual(out['requested_artist'],'Pink');self.assertEqual(out['album']['artist'],'P!nk')

class AlbumRouteTests(unittest.TestCase):
 def test_authenticated_get_dispatch_and_best_local_match(self):
  import ast,types,threading,sqlite3,re
  from pathlib import Path
  from urllib.parse import urlparse,parse_qs,unquote
  from app.server import Handler
  from unittest.mock import patch
  db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;db.executescript('CREATE TABLE track_technical(track_id,lossless,bitrate_kbps);INSERT INTO track_technical VALUES(1,0,128);INSERT INTO track_technical VALUES(2,1,900);')
  result={'album':{'artist':'Artist'},'items':[{'artist':'Artist','title':'Song'}]}
  lib=types.SimpleNamespace(index_lock=threading.RLock(),tracks={i:{'id':i,'artist':'Artist','title':'Song'} for i in (1,2)},music=types.SimpleNamespace(lock=threading.RLock(),db=db))
  request=types.SimpleNamespace(path='/api/v1/discover/album?selection='+RID,identity=lambda:{'id':1},json=lambda value:value,server=types.SimpleNamespace(accounts=types.SimpleNamespace(rate_limit=lambda *a:None),library=lib,player=None,request_submissions=types.SimpleNamespace(mark_requested=lambda items:items)))
  with patch('app.server.acquisition.call',return_value=copy.deepcopy(result)):
   self.assertEqual(Handler.get(request)['items'][0]['local_track_id'],2)
  db.close()

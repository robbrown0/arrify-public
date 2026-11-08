import unittest
from hitdiscovery.request_catalog import artist_albums,explore_catalog,album_for_group
from tests.test_catalog_album import AlbumTests
from tests.test_request_catalog import AID,GID,REL,recording

class ArtistCatalogTests(unittest.TestCase):
 def test_alias_identity_and_actual_page_size(self):
  def get(path,**kw):
   if path=='artist/':return {'artists':[{'id':AID,'name':'P!nk','aliases':[{'name':'Pink'}]},{'id':GID,'name':'Pink'}]}
   if path=='recording/':return {'recordings':[recording()]}
   self.assertEqual(kw['artist'],AID);self.assertEqual(kw['offset'],50)
   return {'release-groups':[{'id':GID,'title':'Album'}],'release-group-count':52}
  r=artist_albums('Pink','Test Song',get,50)
  self.assertEqual(r['next_offset'],51);self.assertEqual(r['artist'],'P!nk')
 def test_ambiguous_artist_rejected(self):
  def get(path,**kw):
   if path=='artist/':return {'artists':[{'id':AID,'name':'Artist'},{'id':GID,'name':'Artist'}]}
   return {'recordings':[{'title':'Song','artist-credit':[{'artist':{'id':AID}},{'artist':{'id':GID}}]}]}
  with self.assertRaises(ValueError):artist_albums('Artist','Song',get)
 def test_combined_search_uses_album_and_artist_terms(self):
  calls=[]
  def get(path,**kw):
   calls.append((path,kw));return {'release-groups':[{'id':GID,'title':'Try This','artist-credit':[{'artist':{'id':AID,'name':'P!nk'}}]}],'count':21}
  r=explore_catalog('Pink Try This',get)
  self.assertIn('(releasegroup:"Pink" OR alias:"Pink" OR artist:"Pink")',next(kw['query'] for path,kw in calls if path=='release-group/'));self.assertEqual(r['next_offset'],1)
  self.assertFalse(r['items'][0]['requestable'])
 def test_explicit_album_pins_all_tracks_without_submission(self):
  fixture=AlbumTests();fixture.setUp()
  def get(path,**kw):
   if path=='release-group/'+GID:return {'id':GID,'title':'Original Album','primary-type':'Album','artist-credit':[{'artist':{'id':AID,'name':'Test Artist'}}]}
   if path=='release/':return {'releases':[fixture.release]}
   if path=='release/'+REL:return fixture.release
   if path=='artist/'+AID:return {'id':AID,'aliases':[{'name':'Test Alias'}]}
   raise AssertionError(path)
  r=album_for_group(GID,get)
  self.assertEqual(len(r['items']),2)
  self.assertTrue(all(x['resolved']['release_id']==REL for x in r['items']))

class CatalogRouteTests(unittest.TestCase):
 def test_real_get_routes_reach_helper_without_submission(self):
  import ast,types,re
  from pathlib import Path
  from urllib.parse import urlparse,parse_qs,unquote
  source=Path('app/server.py');tree=ast.parse(source.read_text())
  handler=next(n for n in tree.body if isinstance(n,ast.ClassDef) and any(isinstance(m,ast.FunctionDef) and m.name=='get' for m in n.body));method=next(m for m in handler.body if isinstance(m,ast.FunctionDef) and m.name=='get')
  calls=[]
  from app import native_http
  scope={'native_http':native_http,'__package__':'app','urlparse':urlparse,'parse_qs':parse_qs,'unquote':unquote,'re':re,'spotify_http':types.SimpleNamespace(get=lambda *a:False),'party_http':types.SimpleNamespace(public_get=lambda *a:False),'acquisition':types.SimpleNamespace(call=lambda path:calls.append(path) or {'items':[]})}
  exec(compile(ast.Module(body=[method],type_ignores=[]),str(source),'exec'),scope)
  lib=types.SimpleNamespace(detail=lambda kind,key:{'name':'Pink','tracks':[{'title':'Trouble'}]})
  req=types.SimpleNamespace(identity=lambda:{'id':1},json=lambda value:value,server=types.SimpleNamespace(accounts=types.SimpleNamespace(rate_limit=lambda *a:None),library=lib,player=None))
  for path in ['/api/v1/discover/explore?q=Pink+Try+This','/api/v1/discover/artist-albums?artist=local&offset=50','/api/v1/discover/artist-albums?mbid='+AID]:
   req.path=path;self.assertEqual(scope['get'](req),{'items':[]})
  self.assertIn('q=Pink+Try+This',calls[0]);self.assertIn('offset=50',calls[1]);self.assertIn('artist='+AID,calls[2])

class AliasSearchRegression(unittest.TestCase):
 def test_exact_alias_is_not_discarded_by_relative_search_score(self):
  def get(path,**kw):
   if path=='artist/':return {'artists':[{'id':AID,'name':'P!nk','score':66,'aliases':[{'name':'Pink'}]}]}
   return {'release-groups':[],'count':0}
  self.assertEqual(explore_catalog('Pink',get)['artists'][0]['name'],'P!nk')

class AlbumAliasRegression(unittest.TestCase):
 def test_alias_album_visible_without_corrupting_pagination(self):
  def get(path,**kw):
   if path=='artist/':return {'artists':[]}
   if kw['query'].startswith('alias:'):return {'release-groups':[{'id':AID,'title':'M!ssundaztood'}]}
   return {'release-groups':[{'id':GID,'title':'Misunderstood'}],'count':40}
  r=explore_catalog('misunderstood',get)
  self.assertEqual(r['items'][0]['title'],'M!ssundaztood');self.assertEqual(r['next_offset'],1)

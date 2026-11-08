import tempfile,unittest,json,sys
from pathlib import Path
from app.accounts import Accounts
from app.channels import PersonalStations,CHANNELS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'request-bridge'))
from hitdiscovery import station_subscriptions as registry
class StationsTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.a=Accounts(self.tmp.name)
  for i in (1,2):self.a.db.execute('INSERT INTO users(id,username,password,created) VALUES(?,?,?,0)',(i,str(i),'unused'))
  self.s=PersonalStations(self.a);self.c=[{'slug':s,'name':s,'section':'channels','tracks':3} for s,_ in CHANNELS]
 def tearDown(self):self.a.db.close();self.tmp.cleanup()
 def change(self,action,slug,**kw):return self.s.update(1,dict(action=action,slug=slug,revision=self.s.get(1,self.c)['revision'],**kw),self.c,lambda c:{'station':{'slug':registry.slug(c),'name':c}})
 def test_defaults_and_isolation(self):
  expected=[s for s,_ in CHANNELS];self.assertEqual([x['slug'] for x in self.s.get(1,self.c)['items']],expected)
  self.change('move',expected[0],target=4);self.assertEqual(self.s.get(1,self.c)['items'][4]['slug'],expected[0]);self.assertEqual([x['slug'] for x in self.s.get(2,self.c)['items']],expected)
  self.change('hide',expected[0],hidden=True);self.assertNotIn(expected[0],[x['slug'] for x in self.s.collections(1,self.c)])
  self.change('remove',expected[0]);self.assertEqual(len(self.s.get(1,self.c)['items']),6)
 def test_add_conflict_and_empty(self):
  self.change('add','sirius-test');self.assertTrue(self.s.collections(1,self.c)[-1]['preparing'])
  with self.assertRaises(ValueError):self.s.update(1,{'action':'remove','slug':'sirius-test','revision':0},self.c,None)
  for row in self.s.get(1,self.c)['items']:self.change('remove',row['slug'])
  self.assertEqual(self.s.get(1,self.c)['items'],[])
 def test_registry_validation_idempotence(self):
  data=Path(self.tmp.name);registry.atomic(data/'station-directory.json',{'items':[{'id':'test','slug':'sirius-test','name':'Test'}]})
  registry.request(data,'test');registry.request(data,'test');self.assertEqual(len(registry.requested(data)),1)
  for invalid in ['../evil','unknown','https://host',None]:
   with self.assertRaises(ValueError):registry.request(data,invalid)
  self.assertEqual(registry.acquisition_channels(data),{'sirius-test'})
 def test_artwork_rejects_untrusted_urls(self):
  self.assertIsNone(registry.image_url('https://evil.test/channel.webp'))
  self.assertIsNone(registry.image_url('https://images.xmplaylist.com@evil.test/a.webp'))
  value='https://images.xmplaylist.com/channel-images/'+'a'*64+'.webp';self.assertEqual(registry.image_url(value),value)
if __name__=='__main__':unittest.main()

class StationRouteTest(StationsTest):
 def test_real_post_dispatch_add_and_remove(self):
  import ast,types
  from urllib.parse import urlparse
  source=Path(__file__).resolve().parents[1]/'app/server.py'
  tree=ast.parse(source.read_text());handler=next(n for n in tree.body if isinstance(n,ast.ClassDef) and any(isinstance(m,ast.FunctionDef) and m.name=='post' for m in n.body))
  method=next(n for n in handler.body if isinstance(n,ast.FunctionDef) and n.name=='post')
  from app import native_http
  scope={'native_http':native_http,'__package__':'app','urlparse':urlparse,'party_http':types.SimpleNamespace(public_post=lambda *a:False),'spotify_http':types.SimpleNamespace(post=lambda *a:False),'acquisition':types.SimpleNamespace(call=lambda path,data:{'station':{'slug':'sirius-'+data['id'],'name':'Watercolors'}})}
  exec(compile(ast.Module(body=[method],type_ignores=[]),str(source),'exec'),scope)
  self.a.rate_limit=lambda *a:None
  req=types.SimpleNamespace(path='/api/v1/stations',server=types.SimpleNamespace(accounts=self.a,library=types.SimpleNamespace(collections=lambda:self.c),player=None),identity=lambda **kw:{'id':1},json=lambda value:value)
  req.body=lambda:{'action':'add','slug':'sirius-watercolors','revision':0}
  result=scope['post'](req);self.assertEqual(result['items'][-1]['name'],'Watercolors')
  req.body=lambda:{'action':'remove','slug':'sirius-watercolors','revision':1}
  self.assertEqual(len(scope['post'](req)['items']),7)

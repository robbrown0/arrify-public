import tempfile,unittest
from pathlib import Path
from app.web_assets import WebAssets

class WebAssetTests(unittest.TestCase):
 def test_versions_change_with_bytes_and_html_stays_fresh(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'app.js').write_text('first');(p/'index.html').write_text('<script src="/app.js"></script><a href="/api/v1/me">API</a>')
   assets=WebAssets(p);body,digest=assets.asset('app.js');html,headers=assets.response('index.html');self.assertIn(('?v='+digest).encode(),html);self.assertIn(b'/api/v1/me',html);self.assertEqual(headers['Cache-Control'],'no-store')
   self.assertIn('immutable',assets.response('app.js',digest)[1]['Cache-Control'])
   (p/'app.js').write_text('second');self.assertNotEqual(assets.asset('app.js')[1],digest);self.assertEqual(assets.response('app.js',digest)[1]['Cache-Control'],'no-store')
 def test_worker_unknown_versions_and_unversioned_scripts_never_immutable(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'sw.js').write_text('worker');(p/'app.js').write_text('app');assets=WebAssets(p)
   self.assertEqual(assets.response('sw.js',assets.asset('sw.js')[1])[1]['Cache-Control'],'no-store')
   for v in [None,'madeup']:self.assertEqual(assets.response('app.js',v)[1]['Cache-Control'],'no-store')
   with self.assertRaises(ValueError):assets.asset('../secret.js')
 def test_asset_memory_cache_is_bounded(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);assets=WebAssets(p,capacity=2)
   for name in ['a.js','b.js','c.js']:(p/name).write_text(name);assets.asset(name)
   self.assertEqual(len(assets.cache),2)

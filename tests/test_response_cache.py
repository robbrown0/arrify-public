"""Exercise the real response writer without loading production services."""
import io,types,unittest
from app.server import Handler
class CacheHeaders(unittest.TestCase):
 def response(self,extra=None,path='/api/v1/me'):
  headers=[];obj=types.SimpleNamespace(server=types.SimpleNamespace(),path=path,command='GET',wfile=io.BytesIO(),send_response=lambda code:None,send_header=lambda k,v:headers.append((k.lower(),v)),end_headers=lambda:None)
  Handler.send(obj,200,b'ok',extra=extra)
  return [v for k,v in headers if k=='cache-control']
 def test_private_api_defaults_to_no_store(self):self.assertEqual(self.response(),['no-store'])
 def test_public_asset_has_only_its_cache_policy(self):self.assertEqual(self.response({'Cache-Control':'public, max-age=86400'},'/assets/icon-192.png'),['public, max-age=86400'])
 def test_private_artwork_stays_private(self):self.assertEqual(self.response({'Cache-Control':'private, max-age=300'}),['private, max-age=300'])
if __name__=='__main__':unittest.main()

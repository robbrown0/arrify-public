import json,tempfile,unittest
from pathlib import Path
from app.diagnostics import Diagnostics
class ArtworkDiagnosticsTests(unittest.TestCase):
 def test_bounded_private_redacted_events(self):
  with tempfile.TemporaryDirectory() as root:
   d=Diagnostics(root)
   for tid in range(80):d.artwork_response(tid,200,1024,True,'AppleCoreMedia private-secret')
   d.artwork_response(79,200,1024,True,'AppleCoreMedia private-secret')
   p=Path(root)/'playback-diagnostics/artwork-responses.json';data=p.read_text();rows=json.loads(data)['events']
   self.assertEqual(len(rows),60);self.assertNotIn('private-secret',data);self.assertEqual(rows[-1]['client'],'receiver');self.assertEqual(p.stat().st_mode&0o777,0o600)
 def test_denial_and_browser_are_distinguishable(self):
  with tempfile.TemporaryDirectory() as root:
   d=Diagnostics(root);d.artwork_response(1,403,10,False,'Safari')
   self.assertEqual(d.artwork_events[0]['status'],403);self.assertEqual(d.artwork_events[0]['client'],'browser')

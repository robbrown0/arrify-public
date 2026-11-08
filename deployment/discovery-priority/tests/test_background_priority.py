import unittest,tempfile
from pathlib import Path
from unittest.mock import Mock,patch
from hitdiscovery.store import Store,now
from hitdiscovery.config import Config
from hitdiscovery.expansion import maintenance
class BackgroundPriorityTests(unittest.TestCase):
 def test_pending_user_request_yields_lock_before_any_background_mutation(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);s=Store(root/'discovery.sqlite3')
   s.cache('arrify-priority:user',{'requested':now()});s.cache('arrify-authorization:user',{'request_id':'user'})
   s.reserve('background',100);s.request_state('background','prepared',5);s.db.close()
   api=Mock();state={'album':[{'id':5,'foreignAlbumId':'background','artistId':1,'title':'Background','monitored':False}], 'queue':{'records':[]},'qualityprofile':[]}
   api.get.side_effect=lambda path,**kw:state[path]
   c=Config(data=root,reports=root,acquire=True,reviewed=True,expansion=True,budget_date='daily',budget_bytes=10**12)
   with patch('hitdiscovery.expansion.Lidarr',return_value=api):result=maintenance(c)
   self.assertEqual(result['decisions'],[]);api.write.assert_not_called()
if __name__=='__main__':unittest.main()

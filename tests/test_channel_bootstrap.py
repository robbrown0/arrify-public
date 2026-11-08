import json,tempfile,unittest
from pathlib import Path
from hitdiscovery.channel_bootstrap import sparse_channels,select
from hitdiscovery.budget import choose_release
class BootstrapTests(unittest.TestCase):
 def test_only_new_sparse_channels(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'requested-stations.json').write_text(json.dumps({'items':[{'id':'new','requested':100},{'id':'full','requested':100},{'id':'old','requested':1}]}));(p/'full-status.json').write_text('{"owned_songs":20}')
   self.assertEqual(sparse_channels(p,7*86400+50),{'sirius-new'})
 def test_gain_dedup_and_bounded_selection(self):
  groups=[{'release_group_id':str(i),'songs':[{'recording_id':str(n),'discovery_channel':'sirius-new'} for n in range(i)]} for i in range(1,6)]
  self.assertEqual([g['release_group_id'] for g in select(groups,{'sirius-new'})],['5','4','3'])
  self.assertEqual(select(groups,{'unrelated'}),[])
 def test_indexer_priority_breaks_quality_tie(self):
  # Actual quality fixture follows Lidarr's release API.
  r={'quality':{'quality':{'name':'FLAC'}},'qualityWeight':1,'customFormatScore':0,'size':100,'title':'Artist Album','approved':True,'downloadAllowed':True,'guid':'a','indexerId':1}
  other=dict(r,guid='b',indexerId=2,size=90)
  result=choose_release([r,other],4,10000,set(),indexer_priorities={1:1,2:25})
  self.assertEqual(result['guid'],'a')
  self.assertIsNone(choose_release([dict(r,downloadAllowed=False)],4,10000,set(),indexer_priorities={1:1}))

import json,sqlite3,tempfile,unittest,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'request-bridge'))
from hitdiscovery.activity import snapshot
from hitdiscovery.matching import key
class ActivityTests(unittest.TestCase):
 def test_bounded_sanitized_snapshot_and_station_stages(self):
  with tempfile.TemporaryDirectory() as root:
   p=Path(root);db=sqlite3.connect(p/'discovery.sqlite3');db.executescript('CREATE TABLE runs(id,started,finished,status); CREATE TABLE requests(rg,state); CREATE TABLE decisions(artist,album,state,updated,detail); CREATE TABLE cache(key,payload);')
   db.execute("INSERT INTO requests VALUES('rg','requested')");db.execute("INSERT INTO decisions VALUES('Artist','Album','waiting-for-release','2026-11-02','SECRET /private/file')")
   db.execute('INSERT INTO cache VALUES(?,?)',('resolution:'+key('Artist','Song'),json.dumps({'resolution_status':'resolved','release_group_id':'rg'})));db.commit();db.close()
   (p/'watercolors-status.json').write_text(json.dumps({'observed_songs':3,'owned_songs':1,'songs':[{'owned':True},{'artist':'Artist','title':'Song'},{'artist':'Other','title':'Unverified'}]}))
   (p/'requested-stations.json').write_text(json.dumps({'items':[{'id':'watercolors','name':'Watercolors','slug':'sirius-watercolors'}]}));(p/'acquisition-worker.json').write_text(json.dumps({'at':time.time(),'phase':'waiting'}))
   result=snapshot(p,p);station=next(x for x in result['stations'] if x['slug']=='sirius-watercolors')
   self.assertEqual(station['counts']['queued'],1);self.assertEqual(station['counts']['available'],1);self.assertEqual(station['counts']['awaiting_verification'],1);self.assertFalse(result['worker']['stale']);self.assertNotIn('SECRET',json.dumps(result));self.assertIs(snapshot(p,p),result)
 def test_missing_database_reports_unavailable(self):
  with tempfile.TemporaryDirectory() as root:
   r=snapshot(root,root);self.assertTrue(r['unavailable']);self.assertTrue(r['worker']['stale'])
if __name__=='__main__':unittest.main()

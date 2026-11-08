import argparse
import sqlite3,threading,tempfile,time,tracemalloc,gc,json,resource
from types import SimpleNamespace
from app.library import Library
from app.browse import Browse
parser=argparse.ArgumentParser(description='Profile an offline catalog snapshot; never modifies the database or calls providers.')
parser.add_argument('catalog');parser.add_argument('--rounds',type=int,default=3);args=parser.parse_args()
tracemalloc.start(1)
db=sqlite3.connect('file:'+args.catalog+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row
lib=Library.__new__(Library);lib.index_lock=threading.RLock();lib.music=SimpleNamespace(db=db,lock=threading.RLock(),dynamic_playlists={});lib.radio=SimpleNamespace(lock=threading.RLock(),pools={})
results=[]
with tempfile.TemporaryDirectory() as tmp:
 for i in range(max(1,min(args.rounds,20))):
  start=time.monotonic();lib.reindex()
  if i==0:lib.discovery=Browse(tmp,lib,start=False)
  for q in ['rock','pop','Noah','love','dance']:lib.search.query(q)
  for offset in range(0,600,60):lib.discovery.page({'kind':'songs','offset':str(offset),'limit':'60'})
  gc.collect();current,peak=tracemalloc.get_traced_memory();results.append({'round':i,'seconds':round(time.monotonic()-start,3),'python_live_bytes':current,'python_peak_bytes':peak,'max_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'tracks':len(lib.tracks)})
 snapshot=tracemalloc.take_snapshot();print(json.dumps({'rounds':results,'top_allocations':[{'location':str(x.traceback),'bytes':x.size,'count':x.count} for x in snapshot.statistics('lineno')[:12]]}))

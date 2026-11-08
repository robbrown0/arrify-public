"""Read-only, bounded helper snapshots. Never expose raw logs, paths, or provider errors."""
import json,sqlite3,time,threading
from pathlib import Path
from .matching import key
from .station_subscriptions import definitions
from .stations import STATIONS
_lock=threading.Lock()
_cache={}
LABELS={'waiting-for-release':'Waiting for a suitable release','quality-options-exhausted':'No release met quality requirements','held-for-review':'Needs review','partial-owned':'Some tracks already available','downloaded':'Downloaded; library import may still be pending','prepared':'Verified and ready for acquisition','requested':'Search requested; download not confirmed','adding-artist':'Preparing artist metadata','already-in-local-collection':'Already in the library','in-lidarr-unmonitored':'Not currently monitored','not-in-lidarr':'Awaiting acquisition','monitored-missing':'Monitored; audio still missing','existing-incomplete':'Existing album is incomplete'}
def read(path):
 try:
  if path.stat().st_size>8*1024*1024:return {}
  return json.loads(path.read_text())
 except (OSError,ValueError):return {}
def snapshot(data,reports):
 data=Path(data);reports=Path(reports);now=time.time();cache_key=str(data)
 with _lock:
  previous=_cache.get(cache_key)
  if previous and now-previous['generated']<20:return previous
  worker=read(data/'acquisition-worker.json');heartbeat=worker.get('at');age=max(0,now-heartbeat) if isinstance(heartbeat,(int,float)) else None
  result={'generated':now,'worker':{'phase':worker.get('phase','unknown'),'updated':heartbeat,'stale':age is None or age>1800},'stations':[],'recent':[],'request_states':{},'transfers':{},'unavailable':False}
  try:
   db=sqlite3.connect('file:'+str(data/'discovery.sqlite3')+'?mode=ro',uri=True,timeout=2);db.row_factory=sqlite3.Row
   try:
    run=db.execute('SELECT started,finished,status FROM runs ORDER BY id DESC LIMIT 1').fetchone();result['discovery']=dict(run) if run else None
    result['request_states']={r['state']:r['n'] for r in db.execute('SELECT state,count(*) n FROM requests GROUP BY state')}
    result['recent']=[{'artist':r['artist'],'album':r['album'],'state':r['state'],'reason':LABELS.get(r['state'],'Waiting for the next acquisition check'),'updated':r['updated']} for r in db.execute('SELECT artist,album,state,updated FROM decisions ORDER BY updated DESC LIMIT 30')]
    defaults={x[0]:x for x in STATIONS};defaults.setdefault('theblend',('theblend','The Blend','sirius-theblend'))
    for channel,name,slug in [('thespectrum','The Spectrum','the-spectrum'),*definitions(data,tuple(defaults.values()))]:
     status=read(data/('spectrum-status.json' if channel=='thespectrum' else channel+'-status.json'))
     counts={'available':int(status.get('owned_songs',0)),'awaiting_verification':0,'verified':0,'queued':0,'downloaded':0,'waiting':0}
     for song in status.get('songs',[])[:3000]:
      if song.get('owned'):continue
      cached=db.execute('SELECT payload FROM cache WHERE key=?',('resolution:'+key(song.get('artist',''),song.get('title','')),)).fetchone()
      try:resolved=json.loads(cached['payload']) if cached else {}
      except (ValueError,TypeError):resolved={}
      if resolved.get('resolution_status')!='resolved':counts['awaiting_verification']+=1;continue
      req=db.execute('SELECT state FROM requests WHERE rg=?',(resolved.get('release_group_id',''),)).fetchone()
      state=req['state'] if req else ''
      bucket='downloaded' if state=='downloaded' else 'queued' if state in ('requested','search-pending','adding-artist','adding-album') else 'waiting' if state in ('waiting-for-release','held-for-review','quality-options-exhausted','partial-owned') else 'verified'
      counts[bucket]+=1
     result['stations'].append({'slug':slug,'name':name,'observed':status.get('observed_songs',0),'updated':status.get('updated'),'pending':not bool(status),'counts':counts})
   finally:db.close()
  except sqlite3.Error:result['unavailable']=True
  expansion=read(reports/'expansion-status.json');budget=expansion.get('budget',{})
  result['transfers']={str(k):int(v) for k,v in budget.get('transfer_states',{}).items() if isinstance(v,int)}
  result['transfers_updated']=expansion.get('generated')
  result['budget']={k:budget.get(k) for k in ('limit_bytes','remaining_bytes','charged_bytes','allowance_active')}
  _cache[cache_key]=result
  return result

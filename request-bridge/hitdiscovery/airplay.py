"""A single durable request budget for all public xmplaylist consumers."""
import fcntl,json,time
from datetime import datetime,timezone,timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from urllib.parse import urlsplit
class AirplayDeferred(RuntimeError):pass
class AirplayClient:
 def __init__(self,http,data,clock=time.time,sleep=time.sleep,opener=urlopen,spacing=30,daily_limit=240):
  self.store=http.store;self.data=Path(data);self.clock=clock;self.sleep=sleep;self.opener=opener;self.spacing=spacing;self.daily_limit=daily_limit
 def _load(self):
  p=self.data/'airplay-control.json'
  return json.loads(p.read_text()) if p.exists() else {}
 def _save(self,state):
  p=self.data/'airplay-control.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(state,indent=2));tmp.replace(p)
 def json(self,url,ttl=600):
  parsed=urlsplit(url)
  if parsed.scheme!='https' or parsed.hostname!='xmplaylist.com' or parsed.username or parsed.password:raise ValueError('Unexpected airplay endpoint')
  if self.store:
   cached=self.store.cached(url,ttl)
   if cached is not None:return cached
  with (self.data/'airplay-http.lock').open('a') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX)
   # A different process may have filled this cache while we waited.
   if self.store:
    cached=self.store.cached(url,ttl)
    if cached is not None:return cached
   state=self._load();now=self.clock();day=datetime.fromtimestamp(now,timezone.utc).date().isoformat()
   if state.get('day')!=day:state.update(day=day,daily_requests=0)
   if now<state.get('cooldown_until',0):raise AirplayDeferred('Airplay cooldown active; cached observations retained')
   if state.get('daily_requests',0)>=self.daily_limit:raise AirplayDeferred('Conservative daily airplay budget reached; cached observations retained')
   wait=max(0,state.get('next_allowed',0)-now)
   if wait:self.sleep(min(wait,self.spacing));now=self.clock()
   if now<state.get('next_allowed',0):raise AirplayDeferred('Airplay request spacing active')
   state['daily_requests']=state.get('daily_requests',0)+1;state['next_allowed']=now+self.spacing
   self._save(state)
   req=Request(url,headers={'User-Agent':'HitDiscovery/0.7 private local radio','Accept':'application/json'})
   try:
    with self.opener(req,timeout=30) as response:payload=json.load(response)
   except HTTPError as error:
    if error.code==429:
     retry=error.headers.get('Retry-After','');delay=3600
     try:delay=max(30,float(retry))
     except ValueError:
      if retry:
       try:delay=max(30,parsedate_to_datetime(retry).timestamp()-now)
       except (ValueError,TypeError):pass
     strikes=state.get('consecutive_limits',0)+1
     state.update(last_status=429,consecutive_limits=strikes,cooldown_until=now+max(delay,min(86400,max(delay,300)*2**min(strikes-1,4))))
     self._save(state);raise AirplayDeferred('Provider rate limit: cooldown persisted; no immediate retry') from None
    state.update(last_status=error.code,cooldown_until=now+300);self._save(state);raise AirplayDeferred('Airplay provider unavailable; cached observations retained') from None
   except (OSError,TimeoutError,ValueError):
    state.update(last_status='connectivity',cooldown_until=now+300);self._save(state);raise AirplayDeferred('Airplay connection failed; retry deferred') from None
   state.update(last_status=200,last_success=self.clock(),consecutive_limits=0);self._save(state)
   if self.store:self.store.cache(url,payload)
   return payload

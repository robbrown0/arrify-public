"""SiriusXM-style local channels from authorized public airplay metadata."""
import fcntl,json,logging,re
from datetime import datetime,timezone
from .spectrum import merge,publish
log=logging.getLogger(__name__)
STATIONS=(('80son8','80s on 8','sirius-80son8'),('thebridge','The Bridge','sirius-thebridge'),('classicrewind','Classic Rewind','sirius-classicrewind'),('1stwave','1st Wave','sirius-1stwave'),('thepulse','The Pulse','sirius-thepulse'),('theblend','The Blend','sirius-theblend'))
HEADERS={'User-Agent':'HitDiscovery/0.7 private local radio'}
def normalize_rows(channel,rows):
 result=[]
 for row in rows:
  t=dict(row['track'])
  if channel=='80son8':
   t['broadcast_title']=t.get('broadcast_title',t['title'])
   t['title']=re.sub(r'\s+\((?:19)?[0-9]{2}\)\s*$','',t['title'])
  result.append(row|{'track':t})
 return result

def refresh(http,data,catalog,output_dir):
 from .airplay import AirplayClient,AirplayDeferred
 client=AirplayClient(http,data)
 from .station_subscriptions import refresh_directory,definitions,cache_artwork
 try:refresh_directory(client,data)
 except Exception:log.exception('Station directory refresh deferred; cached choices retained')
 try:feed=client.json('https://xmplaylist.com/api/feed',600)
 except AirplayDeferred as e:log.info('%s',e);feed={'results':[]}
 cache_artwork(data,output_dir)
 result=[]
 for channel,name,slug in definitions(data,STATIONS):
  try:
   url='https://xmplaylist.com/api/station/'+channel
   try:top=client.json(url+'/most-heard?days=60',86400)
   except AirplayDeferred as e:log.info('%s',e);top={'results':[]}
   recent=[r for r in feed.get('results',[]) if r.get('channelId')==channel]
   filename='station-'+channel+'.json';path=data/filename
   with (data/('station-'+channel+'.lock')).open('a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX)
    old=json.loads(path.read_text()) if path.exists() else {'results':[]}
    rows=merge(normalize_rows(channel,old['results']),normalize_rows(channel,top['results']+recent))
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps({'source':url,'results':rows,'updated':datetime.now(timezone.utc).isoformat()}));tmp.replace(path)
   out=publish(data,catalog,output_dir/(channel+'.json'),filename,slug,name,channel)
   result.append({'name':name,'observed':out['observed_songs'],'owned':out['owned_songs']})
  except Exception:log.exception('Station refresh failed: %s; previous observations retained',name)
 return result

def stored_candidates(data):
 from .station_subscriptions import definitions,acquisition_channels
 from .spectrum import candidates
 rows=[]
 for channel,name,slug in definitions(data,STATIONS):
  path=data/('station-'+channel+'.json')
  if not path.exists():continue
  for t in candidates(normalize_rows(channel,json.loads(path.read_text())['results'])):
   t['discovery_channel']=slug
   for e in t['evidence']:e['source']='xmplaylist observed '+name+' airplay';e['url']='https://xmplaylist.com/api/station/'+channel;e['chart_scope']='station'
   rows.append(t)
 if 'spectrum' in acquisition_channels(data):
  path=data/'spectrum-source.json'
  if path.exists():rows.extend(candidates(json.loads(path.read_text())['results']))
 return rows

def imported_candidates(data):
 path=data/'imported-playlists.json'
 if not path.exists():return []
 rows=[]
 for playlist in json.loads(path.read_text()):
  for position,e in enumerate(playlist['tracks'],1):
   if not e.get('creators'):continue
   rows.append({'artist':e['creators'][0]['name'],'title':e['name'],'album_hint':e.get('parent',{}).get('name',''),
    'discovery_channel':'imported-playlist','spotify_uri':e.get('uri'),'genres':['Jazz'],
    'evidence':[{'source':'User requested Spotify playlist: '+playlist['metadata']['name'],'url':playlist['metadata']['url'],
     'date':datetime.now(timezone.utc).date().isoformat(),'kind':'current','unranked':True,'weight':0,
     'playlist_position':position,'chart_scope':'user-playlist','independent_family':'Spotify playlist'}]})
 return rows

def publish_imports(data,catalog,output_dir):
 path=data/'imported-playlists.json'
 if not path.exists():return []
 result=[]
 for playlist in json.loads(path.read_text()):
  rows=[];seen=set()
  for e in playlist['tracks']:
   if e['uri'] in seen:continue
   seen.add(e['uri'])
   rows.append({'track':{'id':e['uri'],'title':e['name'],'artists':[a['name'] for a in e.get('creators',[])]}})
  source=data/'spotify-jazz-for-sleep-source.json'
  source.write_text(json.dumps({'source':playlist['metadata']['url'],'results':rows}))
  out=publish(data,catalog,output_dir/'jazz-for-sleep.json',source.name,'jazz-for-sleep',playlist['metadata']['name'],'jazz-for-sleep',section='moods',description='Imported from Jazzy Coffee’s Spotify playlist. Missing songs are awaiting album metadata and acquisition; only local matches can play.')
  result.append({'name':'Jazz for Sleep','source_entries':len(playlist['tracks']),'unique_songs':len(rows),'owned':out['owned_songs'],'pending':out['missing_songs']})
 return result

def backfill_one(http,data):
 """Resume one history page per hour; never run another bulk burst."""
 import time
 from urllib.parse import urlsplit,urlunsplit,parse_qs
 from .airplay import AirplayClient,AirplayDeferred
 from .spectrum import merge
 with (data/'airplay-backfill.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  state_path=data/'airplay-backfill.json';state=json.loads(state_path.read_text()) if state_path.exists() else {}
  now=time.time()
  if now<state.get('next_at',0):return 'scheduled'
  station=STATIONS[int(state.get('station_index',0))%len(STATIONS)][0]
  if station in state.get('complete',[]):
   state['station_index']=(int(state.get('station_index',0))+1)%len(STATIONS)
   tmp=state_path.with_suffix('.tmp');tmp.write_text(json.dumps(state));tmp.replace(state_path)
   return 'history for this station already complete'
  path=data/('station-'+station+'.json');old=json.loads(path.read_text()) if path.exists() else {'results':[]}
  cursor=state.get('cursors',{}).get(station)
  if not cursor:
   dates=[r.get('timestamp') for r in old['results'] if r.get('timestamp')]
   if dates:
    oldest=min(dates);moment=datetime.fromisoformat(oldest.replace('Z','+00:00'))
    if now-moment.timestamp()>29*86400:
     state.setdefault('complete',[]).append(station);state['station_index']=(int(state.get('station_index',0))+1)%len(STATIONS)
     tmp=state_path.with_suffix('.tmp');tmp.write_text(json.dumps(state));tmp.replace(state_path)
     return 'history window complete'
    cursor='https://xmplaylist.com/api/station/'+station+'?last='+str(int(moment.timestamp()*1000)-1)
   else:cursor='https://xmplaylist.com/api/station/'+station
  last=parse_qs(urlsplit(cursor).query).get('last',[])
  if last and now-float(last[0])/1000>29*86400:
   state.setdefault('complete',[]).append(station);state['station_index']=(int(state.get('station_index',0))+1)%len(STATIONS)
   tmp=state_path.with_suffix('.tmp');tmp.write_text(json.dumps(state));tmp.replace(state_path)
   return 'Saved cursor reached the public history boundary; no request sent'
  try:page=AirplayClient(http,data).json(cursor,86400*30)
  except AirplayDeferred as e:return str(e)
  with (data/('station-'+station+'.lock')).open('a') as station_lock:
   fcntl.flock(station_lock,fcntl.LOCK_EX)
   # Merge against current data again in case a feed refresh ran meanwhile.
   old=json.loads(path.read_text()) if path.exists() else {'results':[]}
   rows=merge(normalize_rows(station,old['results']),normalize_rows(station,page.get('results',[])))
   tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(old|{'results':rows}));tmp.replace(path)
  nxt=page.get('next')
  if nxt:
   parsed=urlsplit(nxt)
   if parsed.hostname!='xmplaylist.com':raise ValueError('Unexpected history cursor host')
   nxt=urlunsplit(parsed._replace(scheme='https'))
  state.setdefault('cursors',{})[station]=nxt
  if not nxt:state.setdefault('complete',[]).append(station)
  state.update(next_at=now+3600,station_index=(int(state.get('station_index',0))+1)%len(STATIONS))
  tmp=state_path.with_suffix('.tmp');tmp.write_text(json.dumps(state));tmp.replace(state_path)
  return station+': one page retained; next history fetch in one hour'


def publish_cached(data,catalog,output_dir):
 """Refresh availability from local metadata without making indexer/feed calls."""
 from .station_subscriptions import definitions
 for channel,name,slug in definitions(data,STATIONS):
  filename='station-'+channel+'.json'
  if (data/filename).exists():
   publish(data,catalog,output_dir/(channel+'.json'),filename,slug,name,channel)

"""Offline, isolated Unix service fixture. It never runs an acquisition worker."""
import os
import sys
sys.path.insert(0,'/app')
from pathlib import Path
from types import SimpleNamespace
from hitdiscovery import request_service as r
from hitdiscovery.store import Store
if os.environ.get('ARRIFY_REVIEW_ONLY')!='1':raise RuntimeError('Review-only fixture')
r.ROOT=Path('/requests')
for folder in ('inbox','status'):(r.ROOT/folder).mkdir(parents=True,exist_ok=True)
s=Store(r.ROOT/'discovery.sqlite3')
selection='11111111-1111-5111-8111-111111111111'
s.cache('arrify-selection:'+selection,{'selection':selection,'recording_id':selection,'title':'Admin fixture song','artist':'Fixture Artist','album':'Fixture Album','requestable':True})
external_selection='22222222-2222-5222-8222-222222222222'
external_item={'selection':external_selection,'recording_id':external_selection,'title':'A Missing Song','artist':'A Catalog Artist Not Owned','album':'Catalog Album','requestable':True}
s.cache('arrify-selection:'+external_selection,external_item)
s.db.close()
def offline_search(config,query,offset=0,recording_id=None,song=None):
    if song is not None:return {'items':[external_item] if song=={'artist':external_item['artist'],'title':external_item['title']} else [],'requested':song,'next_offset':None}
    item={'selection':selection,'recording_id':selection,'title':'Admin fixture song','artist':'Fixture Artist','album':'Fixture Album','requestable':True}
    items=[item] if recording_id==selection else [external_item] if recording_id==external_selection or query=='A Catalog Artist Not Owned' else []
    return {'items':items,'next_offset':None,'incomplete':False}
r.search=offline_search
sock=r.ROOT/'request.sock'
if sock.exists():sock.unlink()
server=r.UnixServer(str(sock),r.Handler);server.config=SimpleNamespace(data=r.ROOT,contact='https://example.invalid')
server.serve_forever()

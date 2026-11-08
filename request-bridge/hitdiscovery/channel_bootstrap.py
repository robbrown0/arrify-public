"""Bounded preference for verified songs in newly requested, sparse channels."""
import time
from .station_subscriptions import requested,read,slug

def sparse_channels(data,now=None):
    now=time.time() if now is None else now
    result=set()
    for item in requested(data):
        age=now-float(item.get('requested',0))
        if not 0<=age<7*86400:continue
        status=read(data/(item['id']+'-status.json'),{})
        if status.get('owned_songs',0)<20:
            result.add('spectrum' if item['id']=='thespectrum' else slug(item['id']))
    return result

def gain(group,channels):
    return len({t.get('recording_id') or (t.get('artist'),t.get('title')) for t in group.get('songs',[]) if t.get('discovery_channel') in channels})

def select(groups,channels,limit=3):
    return sorted((g for g in groups if gain(g,channels)),key=lambda g:(-gain(g,channels),-g.get('score',0),g['release_group_id']))[:max(0,limit)]

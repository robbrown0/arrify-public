"""Bounded, untrusted client observations; never used to authorize playback."""
import math
import re


def clean_buffer_report(value):
    if not isinstance(value,dict) or value.get('state') not in ('active','inactive'):return None
    def number(v,maximum):
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=maximum:raise ValueError()
        return int(v)
    try:
        items=value.get('items')
        if not isinstance(items,list) or len(items)>3:return None
        result=[]
        for item in items:
            if not isinstance(item,dict) or item.get('state') not in ('ready','downloading','retrying','partial','waiting','queued','oversized','budget','storage-full'):return None
            loaded=number(item.get('loaded_bytes'),256*1024*1024);total=number(item.get('total_bytes'),256*1024*1024)
            if loaded>total:return None
            result.append({'id':number(item.get('id'),2**53-1),'loaded_bytes':loaded,'total_bytes':total,'state':item['state'],'retries':number(item.get('retries',0),1000000)})
            if item.get('failure_reason') in ('unsupported-response','representation-changed','storage-incomplete','buffer-limit','download-failed'):
                result[-1]['failure_reason']=item['failure_reason']
        limit=number(value.get('limit_bytes',96*1024*1024),512*1024*1024)
        if limit not in (96*1024*1024,512*1024*1024) or sum(x['loaded_bytes'] for x in result)>limit:return None
        version=value.get('worker_version')
        identity={'worker_version':version} if isinstance(version,str) and re.fullmatch(r'[a-zA-Z0-9._-]{1,32}',version) else {}
        return {**identity,'storage':'disk' if value.get('storage')=='disk' else 'memory','state':value['state'],'downloaded_bytes':sum(x['loaded_bytes'] for x in result),'limit_bytes':limit,'items':result,'player_seconds':number(value.get('player_seconds',0),86400),'sample_age_seconds':number(value.get('sample_age_seconds',0),15)}
    except (ValueError,TypeError,OverflowError):return None

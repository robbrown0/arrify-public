"""A separate profile for new artists; never modify existing profiles."""
import copy
NAME='Discovery - Lossless preferred'

def preferred_profile(base):
    result=copy.deepcopy(base)
    result.pop('id',None)
    result['name']=NAME
    result['upgradeAllowed']=False
    allowed={'Lossless','High Quality Lossy','Mid Quality Lossy','Low Quality Lossy'}
    def apply(item,enabled=False):
        item['allowed']=item.get('name') in allowed or enabled
        for child in item.get('items',[]): apply(child,item['allowed'])
    for item in result['items']: apply(item)
    # Retain the existing profile's ordering: lossless above high/mid/low bitrate.
    return result

def ensure(lidarr,base_id):
    profiles=lidarr.get('qualityprofile')
    existing=next((p for p in profiles if p['name']==NAME),None)
    if existing: return existing['id']
    base=next(p for p in profiles if p['id']==base_id)
    profile=preferred_profile(base)
    return lidarr.write('qualityprofile','POST',profile)['id']

"""Fallback release types for new artists without editing existing profiles."""
import copy
NAME='Discovery - Albums EPs Singles'

def ensure(lidarr,base_id):
    profiles=lidarr.get('metadataprofile')
    existing=next((p for p in profiles if p['name']==NAME),None)
    if existing: return existing['id']
    profile=copy.deepcopy(next(p for p in profiles if p['id']==base_id))
    profile.pop('id',None); profile['name']=NAME
    for item in profile['primaryAlbumTypes']:
        if item['albumType']['name'] in ('Album','Single','EP'): item['allowed']=True
    return lidarr.write('metadataprofile','POST',profile)['id']

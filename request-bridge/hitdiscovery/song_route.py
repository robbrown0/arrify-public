"""Shared per-song status; album fallback is held only while song work is active."""
import json,time,os
from pathlib import Path

def load(root,rid):
    try:return json.loads((Path(root)/'song-routes'/(rid+'.json')).read_text())
    except (OSError,ValueError):return {}

def ensure(root,rid,rg):
    path=Path(root)/'song-routes'/(rid+'.json');path.parent.mkdir(exist_ok=True)
    try:
        with path.open('x') as f:json.dump({'phase':'pending','rg':rg,'updated_epoch':time.time()},f)
    except FileExistsError:pass
    return load(root,rid)

def active(route):
    if route.get('phase') in ('downloading','importing'):return True
    return route.get('phase') in ('pending','searching','awaiting-metadata') and time.time()-route.get('updated_epoch',0)<600

def hold_group(root,rg):
    for p in (Path(root)/'song-routes').glob('*.json'):
        try:r=json.loads(p.read_text())
        except (OSError,ValueError):continue
        if r.get('rg')==rg and active(r):return True
    return False

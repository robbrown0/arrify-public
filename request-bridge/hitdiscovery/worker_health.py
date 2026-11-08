"""Small atomic worker heartbeats; no credentials or song identities."""
import json
import time
import resource
from pathlib import Path

def beat(path,phase,**fields):
    path=Path(path)
    memory={'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    try:
        for line in Path('/proc/self/status').read_text().splitlines():
            if line.startswith('VmRSS:'):memory['rss_kib']=int(line.split()[1])
    except (OSError,ValueError):pass
    value={'at':time.time(),'phase':phase,**memory,**fields}
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value));tmp.replace(path)

def read(path,max_age=1200):
    try:
        value=json.loads(Path(path).read_text());age=max(0,time.time()-value['at'])
        return {**value,'age_seconds':round(age),'stale':age>max_age}
    except (OSError,ValueError,KeyError,TypeError):return {'phase':'starting','stale':True}

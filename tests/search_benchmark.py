"""Run only against a disposable, consistently copied Arrify catalog and read-only music mounts."""
import json
import statistics
import time
from app.library import Library

lib=Library('/data')
queries=['Tiny Dancer','Elton John','elton tiny','dancer water','AC/DC','acdc','Beyonce','Bill Monroe','Fleetwood Mac','Flleetwood Mac','Fleetwodo Mac','Oingo','love','zzzz no such song']
rows=[]
for q in queries:
    old_times=[];new_times=[]
    for _ in range(5):
        begin=time.perf_counter();old=lib.music._search(q,0,100);old_times.append((time.perf_counter()-begin)*1000)
        begin=time.perf_counter();new=lib.search.query(q,0,100);new_times.append((time.perf_counter()-begin)*1000)
    rows.append({'query':q,'old_total':old['total'],'new_total':new['total'],'old_median_ms':round(statistics.median(old_times),2),'new_median_ms':round(statistics.median(new_times),2),'fuzzy':new['fuzzy']})
print(json.dumps({'tracks':len(lib.tracks),'queries':rows},indent=2))

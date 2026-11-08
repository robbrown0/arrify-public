"""Explicit album requests outrank automatic discovery without broad catalog searches."""
from datetime import datetime, timezone

def explicit(store, rg):
    return bool(store.cached('arrify-priority:'+rg, ttl=86400*3650) and
                store.cached('arrify-authorization:'+rg, ttl=86400*3650))

def pending(store):
    for row in store.db.execute("SELECT key FROM cache WHERE key LIKE 'arrify-priority:%'").fetchall():
        rg=row['key'].split(':',1)[1]
        if not explicit(store,rg): continue
        # Priority markers are durable history, not permanent global stop signs.
        # Already-owned requests may never create a Lidarr request row at all.
        marker=store.cached('arrify-priority:'+rg,ttl=86400*3650) or {}
        try: seconds=(datetime.now(timezone.utc)-datetime.fromisoformat(marker.get('requested',''))).total_seconds()
        except (ValueError,TypeError): continue
        if seconds<0 or seconds>180: continue
        request=store.db.execute('SELECT state FROM requests WHERE rg=?',(rg,)).fetchone()
        if request and request['state']!='prepared': continue
        last=store.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='expansion_state'").fetchone()
        if last:
            import json
            stamp=store.db.execute('SELECT value FROM expansion_state WHERE key=?',('last-search:'+rg,)).fetchone()
            if stamp and (datetime.now(timezone.utc)-datetime.fromisoformat(json.loads(stamp[0]))).total_seconds()<1800: continue
        return True
    return False

def download_limit(store, config, rg):
    if explicit(store,rg): return config.concurrent_downloads
    if pending(store): return 0
    # Keep one lane available for a new interactive request; never interrupt a transfer.
    return max(1,config.concurrent_downloads-1)

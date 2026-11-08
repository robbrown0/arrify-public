"""Bounded, same-origin Cover Art Archive thumbnails for catalog release groups."""
from pathlib import Path
import os
import re
import threading
import time
import urllib.error
import urllib.request

ID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
SLOTS = threading.BoundedSemaphore(2)
FALLBACK = b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64"><rect width="64" height="64" rx="6" fill="#202820"/><circle cx="32" cy="32" r="20" fill="none" stroke="#8b9b8d" stroke-width="2"/><circle cx="32" cy="32" r="5" fill="#8b9b8d"/></svg>'


def thumbnail(group, root='/data/catalog-artwork'):
    if not ID.fullmatch(group):raise ValueError('Invalid release group')
    directory=Path(root);directory.mkdir(parents=True,exist_ok=True)
    target=directory/(group+'.jpg');missing=directory/(group+'.missing')
    if target.exists():return 'image/jpeg',target.read_bytes(),86400
    if missing.exists() and time.time()-missing.stat().st_mtime<86400:return 'image/svg+xml',FALLBACK,3600
    if not SLOTS.acquire(timeout=20):return 'image/svg+xml',FALLBACK,30
    try:
        if target.exists():return 'image/jpeg',target.read_bytes(),86400
        request=urllib.request.Request('https://coverartarchive.org/release-group/'+group+'/front-250',headers={'User-Agent':'Arrify/1.0 (https://github.com/robbrown0/arrify)'})
        with urllib.request.urlopen(request,timeout=8) as response:
            data=response.read(1024*1024+1)
        if len(data)>1024*1024 or not data.startswith(b'\xff\xd8\xff'):return 'image/svg+xml',FALLBACK,60
        # Bound storage to roughly 256 MB even for very large catalogs.
        entries=list(directory.glob('*.jpg'))
        if len(entries)>=256:
            for item in sorted(entries,key=lambda p:p.stat().st_mtime)[:len(entries)-255]:item.unlink(missing_ok=True)
        temporary=directory/(group+'.'+str(threading.get_ident())+'.tmp')
        temporary.write_bytes(data);os.replace(temporary,target)
        return 'image/jpeg',data,86400
    except urllib.error.HTTPError as error:
        if error.code==404:
            missing.touch()
            for item in directory.glob('*.missing'):
                if time.time()-item.stat().st_mtime>86400:item.unlink(missing_ok=True)
        return 'image/svg+xml',FALLBACK,3600 if error.code==404 else 60
    except (OSError,ValueError):return 'image/svg+xml',FALLBACK,60
    finally:SLOTS.release()

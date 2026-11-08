"""Administrator-owned source configuration; never writes to media directories."""
import json
import os
import shutil
import threading
import uuid
from pathlib import Path

MOUNTS = {'/mnt/nas-01-music':'/music/nas01', '/mnt/user/media/Music':'/music/unr01', '/srv/arrify-example/music-extra':'/music/extra'}
# Operators can expose additional read-only mounts without changing application code.
for host,container in json.loads(os.environ.get('ARRIFY_MUSIC_MOUNTS','{}')).items():
    if not isinstance(host,str) or not host.startswith('/') or not isinstance(container,str) or not container.startswith('/music/'):
        raise ValueError('Music mounts must map absolute host paths to folders under /music/.')
    MOUNTS[host]=container
DEFAULTS = [{'id':'nas01','name':'NAS music','path':'/mnt/nas-01-music'}, {'id':'unr01','name':'Music','path':'/mnt/user/media/Music'}]

class Locations:
    def __init__(self, library, directory):
        self.lib=library
        self.file=Path(directory)/'locations.json'
        self.lock=threading.RLock()
        self.running=False
        self.again=False
        self.error=False
        self.state=json.loads(self.file.read_text()) if self.file.exists() else {'revision':0,'items':[dict(x) for x in DEFAULTS]}
        self.apply()

    @staticmethod
    def resolve(path, require=True):
        if not isinstance(path,str) or not path.startswith('/') or len(path)>1024 or '\x00' in path or '..' in Path(path).parts:
            raise ValueError('Enter an absolute music folder path without parent-directory shortcuts.')
        path=str(Path(path))
        for host,container in MOUNTS.items():
            if path==host or path.startswith(host+'/'):
                root=Path(container).resolve()
                target=(root/Path(path).relative_to(host)).resolve()
                if not target.is_relative_to(root):raise ValueError('The folder must stay inside its mounted music location.')
                if require and (not target.is_dir() or not os.access(target,os.R_OK|os.X_OK)):
                    raise ValueError('That music folder is unavailable or unreadable.')
                return str(target)
        raise ValueError('This path needs a read-only Docker mount before Arrify can use it. Choose a folder inside a mounted location shown below.')

    def apply(self):
        m=self.lib.music
        roots={x['id']:self.resolve(x['path'],False) for x in self.state['items']}
        with m.lock:
            old=m.base_sources
            m.base_sources=roots
            for key,path in old.items():
                if roots.get(key)!=path:m.db.execute('UPDATE tracks SET available=0 WHERE source=?',(key,))
            # Include previously removed sources after restart without deleting catalog history.
            for row in m.db.execute('SELECT DISTINCT source FROM tracks').fetchall():
                if row[0] not in m._all_sources():m.db.execute('UPDATE tracks SET available=0 WHERE source=?',(row[0],))
        self.lib.reindex()

    @staticmethod
    def disk(path):
        try:
            usage=shutil.disk_usage(path)
            return {'free_bytes':usage.free,'total_bytes':usage.total}
        except OSError:return {'free_bytes':None,'total_bytes':None}

    def public(self):
        with self.lock:
            source_items=[dict(x) for x in self.state['items']];revision=self.state['revision'];running=self.running;error=self.error
        with self.lib.music.lock:
            counts={r[0]:(r[1],r[2] or 0) for r in self.lib.music.db.execute('SELECT source,count(*),sum(size) FROM tracks WHERE available=1 GROUP BY source')}
            running=running or getattr(self.lib.music,'scan_running',False)
        items=[]
        for x in source_items:
            path=self.resolve(x['path'],False)
            items.append(dict(x,container_path=path,available=Path(path).is_dir() and os.access(path,os.R_OK|os.X_OK),tracks=counts.get(x['id'],(0,0))[0],indexed_bytes=counts.get(x['id'],(0,0))[1],**self.disk(path)))
        mappings=[{'host':host,'container':container,'available':Path(container).is_dir() and os.access(container,os.R_OK|os.X_OK),**self.disk(container)} for host,container in MOUNTS.items()]
        return {'revision':revision,'items':items,'mounts':list(MOUNTS),'mappings':mappings,'scanning':running,'scan_error':error}

    def folders(self,path='',filter_text=''):
        if not path:return {'path':'','parent':None,'items':[{'name':host,'path':host} for host in MOUNTS],'has_more':False}
        target=self.resolve(path);host_path=str(Path(path));root=next(h for h in MOUNTS if host_path==h or host_path.startswith(h+'/'))
        items=[];needle=str(filter_text).casefold()[:80]
        with os.scandir(target) as entries:
            for entry in entries:
                try:
                    if entry.name.startswith('.') or entry.is_symlink() or not entry.is_dir(follow_symlinks=False) or needle not in entry.name.casefold():continue
                    items.append({'name':entry.name,'path':host_path.rstrip('/')+'/'+entry.name})
                except OSError:continue
        items.sort(key=lambda x:x['name'].casefold())
        return {'path':host_path,'parent':str(Path(host_path).parent) if host_path!=root else '', 'items':items[:200],'has_more':len(items)>200}

    def edit(self,data):
        with self.lock:
            if data.get('revision')!=self.state['revision']:raise ValueError('Locations changed. Refresh this page before saving.')
            action=data.get('action')
            if action=='scan':self.refresh();return self.public()
            if action not in ('save','remove'):raise ValueError('Unknown location action.')
            items=[dict(x) for x in self.state['items']]
            key=data.get('id')
            current=next((x for x in items if x['id']==key),None)
            if key and not current:raise ValueError('Music location not found.')
            if action=='remove':
                if not current:raise ValueError('Choose a music location.')
                items.remove(current)
            else:
                name=data.get('name');path=data.get('path')
                if not isinstance(name,str) or not 1<=len(name.strip())<=80:raise ValueError('Give this location a name of up to 80 characters.')
                target=self.resolve(path)
                for item in items:
                    if item is current:continue
                    other=self.resolve(item['path'],False)
                    if target==other or target.startswith(other+'/') or other.startswith(target+'/'):
                        raise ValueError('That folder overlaps an existing music location.')
                if len(items)>=32 and not current:raise ValueError('Up to 32 music locations are supported.')
                entry={'id':key or 'loc_'+uuid.uuid4().hex[:16],'name':name.strip(),'path':str(Path(path))}
                if current:items[items.index(current)]=entry
                else:items.append(entry)
            state={'revision':self.state['revision']+1,'items':items}
            temporary=self.file.with_suffix('.tmp')
            with open(temporary,'w',opener=lambda p,f:os.open(p,f,0o600)) as f:
                json.dump(state,f);f.flush();os.fsync(f.fileno())
            os.replace(temporary,self.file)
            self.state=state
            self.apply()
            self.refresh()
            return self.public()

    def refresh(self):
        with self.lock:
            if self.running:self.again=True;return
            self.running=True
            threading.Thread(target=self._worker,daemon=True).start()

    def _worker(self):
        while True:
            try:
                with self.lib.refresh_lock:
                    result=self.lib.music.scan()
                    self.lib.reindex()
                self.error=bool(result.get('errors'))
            except Exception:self.error=True
            with self.lock:
                if self.again:self.again=False;continue
                self.running=False
                return

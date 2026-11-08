"""Content-addressed public JS/CSS; HTML and unversioned URLs remain fresh."""
from collections import OrderedDict
import hashlib
from pathlib import Path
import re
import threading

class WebAssets:
    def __init__(self, root, capacity=64):
        self.root=Path(root);self.capacity=capacity;self.cache=OrderedDict();self.lock=threading.RLock()

    def asset(self, name):
        if not re.fullmatch(r'[a-z0-9-]+\.(js|css)',name):raise ValueError('Invalid asset')
        path=self.root/name
        with self.lock:
            stat=path.stat();stamp=(stat.st_ino,stat.st_mtime_ns,stat.st_size)
            item=self.cache.get(name)
            if item is None or item[0]!=stamp:
                body=path.read_bytes();item=(stamp,body,hashlib.sha256(body).hexdigest()[:20]);self.cache[name]=item
            self.cache.move_to_end(name)
            while len(self.cache)>self.capacity:self.cache.popitem(last=False)
            return item[1],item[2]

    def html(self, body):
        def replace(match):
            name=match.group(2)
            if name=='sw.js' or not (self.root/name).is_file():return match.group(0)
            return match.group(1)+'/'+name+'?v='+self.asset(name)[1]+match.group(3)
        return re.sub(r'((?:src|href)=[\"\'])/([a-z0-9-]+\.(?:js|css))([\"\'])',replace,body.decode()).encode()

    def response(self, filename, version=None):
        if filename.endswith('.html'):
            return self.html((self.root/filename).read_bytes()),{'Cache-Control':'no-store'}
        if filename.endswith(('.js','.css')):
            body,digest=self.asset(filename)
            # Never make a stale/made-up version immutable or cache the SW script.
            policy='public, max-age=31536000, immutable' if version==digest and filename!='sw.js' else 'no-store'
            return body,{'Cache-Control':policy}
        return (self.root/filename).read_bytes(),{'Cache-Control':'no-store'}

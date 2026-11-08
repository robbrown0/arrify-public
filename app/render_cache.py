"""Bounded, disposable seekable browser renditions; source audio stays untouched."""
from contextlib import contextmanager
from .media_identity import source_revision
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading

class RenderBusy(Exception):pass

class RenderCache:
    def __init__(self, directory, limit=512*1024*1024):
        self.root=Path(directory)/'stream-cache'
        self.limit=limit
        self.lock=threading.Lock()
        self.render_locks={}
        self.default_slots=threading.BoundedSemaphore(2)

    @contextmanager
    def render_guard(self,target):
        # Coalesce the same rendition without serializing unrelated songs.
        with self.lock:
            entry=self.render_locks.setdefault(str(target),[threading.Lock(),0]);entry[1]+=1
        try:
            with entry[0]:yield
        finally:
            with self.lock:
                entry[1]-=1
                if not entry[1]:self.render_locks.pop(str(target),None)

    def target(self, filename, profile, gain):
        key=hashlib.sha256(json.dumps([source_revision(filename),profile,gain,'v2']).encode()).hexdigest()
        self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        return self.root/(key+('.m4a' if profile=='aac' else '.mp3'))

    def cached(self, target):
        with self.lock:
            if not target.exists():return False
            os.utime(target,None)
            return True

    @contextmanager
    def open(self, filename, profile, gain, slots, ready_only=False):
        target=self.target(filename,profile,gain)
        while True:
            # Pin an open descriptor before eviction can unlink the path. It
            # stays readable for the whole response, including during pruning.
            with self.lock:
                try:
                    audio=target.open('rb')
                    os.utime(target,None)
                except FileNotFoundError:audio=None
            if audio is not None:break
            if ready_only:raise RenderBusy()
            self.render(filename,profile,gain,slots)
        try:yield audio
        finally:audio.close()

    def render(self, filename, profile, gain, slots=None):
        target=self.target(filename,profile,gain)
        if self.cached(target):return target
        # Only cache misses wait for a render. Cache hits never take a
        # conversion slot or the long-lived render lock.
        with self.render_guard(target):
            if self.cached(target):return target
            slots=slots if slots is not None else self.default_slots
            if not slots.acquire(blocking=False):raise RenderBusy()
            temporary=target.with_suffix('.tmp')
            args=['ffmpeg','-v','error','-nostdin','-y','-threads','1','-filter_threads','1','-i',str(filename),'-vn','-sn','-dn','-map_metadata','-1','-ac','2']
            if gain:args+=['-af','volume='+str(gain)+'dB']
            args+=['-c:a','aac' if profile=='aac' else 'libmp3lame','-b:a','192k','-threads','1','-fs',str(128*1024*1024)]
            if profile=='aac':args+=['-movflags','+faststart']
            args+=['-f','mp4' if profile=='aac' else 'mp3',str(temporary)]
            try:
                subprocess.run(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=120,check=True)
                if self.target(filename,profile,gain)!=target:raise ValueError('Source changed during preparation.')
                size=temporary.stat().st_size
                if not size or size>=128*1024*1024:raise ValueError('Song exceeds compatible playback size limit.')
                with self.lock:
                    files=sorted((p for p in self.root.iterdir() if p.suffix!='.tmp' and p.is_file()),key=lambda p:p.stat().st_mtime)
                    total=sum(p.stat().st_size for p in files)
                    for old in files:
                        if total+size<=self.limit:break
                        total-=old.stat().st_size;old.unlink()
                    os.chmod(temporary,0o600);os.replace(temporary,target)
                return target
            finally:
                temporary.unlink(missing_ok=True)
                slots.release()

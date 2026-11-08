"""Measured, cached playback gain. Media and embedded tags are strictly read-only."""
import json
import math
import queue
import re
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from app.errors import CatalogError

TARGET=-16.0
CEILING=-1.0

def safe_gain(lufs,peak):
    if not math.isfinite(lufs) or not math.isfinite(peak):return 0.0
    return round(max(-60.0,min(12.0,TARGET-lufs,CEILING-peak)),3)

class Sound:
    def __init__(self,directory,library,accounts):
        self.lib,self.accounts=library,accounts
        self.lock=threading.RLock()
        self.db=sqlite3.connect(str(Path(directory)/'sound.db'),check_same_thread=False,isolation_level=None)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS measurements(track_id INTEGER,modified_ns INTEGER,size INTEGER,lufs REAL,peak REAL,duration REAL,failed INTEGER,analyzed INTEGER,PRIMARY KEY(track_id,modified_ns,size))')
        with accounts.lock:
            accounts.db.execute("CREATE TABLE IF NOT EXISTS sound_preferences(user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,leveling TEXT NOT NULL DEFAULT 'off',gapless INTEGER NOT NULL DEFAULT 0)")
        self.jobs=queue.Queue(maxsize=128)
        self.pending=set()
        threading.Thread(target=self._worker,daemon=True).start()

    def preferences(self,uid,data=None):
        with self.accounts.lock:
            if data is not None:
                level=data.get('leveling');gapless=data.get('gapless')
                if level not in ('off','track','album') or not isinstance(gapless,bool):raise ValueError('Choose a valid sound setting.')
                self.accounts.db.execute('INSERT INTO sound_preferences VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET leveling=excluded.leveling,gapless=excluded.gapless',(uid,level,int(gapless)))
            row=self.accounts.db.execute('SELECT * FROM sound_preferences WHERE user_id=?',(uid,)).fetchone()
            return {'leveling':row['leveling'] if row else 'off','gapless':bool(row['gapless']) if row else False,'target_lufs':TARGET,'peak_ceiling_dbtp':CEILING}

    def key(self,tid):
        row,path=self.lib.music._track_file(tid)
        return (int(tid),int(row['modified_ns']),int(row['size'])),path

    def cached(self,tid,enqueue=True):
        key,path=self.key(tid)
        with self.lock:
            row=self.db.execute('SELECT * FROM measurements WHERE track_id=? AND modified_ns=? AND size=?',key).fetchone()
            if row and (not row['failed'] or time.time()-row['analyzed']<3600):return dict(row)
            if enqueue and key not in self.pending:
                try:self.jobs.put_nowait((key,path));self.pending.add(key)
                except queue.Full:pass
        return None

    def status(self,tid,mode):
        if mode not in ('off','track','album'):raise ValueError('Invalid leveling mode.')
        track=self.lib.track(tid)
        if mode=='off':return {'status':'off','gain_db':0,'mode':'off'}
        ids=[tid]
        if mode=='album':
            with self.lib.index_lock:ids=list(self.lib.albums[track['album_id']]['track_ids'])
            if len(ids)>100:return {'status':'unavailable','gain_db':0,'mode':mode}
        try:measurements=[self.cached(i) for i in ids]
        except (OSError,ValueError,CatalogError):return {'status':'unavailable','gain_db':0,'mode':mode}
        if any(m and m['failed'] for m in measurements):return {'status':'unavailable','gain_db':0,'mode':mode}
        if any(m is None for m in measurements):return {'status':'analyzing','gain_db':0,'mode':mode,'ready':sum(m is not None for m in measurements),'total':len(ids)}
        # A single gain for the album preserves quiet/loud track relationships.
        # Duration-weighted track loudness is an estimate, not a concatenated R128 measurement.
        valid=[m for m in measurements if m['lufs'] is not None and m['peak'] is not None and m['duration']>0]
        if not valid:return {'status':'silent','gain_db':0,'mode':mode}
        duration=sum(m['duration'] for m in valid)
        lufs=10*math.log10(sum(m['duration']*10**(m['lufs']/10) for m in valid)/duration)
        peak=max(m['peak'] for m in valid)
        return {'status':'ready','gain_db':safe_gain(lufs,peak),'mode':mode,'lufs':round(lufs,2),'peak_dbtp':peak,'album_estimate':mode=='album'}

    def _worker(self):
        while True:
            key,path=self.jobs.get()
            try:
                result=subprocess.run(['ffmpeg','-hide_banner','-nostats','-nostdin','-threads','1','-filter_threads','1','-i',path,'-vn','-sn','-dn','-af','loudnorm=I=-16:TP=-1:LRA=50:print_format=json','-f','null','-'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=600,check=True)
                # FFmpeg's summary is small; never retain filenames or stderr in the cache/API.
                blocks=re.findall(rb'\{[^{}]*"input_i"[^{}]*\}',result.stderr)
                values=json.loads(blocks[-1]);lufs=float(values['input_i']);peak=float(values['input_tp'])
                duration=float(self.lib.technical(key[0])['duration'])
                if not math.isfinite(lufs):lufs=None
                if not math.isfinite(peak):peak=None
                current,_=self.key(key[0])
                if current==key:
                    with self.lock:self.db.execute('INSERT OR REPLACE INTO measurements VALUES(?,?,?,?,?,?,0,?)',(*key,lufs,peak,duration,int(time.time())))
            except Exception:
                with self.lock:self.db.execute('INSERT OR REPLACE INTO measurements VALUES(?,?,?,NULL,NULL,0,1,?)',(*key,int(time.time())))
            finally:
                with self.lock:self.pending.discard(key)
                self.jobs.task_done()

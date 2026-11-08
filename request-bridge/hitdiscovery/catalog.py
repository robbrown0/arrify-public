"""Reuse the existing private player's embedded-tag index, read-only.
No player settings, files, or databases are modified. Normal file stat validation
still runs before cached metadata is used. Newly changed files are parsed directly.
"""
import json
import os
import sqlite3
from pathlib import Path
from .store import now

def seed(store,path,source_map):
    if not path: return 0
    conn=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    count=0; visited=0
    try:
        for r in conn.execute('''SELECT t.source,t.relative_path,t.size,t.modified_ns,m.title,m.artist,m.album,m.year,m.confidence
             FROM tracks t JOIN track_metadata m ON m.track_id=t.id
             WHERE t.available=1 AND m.file_modified_ns=t.modified_ns AND m.title<>'' AND m.artist<>'' '''):
            source,relative,size,modified,title,artist,album,year,confidence=r
            if source not in source_map: continue
            root=Path(source_map[source]).resolve(); target=(root/relative).resolve()
            if not target.is_relative_to(root): continue
            payload={'artist':artist,'title':title,'album':album,'date':year,'recording_ids':[],'isrcs':[],
                     'album_ids':[],'genres':[],'path':str(target),'owned':True,'metadata_origin':'existing-embedded-tag-index','confidence':confidence}
            cur=store.db.execute('INSERT OR IGNORE INTO files VALUES(?,?,?,?,?)',(str(target),size,modified,json.dumps(payload),now()))
            count+=cur.rowcount; visited+=1
            if visited%250==0: store.db.commit()
        store.db.commit()
    finally: conn.close()
    return count

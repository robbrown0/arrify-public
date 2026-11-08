import json
import os
import tempfile
from collections import Counter
from .store import now

AUDIO={'.mp3','.flac','.m4a','.ogg','.opus','.wav','.wma','.aiff','.ape','.wv'}

def scan_files(store, roots):
    from mutagen import File
    stamp=now(); result=[]; errors=[]; count=0
    for root in roots:
        if not os.path.isdir(root): raise RuntimeError(f'Ownership path unavailable: {root}')
        def walk_error(error):
            raise RuntimeError(f'Ownership directory unreadable: {error.filename}')
        for directory,dirs,files in os.walk(root,onerror=walk_error):
            dirs[:]=[d for d in dirs if d not in ('#recycle','@eaDir','.Trash')]
            for filename in files:
                if os.path.splitext(filename)[1].lower() not in AUDIO: continue
                path=os.path.join(directory,filename); st=os.stat(path); count+=1
                row=store.db.execute('SELECT * FROM files WHERE path=?',(path,)).fetchone()
                if row and row['size']==st.st_size and row['mtime']==st.st_mtime_ns:
                    payload=json.loads(row['payload'])
                else:
                    try:
                        f=File(path,easy=True); tags=f.tags if f else None
                        def values(k): return list(tags.get(k,[])) if tags else []
                        artists=values('artist') or values('albumartist')
                        names=values('title')
                        payload={'artist':artists[0] if artists else '', 'title':names[0] if names else '',
                                 'album':next(iter(values('album')),''),'recording_ids':values('musicbrainz_trackid'),
                                 'isrcs':values('isrc'),'album_ids':values('musicbrainz_releasegroupid'),
                                 'date':next(iter(values('originaldate') or values('date')),''), 'genres':values('genre'),
                                 'path':path,'owned':True}
                    except Exception as e:
                        payload={'artist':'','title':'','path':path,'tag_error':type(e).__name__,'owned':True}
                if not payload.get('artist') or not payload.get('title'):
                    errors.append(path)
                store.db.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?,?)', (path,st.st_size,st.st_mtime_ns,json.dumps(payload),stamp))
                result.append(payload)
                if count%250==0: store.db.commit()
                if count%5000==0:
                    store.db.commit(); print(f'Indexed {count} audio files',flush=True)
    store.db.execute('DELETE FROM files WHERE seen<>?',(stamp,)); store.db.commit()
    return result, {'audio_files':count,'missing_or_unreadable_tags':len(errors),'examples':errors[:20]}

def scan(lidarr,store,roots):
    from .worker_health import beat
    from pathlib import Path
    metrics=Path(store.db.execute('PRAGMA database_list').fetchone()[2]).parent/'inventory-memory.json'
    beat(metrics,'inventory-start')
    artists=lidarr.get('artist'); albums=lidarr.get('album'); tracks=[]
    track_count=file_count=0
    # Spool complete audit snapshots to disk. Keep only compact owned records
    # in memory for matching; never retain all missing catalog tracks.
    with tempfile.TemporaryFile(mode='w+t', dir=metrics.parent) as track_rows, tempfile.TemporaryFile(mode='w+t', dir=metrics.parent) as file_rows:
        for artist in artists:
            at=lidarr.get('track',artistId=artist['id'])
            track_count+=len(at)
            for t in at:
                row={**t,'artist':artist['artistName'],'owned':bool(t.get('hasFile') or t.get('trackFileId')),
                     'recording_ids':[t.get('foreignRecordingId')] if t.get('foreignRecordingId') else [],'isrcs':t.get('isrcs',[])}
                track_rows.write(json.dumps(row)+'\n')
                if row['owned']:
                    tracks.append({k:row[k] for k in ('id','artistId','artist','title','album','owned','recording_ids','isrcs') if k in row})
            del at
            af=lidarr.get('trackfile',artistId=artist['id']);file_count+=len(af)
            for row in af:file_rows.write(json.dumps(row)+'\n')
            del af
        for kind,rows in [('artist',artists),('album',albums)]:store.inventory(kind,rows)
        for kind,stream in [('track',track_rows),('trackfile',file_rows)]:
            stream.seek(0);store.inventory(kind,(json.loads(line) for line in stream))
    local,stats=scan_files(store,roots)
    owned=[t for t in tracks if t['owned']]
    artist_map={a['id']:a for a in artists}
    downloaded=[a for a in albums if a.get('statistics',{}).get('trackFileCount',0)>0]
    incomplete=[{'artist':artist_map[a['artistId']]['artistName'],'album':a['title'],'id':a['id'],
                 'monitored':a['monitored'],**a.get('statistics',{})} for a in albums
                if 0<a.get('statistics',{}).get('trackFileCount',0)<a.get('statistics',{}).get('totalTrackCount',0)]
    summary={'lidarr_artists_cataloged':len(artists),'lidarr_artists_with_files':len({t['artistId'] for t in owned}),
             'lidarr_albums_cataloged':len(albums),'lidarr_albums_with_files':len(downloaded),
             'lidarr_owned_tracks':len(owned),'lidarr_track_files':file_count,'lidarr_catalog_tracks':track_count,
             'monitored_empty_albums':sum(a['monitored'] and not a.get('statistics',{}).get('trackFileCount',0) for a in albums),
             'release_years':dict(sorted(Counter(a.get('releaseDate','unknown')[:4] for a in downloaded).items())),
             'genres':dict(Counter(g for a in downloaded for g in a.get('genres',[])).most_common()),
             'incomplete_albums':incomplete,'filesystem':stats,
             'filesystem_release_years':dict(sorted(Counter(t.get('date','unknown')[:4] or 'unknown' for t in local).items())),
             'filesystem_genres':dict(Counter(g for t in local for g in t.get('genres',[])).most_common()),
             'filesystem_unique_artist_names':len({t['artist'] for t in local if t['artist']}),
             'filesystem_unique_album_names':len({(t['artist'],t.get('album')) for t in local if t.get('album')})}
    beat(metrics,'inventory-complete',catalog_tracks=track_count,retained_owned_tracks=len(tracks),track_files=file_count)
    return artists,albums,tracks+local,summary

"""Private account summaries from saved playback history, never estimated listening time."""
from collections import Counter
import time


def summary(accounts, library, uid, now=None):
    end=int(time.time() if now is None else now);start=end-7*86400
    with accounts.lock:
        native=accounts.db.execute('SELECT track_id,count(*) n FROM history WHERE user_id=? AND played_at>=? AND played_at<=? GROUP BY track_id',(uid,start,end)).fetchall()
        recent=accounts.db.execute('SELECT track_id,max(played_at) played_at FROM history WHERE user_id=? GROUP BY track_id ORDER BY played_at DESC LIMIT 50',(uid,)).fetchall()
        playlists=accounts.db.execute("SELECT count(*) total,coalesce(sum(visibility='shared'),0) shared FROM personal_playlists WHERE user_id=?",(uid,)).fetchone()
        likes=accounts.db.execute('SELECT count(*) FROM favorites WHERE user_id=?',(uid,)).fetchone()[0]
        apple=[];apple_recent=[]
        if accounts.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='apple_music_history'").fetchone():
            apple=accounts.db.execute('SELECT artist,count(*) n FROM apple_music_history WHERE user_id=? AND started>=? AND started<=? GROUP BY artist',(uid,start,end)).fetchall()
            apple_recent=accounts.db.execute('SELECT title,artist,started FROM apple_music_history WHERE user_id=? ORDER BY started DESC LIMIT 50',(uid,)).fetchall()
    artists=Counter();names={};items=[]
    def add_artist(name,count):
        if name and name.strip():key=name.strip().casefold();names.setdefault(key,name.strip());artists[key]+=count
    for row in native:
        if row['track_id'] in library.tracks:add_artist(library.track(row['track_id']).get('artist',''),row['n'])
    for row in apple:add_artist(row['artist'],row['n'])
    for row in recent:
        if row['track_id'] in library.tracks:
            track=library.track(row['track_id']);items.append(dict(title=track.get('title',''),artist=track.get('artist',''),played_at=row['played_at'],source='arrify',track_id=row['track_id']))
    items.extend(dict(title=r['title'],artist=r['artist'],played_at=r['started'],source='apple') for r in apple_recent)
    best=sorted(artists,key=lambda key:(-artists[key],key))[:1]
    return dict(period='Last 7 days',starts=sum(r['n'] for r in native)+sum(r['n'] for r in apple),top_artist=names[best[0]] if best else None,top_artist_starts=artists[best[0]] if best else 0,playlists=playlists['total'],shared_playlists=playlists['shared'],liked_songs=likes,recent=sorted(items,key=lambda r:r['played_at'],reverse=True)[:50])

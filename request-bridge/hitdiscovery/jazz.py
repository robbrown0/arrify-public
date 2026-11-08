"""Publish explicit jazz album curation as local collections as files arrive."""
import json
from .spectrum import publish

def publish_collections(data,catalog,output):
    from .store import Store
    path=data/'jazz-targets.json'
    if not path.exists():return []
    s=Store(data/'discovery.sqlite3');buckets={}
    try:
        for song in json.loads(path.read_text()):
            row=s.db.execute('SELECT payload FROM cache WHERE key LIKE ? LIMIT 1',
                ('https://musicbrainz.org/ws/2/release/'+song['release_id']+'?%',)).fetchone()
            if not row:continue
            release=json.loads(row[0]);mood=song['genres'][-1]
            for medium in release.get('media',[]):
                for track in medium.get('tracks',[]):
                    title=track.get('recording',{}).get('title') or track.get('title')
                    if title:buckets.setdefault(mood,[]).append({'track':{'title':title,'artists':[song['artist']]}})
    finally:s.db.close()
    names={'smooth':'Smooth Jazz','piano':'Piano Jazz','acoustic':'Acoustic Jazz','sleep':'Jazz for Sleep — Quiet Albums','essentials':'Jazz Essentials'}
    results=[]
    for mood,rows in buckets.items():
        slug='curated-jazz-'+mood;source=data/(slug+'-source.json')
        source.write_text(json.dumps({'source':'https://musicbrainz.org/','results':rows}))
        report=publish(data,catalog,output/(slug+'.json'),source.name,slug,names[mood],slug,
            section='playlists' if mood=='essentials' else 'moods',
            description='Selected jazz albums. Newly acquired local songs are added automatically.')
        results.append({'name':names[mood],'owned':report['owned_songs'],'selected':report['observed_songs']})
    return results

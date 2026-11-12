"""Arrify library indexing, discovery, and playback queues."""
import collections
import hashlib
import json
import os
import re
import secrets
import threading
import time
import fcntl
from pathlib import Path
from app.catalog_engine import Catalog, CATALOG_COLUMNS, CATALOG_JOIN, _public_row
from tinytag import TinyTag
from app.errors import CatalogError

def verified_import_identity(row,proof):
    """Restore missing display tags only for an unchanged, verified imported file."""
    if not proof or row.get('metadata_source')!='path':return None
    if any(row.get(k)!=proof.get(k) for k in ('source','relative_path','modified_ns')):return None
    if not re.fullmatch(r'.+ - .+ - \d{1,3} - .+',row.get('title','')):return None
    return {'artist':proof['artist'],'title':proof['title']}

class Library:
    def __init__(self, directory):
        self.music = Catalog(str(Path(directory) / 'catalog.db'),
            {'nas01': '/music/nas01', 'unr01': '/music/unr01'},
            '/music/extra', '/opt/arrify/music-playlists.json')
        # Coordinate complete folder snapshots with the existing tag/rename
        # worker so a stale scan cannot recreate an old path or hide a new one.
        original_scan = self.music.scan
        def coordinated_scan(*args, **kwargs):
            with (Path(directory) / 'library-file-mutation.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    return original_scan(*args, **kwargs)
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
        self.music.scan = coordinated_scan
        self.index_lock = threading.RLock()
        self.music.db.execute('CREATE TABLE IF NOT EXISTS arrify_audio_details(track_id INTEGER PRIMARY KEY, modified_ns INTEGER NOT NULL, payload TEXT NOT NULL)')
        from .metadata import Metadata
        self.metadata = Metadata(directory, self)
        self.reindex()
        self.refresh_error = False
        self.last_refresh_success = 0
        self.refresh_lock = threading.Lock()
        from .locations import Locations
        self.locations = Locations(self, directory)
        from .radio import Radio
        self.radio = Radio(directory, self)
        from .popularity import Popularity
        self.popularity = Popularity(directory, self.radio.similar._fetch, self.popularity_artists)
        from .browse import Browse
        self.discovery = Browse(directory, self, start=os.environ.get('ARRIFY_BROWSE_INDEX','1')=='1')
        self.metadata.start()

    def popularity_artists(self):
        with self.index_lock:
            return [a['name'] for a in sorted(self.artists.values(),key=lambda a:-len(a['track_ids']))]

    @staticmethod
    def key(*values):
        return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()[:24]

    def apply_recording_preferences(self):
        """Keep audited station recording choices across generated playlist refreshes."""
        path = Path('/data/playlist-recording-preferences.json')
        if not path.exists(): return
        preferences = json.loads(path.read_text())
        m = self.music
        with m.lock:
            for choice in preferences:
                target = int(choice['track_id'])
                row = m.db.execute('SELECT modified_ns,available FROM tracks WHERE id=?',(target,)).fetchone()
                if row is None or not row['available'] or row['modified_ns'] != choice['modified_ns']: continue
                for slug in choice['playlists']:
                    for old in choice['replaces']:
                        if old == target: continue
                        if not m.db.execute('SELECT 1 FROM playlist_tracks WHERE playlist_slug=? AND track_id=?',(slug,old)).fetchone(): continue
                        exists=m.db.execute('SELECT 1 FROM playlist_tracks WHERE playlist_slug=? AND track_id=?',(slug,target)).fetchone()
                        if exists:
                            m.db.execute('DELETE FROM playlist_tracks WHERE playlist_slug=? AND track_id=?',(slug,old))
                        else:
                            m.db.execute('UPDATE playlist_tracks SET track_id=? WHERE playlist_slug=? AND track_id=?',(target,slug,old))
                        weights=getattr(m,'channel_weights',{}).get(slug,{})
                        if old in weights: weights[target]=max(weights.get(target,0),weights.pop(old))
            m.db.commit()

    def reindex(self):
        self.apply_recording_preferences()
        m = self.music
        with m.lock:
            rows = m.db.execute('SELECT '+CATALOG_COLUMNS+CATALOG_JOIN+'WHERE t.available=1').fetchall()
            confirmed_art = {r[0]:r[1] for r in m.db.execute('SELECT artwork_key,image_sha256 FROM album_artwork')}
            quality = {r['track_id']: dict(r) for r in m.db.execute('SELECT * FROM track_technical')}
            memberships = [(r[0],r[1]) for r in m.db.execute('SELECT playlist_slug,track_id FROM playlist_tracks') if not m.dynamic_playlists.get(r[0])]
        overrides = self.metadata.snapshot() if hasattr(self, 'metadata') else {}
        exclusions=m._audio_exclusions() if hasattr(m,'_audio_exclusions') else {}
        cleanup_path = Path('/data/catalog-cleanup.json')
        cleanup = json.loads(cleanup_path.read_text()) if cleanup_path.exists() else {}
        proof_path=Path('/requests/song-routes/verified-recording-links.json')
        proof_items=json.loads(proof_path.read_text()).get('items',{}) if proof_path.exists() else {}
        import_proofs={(p['source'],p['relative_path'],p['modified_ns']):p for p in proof_items.values()}
        import unicodedata
        name_key = lambda value: ' '.join(unicodedata.normalize('NFKC', value or '').split()).casefold()
        canonical = {name_key(k):v for k,v in cleanup.get('names',{}).items()}
        # Keep future case-only variants together, too. Prefer the most common
        # existing spelling unless an audited canonical name is configured.
        spellings = collections.Counter(_public_row(row)['artist'] for row in rows)
        for value in overrides.values():
            name = value.get('values',{}).get('artist')
            if name: spellings[name] += 1
        for name, count in spellings.most_common():
            canonical.setdefault(name_key(name), name)
        fallback_path = Path('/data/song-artwork/artist-fallbacks.json')
        artwork_fallbacks = json.loads(fallback_path.read_text()) if fallback_path.exists() else {}
        artist_cover_versions = {p.stem:str(p.stat().st_mtime_ns) for p in Path('/data/artist-artwork').glob('*.jpg')}
        credits_path = Path('/data/artist-credits.json')
        verified_credits = json.loads(credits_path.read_text()) if credits_path.exists() else {}
        tracks, albums, artists = {}, {}, {}
        for row in rows:
            if exclusions.get((row['source'],row['relative_path'])) == (row['size'],row['modified_ns']):continue
            t = _public_row(row)
            override = overrides.get(t['id'])
            if override and override['modified'] == row['modified_ns']:
                values = override['values']
                for field in ('title','artist'):
                    if values.get(field): t[field] = values[field]
                if values.get('genres'): t['catalog_genres'] = values['genres']
                if values.get('year'):
                    t['catalog_year'] = values['year']; t['year'] = str(values['year']); t['catalog_date_source'] = 'researched'
                t['catalog_metadata_source'] = override['source']
            repair = cleanup.get('tracks',{}).get(str(t['id']))
            if (repair and repair['modified_ns'] == row['modified_ns']
                    and not override and row['artist'] == repair['original_artist'] and row['title'] == repair['original_title']):
                t['artist'], t['title'] = repair['artist'], repair['title']
                if repair.get('album') and repair.get('original_album') == row['album']:
                    t['album'] = repair['album']
                if 'album_artist' in repair: t['album_artist'] = repair['album_artist']
                if repair.get('metadata_source'):
                    t['catalog_metadata_source'] = repair['metadata_source']
                    t['isrc'] = repair.get('isrc', '')
                    if repair.get('year') and not t.get('year'):
                        t['year'] = str(repair['year']); t['catalog_year'] = int(repair['year'])
                        t['catalog_date_source'] = 'identified-release'
                    if repair.get('genres') and not t.get('catalog_genres'):
                        from .browse import genres as browse_genres
                        t['catalog_genres'] = sorted({g for label in repair['genres'] for g in browse_genres(str(label))})

            proof=import_proofs.get((row['source'],row['relative_path'],row['modified_ns']))
            restored=verified_import_identity(dict(row),proof) if not override else None
            if restored:
                t.update(restored);t['catalog_metadata_source']='verified-song-import'
            t['artist'] = canonical.get(name_key(t['artist'] or 'Unknown artist'), t['artist'] or 'Unknown artist')
            if t.get('album_artist'):
                t['album_artist'] = canonical.get(name_key(t['album_artist']), t['album_artist'])
            for field in ('source', 'size', 'extension', 'metadata_confidence', 'metadata_source', 'raw_artist', 'raw_title', 'raw_album'):
                t.pop(field, None)
            q = quality.get(t['id'], {})
            t['quality'] = 'Lossless' if q.get('lossless') else (f"{q['bitrate_kbps']} kbps" if q.get('bitrate_kbps') else str(q.get('codec', '')).upper())
            t['artwork'] = f"/api/v1/artwork/{t['id']}"
            art_identity = self.key(t['artist'], t['title'], t['album'])
            art_version = str(row['modified_ns']) + '-' + art_identity
            for art_directory in ('/data/recognition-artwork','/data/song-artwork'):
                art_file = Path(art_directory) / (str(t['id']) + '-' + art_version + '.jpg')
                if art_file.is_file():
                    t['artwork'] += '?v=' + art_version + '-' + str(art_file.stat().st_mtime_ns)
                    break
            credit = verified_credits.get(str(t['id']), {})
            credited_names = [t['artist']]
            if (credit.get('modified_ns') == row['modified_ns']
                    and all(credit.get(k) == t.get(k) for k in ('artist','title','album'))):
                primary = list(dict.fromkeys(canonical.get(name_key(n), n) for n in credit.get('primary', [])))
                featured = list(dict.fromkeys(canonical.get(name_key(n), n) for n in credit.get('featured', [])))
                if primary:
                    t['primary_artists'], t['featured_artists'] = primary, featured
                    t['artist_credit_source'] = credit.get('source', '')
                    credited_names = list(dict.fromkeys(primary + featured))
                    if credit.get('album_artist'): t['album_artist'] = credit['album_artist']
            t['artist_refs'] = [{'id':self.key(n), 'name':n} for n in credited_names]
            artist = t.get('album_artist') or t['artist'] or 'Unknown artist'
            aid, rid = self.key(artist, t['album']), t['artist_refs'][0]['id']
            if '?v=' not in t['artwork']:
                if rid in artist_cover_versions:
                    t['artwork'] += '?v=artist-' + artist_cover_versions[rid]
                elif rid in artwork_fallbacks:
                    t['artwork'] += '?v=related-' + self.key(artwork_fallbacks[rid])
            confirmed_version = next((confirmed_art[k] for k in Catalog._artwork_keys(row) if k in confirmed_art), None)
            if confirmed_version:
                t['artwork'] = f"/api/v1/artwork/{t['id']}?v=confirmed-{confirmed_version[:16]}"
            t.update(album_id=aid, artist_id=rid, artist_credit_id=self.key(t['artist']))
            tracks[t['id']] = t
            album = albums.setdefault(aid, {'id':aid, 'name':t['album'] or 'Unknown album', 'artist':artist, 'artwork':t['artwork'], 'year':t.get('year',''), 'track_ids':[]})
            album['track_ids'].append(t['id'])
            for ref in t['artist_refs']:
                artists.setdefault(ref['id'], {'id':ref['id'],'name':ref['name'],'artwork':t['artwork'],'track_ids':[]})['track_ids'].append(t['id'])
        # Use audited album covers without depending on track ordering.
        selection_path = Path('/data/album-artwork/selection.json')
        selections = json.loads(selection_path.read_text()) if selection_path.exists() else {}
        versions = {row['id']: row['modified_ns'] for row in rows}
        for aid, choice in selections.items():
            album = albums.get(aid)
            track = tracks.get(choice['track_id'])
            if (album and track and track['id'] in album['track_ids']
                    and versions.get(track['id']) == choice['modified_ns']
                    and self.key(track['artist'], track['title'], track['album']) == choice['identity']):
                album['artwork'] = track['artwork']
        # Prefer a locally cached, identity-matched artist portrait.
        for rid, artist in artists.items():
            if (Path('/data/artist-artwork') / (rid + '.jpg')).is_file():
                artist['artwork'] = '/api/v1/artist-artwork/' + rid + '?v=' + str((Path('/data/artist-artwork') / (rid + '.jpg')).stat().st_mtime_ns)
        membership_counts=collections.Counter(memberships)
        with self.index_lock:
            # Refreshing playlists or scanning unchanged files does not change
            # the derived catalog. Preserve search, radio and Browse caches.
            if (getattr(self,'tracks',None)==tracks and getattr(self,'albums',None)==albums
                    and getattr(self,'artists',None)==artists
                    and getattr(self,'_index_memberships',None)==membership_counts
                    and getattr(self,'artwork_fallbacks',None)==artwork_fallbacks):
                return
        from .search import Search
        search = Search(list(tracks.values()), list(albums.values()), list(artists.values()))
        from .radio import RadioIndex
        radio_index = RadioIndex(tracks, memberships)
        with self.index_lock:
            self.artwork_fallbacks = artwork_fallbacks
            self.radio_index = radio_index
            self.tracks, self.albums, self.artists = tracks, albums, artists
            self.search = search
            self._index_memberships = membership_counts
        # Drop obsolete generations only after a replacement is ready.
        if hasattr(self, 'radio'):
            with self.radio.lock: self.radio.pools.clear()
        if hasattr(self, 'discovery'):
            with self.discovery.lock: self.discovery.index = None; self.discovery.index_key = None

    def supplemental_artwork(self, track_id, directory='/data/song-artwork'):
        # A file-version-specific fallback never replaces embedded/folder art.
        with self.music.lock:
            row = self.music.db.execute('SELECT modified_ns FROM tracks WHERE id=? AND available=1',(int(track_id),)).fetchone()
        if row is None:
            return None
        with self.index_lock:
            track = self.tracks.get(int(track_id))
        if track is None:
            return None
        identity = self.key(track['artist'], track['title'], track['album'])
        image = Path(directory) / (str(int(track_id)) + '-' + str(row[0]) + '-' + identity + '.jpg')
        try:
            if not 100 < image.stat().st_size <= 5000000:
                return None
            data = image.read_bytes()
            return data if data.startswith(b'\xff\xd8') else None
        except FileNotFoundError:
            return None

    def related_artwork(self, track_id):
        """Last-resort artist image or same-artist album cover; never metadata."""
        with self.index_lock:
            track = self.tracks.get(int(track_id))
            if not track or track['artist'].casefold() in (
                    'unknown', 'unknown artist', 'various artists', 'various',
                    'music from old computer', 'compilations'):
                return None
            rid = track['artist_id']
            choices = list(getattr(self, 'artwork_fallbacks', {}).get(rid, []))
        image = Path('/data/artist-artwork') / (rid + '.jpg')
        try:
            if 100 < image.stat().st_size <= 5000000:
                data = image.read_bytes()
                if data.startswith(b'\xff\xd8'):
                    return 'image/jpeg', data
        except FileNotFoundError:
            pass
        for choice in choices:
            if choice['id'] == int(track_id):
                continue
            with self.index_lock:
                donor = self.tracks.get(choice['id'])
            if (not donor or donor['artist_id'] != rid
                    or self.key(donor['artist'], donor['title'], donor['album']) != choice['identity']):
                continue
            with self.music.lock:
                row = self.music.db.execute('SELECT modified_ns FROM tracks WHERE id=? AND available=1', (choice['id'],)).fetchone()
            if row is None or row[0] != choice['modified_ns']:
                continue
            try:
                mime, data, source = self.music.artwork(choice['id'])
                if source != 'fallback' and data:
                    return mime, data
            except (OSError, CatalogError):
                pass
            data = self.supplemental_artwork(choice['id'])
            if data is not None:
                return 'image/jpeg', data
        return None

    def track(self, track_id):
        with self.index_lock:
            if int(track_id) not in self.tracks:
                raise ValueError('Track unavailable.')
            return self.tracks[int(track_id)]

    def request_track_ready(self,track_id):
        try:
            row,filename=self.music._track_file(track_id)
            # Metadata ownership alone is insufficient: verify the file is readable.
            with open(filename,'rb') as audio:return bool(audio.read(1))
        except (CatalogError,OSError,ValueError):return False

    def technical(self, track_id):
        row,filename=self.music._track_file(track_id)
        with self.music.lock:
            saved=self.music.db.execute('SELECT payload FROM arrify_audio_details WHERE track_id=? AND modified_ns=?',(track_id,row['modified_ns'])).fetchone()
        quality=self.music.track_quality(track_id).get('label','')
        quality='Lossless' if quality=='LOSSLESS' else quality
        with self.index_lock:
            if track_id in self.tracks:self.tracks[track_id]['quality']=quality
        if saved:return json.loads(saved[0]) | {'quality':quality}
        tag=TinyTag.get(filename,image=False,duration=True)
        details={'duration':float(tag.duration or 0),'sample_rate':int(tag.samplerate or 0),'bit_depth':int(tag.bitdepth or 0)}
        with self.music.lock:
            self.music.db.execute('INSERT OR REPLACE INTO arrify_audio_details VALUES(?,?,?)',(track_id,row['modified_ns'],json.dumps(details)))
        return details | {'quality':quality}

    def collections(self):
        result = self.music.playlists()
        with self.music.lock:
            for p in result:
                rows = self.music.db.execute('''SELECT pt.track_id FROM playlist_tracks pt
                    JOIN tracks t ON t.id=pt.track_id WHERE pt.playlist_slug=? AND t.available=1
                    GROUP BY t.artist,t.album ORDER BY min(pt.position) LIMIT 4''',(p['slug'],)).fetchall()
                p['artworks']=[f"/api/v1/artwork/{row[0]}" for row in rows]
                p['artwork']=p['artworks'][0] if rows else None
                if p['slug']=='jazz-for-sleep':
                    p['description'] = 'Spotify metadata snapshot · 161 entries / 148 unique songs. Only locally matched songs can play.'
        from .channels import present_channels
        return present_channels(result)

    def collection_ids(self, slug):
        m = self.music
        if slug not in {p['slug'] for p in m.playlist_definitions}:
            raise ValueError('Collection not found.')
        with m.lock:
            if m.dynamic_playlists.get(slug):
                return list(self.tracks)
            return [r[0] for r in m.db.execute('SELECT pt.track_id FROM playlist_tracks pt JOIN tracks t ON t.id=pt.track_id WHERE playlist_slug=? AND t.available=1 ORDER BY position', (slug,))]

    def browse(self, kind, query='', offset=0, limit=60):
        with self.index_lock:
            values = list((self.albums if kind=='albums' else self.artists).values())
        values = [v for v in values if query.casefold() in (v['name']+' '+v.get('artist','')).casefold()]
        values.sort(key=lambda v: v['name'].casefold())
        return {'items':[{k:v for k,v in x.items() if k!='track_ids'} | {'tracks':len(x['track_ids'])} for x in values[offset:offset+limit]], 'total':len(values)}

    def suggestions(self):
        from datetime import date
        with self.index_lock:
            albums=[a for a in self.albums.values() if a['name']!='Unknown album' and len(a['track_ids'])>1]
            chosen=sorted(albums,key=lambda a:self.key(date.today().isoformat(),a['id']))[:6]
            return [{k:v for k,v in album.items() if k!='track_ids'} for album in chosen]

    def detail(self, kind, key):
        values = self.albums if kind=='albums' else self.artists
        if key not in values:
            raise ValueError('Not found.')
        item = values[key]
        tracks = [self.track(i) for i in item['track_ids']]
        def number(t):
            try: return (int(str(t.get('disc_number') or '1').split('/')[0]),int(str(t.get('track_number') or '0').split('/')[0]))
            except ValueError: return (0,0)
        if kind=='albums': tracks.sort(key=number)
        pending=False
        if kind=='artists' and hasattr(self,'popularity'): tracks,pending=self.popularity.get(item['name'],tracks)
        elif kind=='albums' and hasattr(self,'popularity'):
            groups={}
            for track in tracks:
                groups.setdefault(track.get('artist') or item.get('artist',''),[]).append(track)
            enriched={}
            for artist,group in groups.items():
                rated,waiting=self.popularity.get(artist,group)
                pending=pending or waiting
                enriched.update((t['id'],t) for t in rated)
            tracks=[enriched[t['id']] for t in tracks]

        return {k:v for k,v in item.items() if k!='track_ids'} | {'tracks':tracks,'popularity_pending':pending}

    def background_refresh(self):
        first = True
        while True:
            time.sleep(30 if first else 300)
            first = False
            try:
                # scan() reads only media; metadata and playlists write only catalog.db.
                with self.refresh_lock:
                    if time.time()-self.music.last_scan > 300:
                        self.music.scan()
                    else:
                        self.music._refresh_playlists()
                    self.reindex()
                self.refresh_error = False
                self.last_refresh_success = time.time()
            except Exception:
                self.refresh_error = True

class PlaybackConflict(ValueError):
    pass

class Player:
    def __init__(self, accounts, library):
        self.a, self.lib = accounts, library

    def load(self, uid):
        row = self.a.db.execute('SELECT state FROM player WHERE user_id=?', (uid,)).fetchone()
        return json.loads(row[0]) if row else {'current':None,'upcoming':[],'history':[],'channel':None,'position':0,'shuffle':False,'repeat':'off'}

    def preview(self, state):
        if state.get('station'):
            with self.lib.index_lock:available=self.lib.radio_index.tracks
            state['upcoming']=[i for i in state['upcoming'] if i in available]
        if state.get('station') and state['station'].get('mode')!='mix' and not state['upcoming']:
            chosen=self.lib.radio.pick(state['station'],state['history']+([state['current']] if state['current'] else []))
            if chosen is not None:state['upcoming']=[chosen]
        if state.get('channel') and len(state['upcoming'])<3:
            with self.lib.music.lock:
                while len(state['upcoming'])<3:
                    chosen=self.lib.music.radio_pick(state['channel'], state['history'] + ([state['current']] if state['current'] else []) + state['upcoming'])
                    if chosen is None:break
                    state['upcoming'].append(chosen)

    def save(self, uid, state):
        self.preview(state)
        self.a.db.execute('INSERT INTO player VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET state=excluded.state', (uid,json.dumps(state)))

    def public(self, state):
        def safe(i):
            try: return self.lib.track(i)
            except ValueError: return None
        return {**state, 'track':safe(state['current']) if state['current'] else None,
            'next':safe(state['upcoming'][0]) if state['upcoming'] else None,
            'queue':[t for i in state['upcoming'][:250] if (t:=safe(i))]}

    def state(self, uid):
        with self.a.lock:
            state=self.load(uid)
            self.save(uid,state)
            return self.public(state)

    def command(self, uid, data, record_history=True):
        with self.a.lock:
            s = self.load(uid)
            action = data.get('action')
            previous = s['current']
            # Stable ranks identify queue occurrences, including repeated track IDs.
            ranks=s.get('shuffle_order')
            if s.get('shuffle') and (not isinstance(ranks,list) or len(ranks)!=len(s['upcoming'])):
                s['shuffle_order']=list(range(len(s['upcoming'])))
            transition=str(data.get('transition_id',''))
            if action=='next' and transition and s.get('last_transition')==transition:
                return self.public(s)
            if 'expected_current' in data and data['expected_current']!=s['current']:
                if action=='position':return self.public(s)
                raise PlaybackConflict('Playback changed. Refresh the queue before advancing.')
            if action in ('repeat','queue_edit','queue_jump') and s.get('party_id'):
                raise ValueError('Use PartyQueue controls while hosting.')
            parties=getattr(self,'parties',None)
            if parties:data=parties.before_command(uid,data,s)
            if action=='play':
                channel=data.get('channel');station=None
                if data.get('radio'):
                    if channel or data.get('collection') or data.get('ids') or data.get('browse'):raise ValueError('Choose one playback source.')
                    radio=data['radio']
                    if not isinstance(radio,dict):raise ValueError('Invalid station.')
                    station=self.lib.radio.describe(radio.get('kind'),radio.get('seed'))
                    if 'ids' in radio:
                        # The displayed mix is a queue snapshot. Metadata enrichment
                        # must not silently replace its order between viewing and Play.
                        ids=radio['ids']
                        if (not isinstance(ids,list) or not 1<=len(ids)<=50
                                or any(type(i) is not int or i<=0 for i in ids)
                                or len(set(ids))!=len(ids)):
                            raise ValueError('Refresh the radio mix and choose Play again.')
                        station={**station,'mode':'mix'}
                    else:
                        # Keep already-open legacy clients and existing live radio
                        # sessions compatible; the current UI sends the displayed IDs.
                        ids=[self.lib.radio.start(station,data.get('track_id'))]
                elif channel:
                    if self.lib.music.collection_section(channel)!='channels': raise ValueError('Not a channel.')
                    if data.get('track_id') is not None:
                        chosen=int(data['track_id'])
                        if chosen not in self.lib.collection_ids(channel):raise ValueError('Track is outside this channel.')
                    else:
                        with self.lib.music.lock:
                            chosen=self.lib.music.radio_pick(channel, s['history']+([previous] if previous else []))
                    ids=[chosen]
                elif data.get('browse'):
                    if data.get('ids') or data.get('collection') or channel:raise ValueError('Choose one playback source.')
                    if not isinstance(data['browse'],dict):raise ValueError('Invalid browse selection.')
                    ids=self.lib.discovery.select(data['browse'])
                elif data.get('collection'):
                    ids=self.lib.collection_ids(data['collection'])
                else:
                    ids=data.get('ids',[])
                if not isinstance(ids,list) or not ids or len(ids)>50000: raise ValueError('No playable tracks in this selection.')
                ids=[int(i) for i in ids]
                for i in ids: self.lib.track(i)
                shuffled_start=data.get('shuffle') is True and not data.get('track_id') and not channel and not (station and station.get('mode')!='mix')
                ranked=list(enumerate(ids))
                if shuffled_start:
                    secrets.SystemRandom().shuffle(ranked)
                    ids=[tid for _,tid in ranked]
                selected=int(data.get('track_id') or ids[0])
                if selected not in ids: raise ValueError('Track is outside this selection.')
                ix=ids.index(selected)
                s.update(current=selected,upcoming=ids[ix+1:],channel=channel,station=station,position=0,playback_context=data.get('playback_context'))
                if channel or data.get('party_id') or station and station.get('mode')!='mix':s['repeat']='off'
                s['shuffle']=False
                s.pop('shuffle_order',None)
                if data.get('shuffle') is True and not channel and not (station and station.get('mode')!='mix'):
                    if shuffled_start:
                        s['shuffle_order']=[rank for rank,_ in ranked[ix+1:]]
                    else:
                        ranked=list(enumerate(s['upcoming']))
                        secrets.SystemRandom().shuffle(ranked)
                        s['shuffle_order']=[rank for rank,_ in ranked]
                        s['upcoming']=[tid for _,tid in ranked]
                    s['shuffle']=True
            elif action=='queue_jump':
                index=data.get('index')
                if type(index) is not int or not 0<=index<len(s['upcoming']):raise ValueError('Queue item unavailable.')
                if s.get('shuffle'):s['shuffle_order']=s['shuffle_order'][index+1:]
                s['current']=s['upcoming'][index];s['upcoming']=s['upcoming'][index+1:];s['position']=0
            elif action=='repeat':
                mode=data.get('mode')
                if mode not in ('off','all','one'):raise ValueError('Invalid repeat mode.')
                if s.get('channel') or s.get('station') and s['station'].get('mode')!='mix':raise ValueError('Repeat is unavailable for live radio.')
                s['repeat']=mode
            elif action=='queue_edit':
                # Indices identify occurrences, including duplicate tracks. A full
                # queue snapshot prevents a stale edit from removing a new request.
                if data.get('expected_upcoming')!=s['upcoming']:raise PlaybackConflict('Queue changed. Review it before editing.')
                order=data.get('order')
                if not isinstance(order,list) or any(type(i) is not int or not 0<=i<len(s['upcoming']) for i in order) or len(set(order))!=len(order):raise ValueError('Invalid queue order.')
                if s.get('shuffle'):s['shuffle_order']=[s['shuffle_order'][i] for i in order]
                s['upcoming']=[s['upcoming'][i] for i in order]
            elif action=='next':
                self.preview(s)
                actual=s['upcoming'][0] if s['upcoming'] else None
                if 'expected_next' in data and data['expected_next']!=actual:
                    raise PlaybackConflict('The next song changed. Refresh the queue before advancing.')
                if len(transition)>80:raise ValueError('Invalid transition identifier.')
                repeating=not s.get('party_id') and not s.get('channel') and not (s.get('station') and s['station'].get('mode')!='mix')
                if repeating and s.get('repeat')=='one' and data.get('automatic'):
                    pass
                else:
                    if repeating and s.get('repeat')=='all' and s['current']:
                        if s.get('shuffle'):s['shuffle_order'].append(max(s['shuffle_order'],default=-1)+1)
                        s['upcoming'].append(s['current'])
                    if s.get('shuffle') and s['shuffle_order']:s['shuffle_order'].pop(0)
                    s['current']=s['upcoming'].pop(0) if s['upcoming'] else None
                s['position']=0
                if transition:s['last_transition']=transition
            elif action=='previous':
                if s['history']:
                    if s['current']:
                        if s.get('shuffle'):s['shuffle_order'].insert(0,min(s['shuffle_order'],default=0)-1)
                        s['upcoming'].insert(0,s['current'])
                    s['current']=s['history'].pop()
                    s['position']=0
            elif action=='queue':
                ids=data.get('ids',[])
                if not isinstance(ids,list) or len(ids)>1000: raise ValueError('Queue is too long.')
                for i in ids: self.lib.track(int(i))
                if len(s['upcoming'])+len(ids)>50000: raise ValueError('Queue is full.')
                if s.get('shuffle'):
                    start=max(s['shuffle_order'],default=-1)+1
                    s['shuffle_order'].extend(range(start,start+len(ids)))
                s['upcoming'] += [int(i) for i in ids]
            elif action=='remove':
                index=int(data['index'])
                if not 0<=index<len(s['upcoming']): raise ValueError('Queue item unavailable.')
                if s.get('shuffle'):s['shuffle_order'].pop(index)
                s['upcoming'].pop(index)
            elif action=='reorder':
                src,dst=int(data['from']),int(data['to'])
                if not (0<=src<len(s['upcoming']) and 0<=dst<len(s['upcoming'])): raise ValueError('Queue item unavailable.')
                if s.get('shuffle'):s['shuffle_order'].insert(dst,s['shuffle_order'].pop(src))
                s['upcoming'].insert(dst,s['upcoming'].pop(src))
            elif action=='clear':
                s.update(upcoming=[],channel=None,station=None,shuffle=False)
                s.pop('shuffle_order',None)
            elif action=='shuffle':
                if s['channel'] or s.get('station') and s['station'].get('mode')!='mix': raise ValueError('Radio already mixes its next songs.')
                if s.get('shuffle'):
                    s['upcoming']=[tid for _,tid in sorted(zip(s['shuffle_order'],s['upcoming']))]
                    s['shuffle']=False
                    s.pop('shuffle_order',None)
                else:
                    ranked=list(enumerate(s['upcoming']))
                    secrets.SystemRandom().shuffle(ranked)
                    s['shuffle_order']=[rank for rank,_ in ranked]
                    s['upcoming']=[tid for _,tid in ranked]
                    s['shuffle']=True
            elif action=='position':
                pos=float(data.get('position',0))
                if not 0<=pos<=86400: raise ValueError('Invalid position.')
                s['position']=pos
            else: raise ValueError('Unknown playback action.')
            if action in ('play','next','queue_jump') and previous and not (action=='next' and data.get('automatic') and s.get('repeat')=='one' and not s.get('party_id')):
                s['history']=(s['history']+[previous])[-250:]
            if parties:parties.after_command(uid,data,s)
            if record_history and action in ('play','next','previous','queue_jump') and s['current']:
                self.a.db.execute('INSERT INTO history(user_id,track_id,played_at) VALUES(?,?,?)',(uid,s['current'],int(time.time())))
            self.save(uid,s)
            return self.public(s)

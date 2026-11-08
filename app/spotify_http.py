"""Spotify endpoints use the existing account session, CSRF and party ownership."""
import math
import re
from .library import PlaybackConflict


def get(h,path,q):
    if not path.startswith('/api/v1/spotify'):return False
    service=h.server.spotify
    if path=='/api/v1/spotify/callback':
        # Never put authorization codes, provider errors or tokens in the return URL.
        try:
            user=h.identity();service.a.rate_limit(('spotify-callback',user['id']),12,900)
            service.complete(user['id'],h.token()[0],q.get('state',[''])[0],q.get('code',[''])[0],q.get('error',[''])[0],authorize=lambda:h.identity()['id']==user['id'])
            outcome='connected'
        except (ValueError,PermissionError):outcome='failed'
        h.send(303,b'',extra={'Location':'/?music_service=spotify&connection='+outcome,'Referrer-Policy':'no-referrer'});return True
    user=h.identity();uid=user['id']
    if path=='/api/v1/spotify':h.json(service.config(uid));return True
    if path=='/api/v1/spotify/token':
        service.a.rate_limit(('spotify-browser-token',uid),60,900)
        h.json(service.token(uid));return True
    if path=='/api/v1/spotify/playlists':
        service.a.rate_limit(('spotify-playlists',uid),60,60)
        h.json(service.playlists(uid,q.get('q',[''])[0],q.get('offset',[0])[0],q.get('catalog',['0'])[0]=='1'));return True
    match=re.fullmatch(r'/api/v1/spotify/playlists/([A-Za-z0-9]{22})',path)
    if match:
        service.a.rate_limit(('spotify-playlist-tracks',uid),12,60)
        h.json(service.playlist_tracks(uid,match[1]));return True
    return False


def post(h,path,data,user):
    if path not in ('/api/v1/spotify','/api/v1/spotify/player'):return False
    service=h.server.spotify;uid=user['id'];parties=h.server.parties
    if path=='/api/v1/spotify':
        service.a.rate_limit(('spotify-connect',uid),12,900)
        if data.get('action')=='connect':h.json(service.begin(uid,h.token()[0]));return True
        if data.get('action')!='disconnect':raise ValueError('Choose Connect or Disconnect Spotify.')
        # Revoke pending OAuth handoffs and active party leases with the account link.
        with service.a.transaction():parties.spotify_playback.stop(uid)
        result=service.disconnect(uid)
        parties.notify(uid);h.json(result);return True
    service.a.rate_limit(('spotify-player',uid),120,60)
    with service.a.lock:
        p=parties.row(data.get('party_id'),uid)
        if p['ended']:raise PermissionError('This party has ended.')
        if p['source']!='spotify':raise ValueError('This is not a Spotify party.')
        d=parties.spotify_playback.load(uid)
        if d['party_id']!=p['id'] or d['device_id']!=data.get('device_id') or d['epoch']!=data.get('epoch') or d['revision']!=data.get('revision') or d['entry_id']!=data.get('entry_id'):raise PlaybackConflict('Spotify playback changed. Refresh the party controls.')
        if not parties.spotify_playback.online(d):raise PlaybackConflict('Spotify playback device is offline. Choose Play here again.')
        action=data.get('action');position=data.get('position',0)
        if type(position) not in (int,float) or not math.isfinite(position) or not 0<=position<=86400:raise ValueError('Invalid playback position.')
        if action not in ('play','resume','pause','seek'):raise ValueError('Choose a Spotify playback action.')
        if action!='pause' and (not d.get('desired_playing') and action!='seek' or not d['entry_id']):raise PlaybackConflict('This party is paused or its queue is empty.')
        if d['entry_id']:
            row=service.a.db.execute('SELECT catalog_id FROM party_entries WHERE id=? AND party_id=? AND status=\'playing\'',(d['entry_id'],p['id'])).fetchone()
            if not row:raise PlaybackConflict('The playing song changed.')
            track=row[0]
        else:track=None
        external=d.get('spotify_device_id','')
    result=service.player(uid,external,action,track,position)
    with service.a.lock:
        fresh=service.a.db.execute('SELECT ended FROM parties WHERE id=?',(p['id'],)).fetchone();latest=parties.spotify_playback.load(uid)
        stale=not fresh or fresh['ended'] or latest['party_id']!=p['id'] or latest['epoch']!=d['epoch'] or latest['revision']!=d['revision'] or latest['entry_id']!=d['entry_id']
    if stale:
        if action in ('play','resume'):
            try:service.player(uid,external,'pause')
            except (ValueError,PermissionError):pass
        raise PlaybackConflict('Spotify playback changed while applying the control.')
    h.json({'ok':True});return True

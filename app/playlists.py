"""Personal playlist ownership and signed-in community sharing."""
import json

class Playlists:
    def __init__(self,accounts,library):self.accounts=accounts;self.library=library
    def public(self,row,uid):
        ids=[i for i in json.loads(row['tracks']) if i in self.library.tracks]
        return {'id':row['id'],'name':row['name'],'track_ids':ids,'visibility':row['visibility'],
                'owner_id':row['user_id'],'owner':row['owner'],'editable':row['user_id']==uid}
    def rows(self,uid,scope='mine',pid=None):
        if scope not in ('mine','shared'):raise ValueError('Unknown playlist scope.')
        where='p.user_id=?' if scope=='mine' else "p.visibility='shared' AND u.approved=1 AND u.frozen=0 AND u.deleted_at=0"
        args=[uid] if scope=='mine' else []
        if pid is not None:
            where="p.id=? AND (p.user_id=? OR (p.visibility='shared' AND u.approved=1 AND u.frozen=0 AND u.deleted_at=0))";args=[pid,uid]
        with self.accounts.lock:
            rows=self.accounts.db.execute('''SELECT p.*,coalesce(nullif(pr.display_name,''),u.username) owner
                FROM personal_playlists p JOIN users u ON u.id=p.user_id LEFT JOIN profiles pr ON pr.user_id=u.id
                WHERE '''+where+' ORDER BY p.id DESC',args).fetchall()
            return [self.public(row,uid) for row in rows]
    def get(self,uid,pid):
        rows=self.rows(uid,pid=pid)
        if not rows:raise LookupError('Playlist not found.')
        return rows[0]
    def edit(self,uid,data):
        a=self.accounts
        with a.transaction():
            pid=data.get('id');row=None
            if pid:
                row=a.db.execute('SELECT * FROM personal_playlists WHERE id=? AND user_id=?',(int(pid),uid)).fetchone()
                if not row:raise LookupError('Playlist not found.')
                if data.get('delete'):
                    a.db.execute('DELETE FROM personal_playlists WHERE id=? AND user_id=?',(pid,uid));return {'ok':True}
            name=str(data.get('name',row['name'] if row else '')).strip()[:120]
            if not name:raise ValueError('Give your playlist a name.')
            visibility=data.get('visibility',row['visibility'] if row else ('shared' if a.playlist_shared_default(uid) else 'private'))
            if visibility not in ('private','shared'):raise ValueError('Choose private or shared.')
            ids=data.get('track_ids',json.loads(row['tracks']) if row else [])
            if not isinstance(ids,list) or len(ids)>10000:raise ValueError('Invalid playlist.')
            ids=list(dict.fromkeys(int(i) for i in ids))
            if 'add_track_id' in data:
                tid=int(data['add_track_id']);self.library.track(tid)
                if tid not in ids:ids.append(tid)
            if len(ids)>10000:raise ValueError('Playlist is full.')
            # Old unavailable entries may remain; newly supplied tracks must exist.
            if 'track_ids' in data:
                for tid in ids:self.library.track(tid)
            if pid:a.db.execute('UPDATE personal_playlists SET name=?,tracks=?,visibility=? WHERE id=? AND user_id=?',(name,json.dumps(ids),visibility,pid,uid))
            else:pid=a.db.execute('INSERT INTO personal_playlists(user_id,name,tracks,visibility) VALUES(?,?,?,?)',(uid,name,json.dumps(ids),visibility)).lastrowid
            return {'id':int(pid)}

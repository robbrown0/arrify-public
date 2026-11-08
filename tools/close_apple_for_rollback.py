"""Offline rollback preparation: snapshot accounts, then close unsupported parties."""
import json
import contextlib
import os
from pathlib import Path
import sqlite3
import sys


def prepare(database,backup):
    database,backup=Path(database),Path(backup)
    if not database.is_file():raise ValueError('Accounts database is missing; rollback was not applied.')
    fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    with contextlib.closing(sqlite3.connect(database)) as db,contextlib.closing(sqlite3.connect(backup)) as saved:
        db.backup(saved)
        if saved.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Rollback safety backup failed verification.')
        columns={r[1] for r in db.execute('PRAGMA table_info(parties)')}
        closed=0
        db.execute('BEGIN IMMEDIATE')
        if 'source' in columns:
            unsupported="source='apple'"+(" OR invitation_version>=1" if 'invitation_version' in columns else '')
            ids=[r[0] for r in db.execute('SELECT id FROM parties WHERE ended=0 AND ('+unsupported+')')]
            closed=db.execute('UPDATE parties SET ended=1,open=0,revision=revision+1 WHERE ended=0 AND ('+unsupported+')').rowcount
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for pid in ids:
                if 'party_guests' in tables:db.execute('DELETE FROM party_guests WHERE party_id=?',(pid,))
                if 'party_session_ids' in tables:db.execute("UPDATE party_session_ids SET state='ended' WHERE id=?",(pid,))
                if 'party_entries' in tables:db.execute("UPDATE party_entries SET status=CASE WHEN status='playing' THEN 'skipped' ELSE 'removed' END WHERE party_id=? AND status IN ('playing','queued')",(pid,))
            if 'player' in tables:
                for uid,payload in db.execute('SELECT user_id,state FROM player').fetchall():
                    s=json.loads(payload)
                    if s.get('party_id') in ids:
                        s.pop('party_id',None);s['upcoming']=[];d=s.get('listening')
                        if d:d.update(action='pause',playing=False,observed='paused',intent=d.get('intent',0)+1,revision=d.get('revision',0)+1)
                        db.execute('UPDATE player SET state=? WHERE user_id=?',(json.dumps(s),uid))
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='apple_party_playback'").fetchone():
                for uid,payload in db.execute('SELECT user_id,payload FROM apple_party_playback').fetchall():
                    d=json.loads(payload)
                    if d.get('party_id'):
                        d.update(party_id=None,entry_id=None,observed='paused',desired_playing=False,seen=0,epoch=d.get('epoch',0)+1,revision=d.get('revision',0)+1)
                        db.execute('UPDATE apple_party_playback SET payload=? WHERE user_id=?',(json.dumps(d),uid))
        db.commit()
    # A real backup, not only a rename, must be durable before image replacement.
    for path in (backup,backup.parent):
        fd=os.open(path,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    return closed


if __name__=='__main__':
    print(json.dumps({'unsupported_parties_closed':prepare(sys.argv[1],sys.argv[2]),'backup_verified':True}))

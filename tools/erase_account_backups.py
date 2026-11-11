#!/usr/bin/env python3
"""Apply a server's deletion ledger to its offline SQLite backup snapshots.

Run hourly as the server operator. Never restore accounts.db without retaining
account-deletions.jsonl in the live data directory. No credentials are printed.
"""
import argparse
import json
import os
import tempfile
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.account_deletion import erase, install_guards


def scrub(data, backups, requests=None):
    ledger = Path(data)/'account-deletions.jsonl'
    if not ledger.exists():return 0
    entries = [json.loads(line) for line in ledger.read_text().splitlines()]
    if any(set(e) != {'id','setup_complete'} or type(e['id']) is not int or e['id'] < 1 or type(e['setup_complete']) is not bool for e in entries):
        raise ValueError('Invalid deletion ledger')
    ids = {e['id'] for e in entries}
    if requests:
        for path in (Path(requests)/'inbox').glob('*.json'):
            value = json.loads(path.read_text())
            if value.get('requested_by') in ids:
                del value['requested_by']
                fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.erasure-')
                try:
                    with os.fdopen(fd, 'w') as out:
                        json.dump(value, out);out.flush();os.fsync(out.fileno())
                    os.replace(temporary, path)
                finally:
                    if os.path.exists(temporary):os.unlink(temporary)
    count = 0
    for path in Path(backups).rglob('accounts.db'):
        if path.resolve() == (Path(data)/'accounts.db').resolve():raise ValueError('Backup root must not contain the live database')
        db = sqlite3.connect(path)
        try:
            db.execute('PRAGMA foreign_keys=ON'); db.execute('PRAGMA secure_delete=ON')
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS deleted_account_ids(id INTEGER PRIMARY KEY)')
                db.execute('CREATE TABLE IF NOT EXISTS account_lifecycle(id INTEGER PRIMARY KEY CHECK(id=1),setup_complete INTEGER NOT NULL)')
                db.execute('INSERT OR IGNORE INTO account_lifecycle VALUES(1,0)')
                for entry in entries:
                    uid = entry['id']
                    db.execute('INSERT OR IGNORE INTO deleted_account_ids VALUES(?)',(uid,))
                    erase(SimpleNamespace(db=db, directory=path.parent), uid)
                    if entry['setup_complete']:db.execute('UPDATE account_lifecycle SET setup_complete=1 WHERE id=1')
                install_guards(SimpleNamespace(db=db))
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            db.execute('VACUUM')
            count += 1
        finally:db.close()
    return count


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data', required=True);p.add_argument('--backups',required=True)
    p.add_argument('--requests', help='Optional shared request-service directory for removing legacy requester IDs')
    args=p.parse_args()
    print('Account erasure applied to',scrub(args.data,args.backups,args.requests),'backup snapshots.')

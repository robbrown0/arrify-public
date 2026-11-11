"""Permanent account erasure and a non-identifying restore-suppression ledger.

The ledger contains only reserved numeric IDs. Keep it when restoring a database;
it prevents an older backup from restoring deleted accounts or reusing their IDs.
"""
import json
import os


def tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def erase(accounts, uid):
    db = accounts.db
    present = tables(db)
    # Delete guest content and credentials belonging to the user's hosted parties.
    if 'parties' in present:
        for table in ('party_entries', 'party_acquisitions', 'party_guests', 'party_wifi'):
            if table in present:
                db.execute('DELETE FROM '+table+' WHERE party_id IN (SELECT id FROM parties WHERE owner=?)', (uid,))
        if 'guest_wifi_profiles' in present:
            db.execute("DELETE FROM guest_wifi_profiles WHERE scope IN (SELECT 'party:'||id FROM parties WHERE owner=?)", (uid,))
        db.execute('DELETE FROM parties WHERE owner=?', (uid,))
    if 'party_session_ids' in present:
        db.execute('DELETE FROM party_session_ids WHERE owner=?', (uid,))
    if 'guest_wifi_profiles' in present:
        db.execute('DELETE FROM guest_wifi_profiles WHERE scope=?', ('user:'+str(uid),))
    # Sessions first: cascades revoke native token families, stream grants,
    # remote activity push tokens and output registrations on every device.
    db.execute('DELETE FROM sessions WHERE user_id=?', (uid,))
    for table in sorted(present - {'users', 'sessions'}):
        columns = {r[1] for r in db.execute('PRAGMA table_info("'+table+'")')}
        if 'user_id' in columns:
            db.execute('DELETE FROM "'+table+'" WHERE user_id=?', (uid,))
    db.execute('DELETE FROM admin_audit WHERE actor_id=? OR target_id=?', (uid, uid))
    db.execute('DELETE FROM users WHERE id=?', (uid,))
    (accounts.directory/'playback-diagnostics'/f'{uid}.json').unlink(missing_ok=True)


def install_guards(accounts):
    # Requests already in flight must not recreate personal data after erasure.
    # Non-identifying reserved IDs also protect optional integrations and workers.
    db = accounts.db
    for table in tables(db):
        columns = {r[1] for r in db.execute('PRAGMA table_info("'+table+'")')}
        field = 'user_id' if 'user_id' in columns else 'owner' if table in ('parties', 'party_session_ids') else None
        if not field:
            continue
        for operation in ('INSERT', 'UPDATE'):
            db.execute(f'''CREATE TRIGGER IF NOT EXISTS "deleted_account_{table}_{operation}"
                BEFORE {operation} ON "{table}" WHEN EXISTS
                (SELECT 1 FROM deleted_account_ids WHERE id=NEW."{field}")
                BEGIN SELECT RAISE(ABORT, 'Account deleted'); END''')


def record(accounts, uid):
    path = accounts.directory/'account-deletions.jsonl'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, 'a') as out:
        setup = bool(accounts.db.execute('SELECT setup_complete FROM account_lifecycle WHERE id=1').fetchone()[0] or accounts.db.execute('SELECT 1 FROM users WHERE admin=1').fetchone())
        out.write(json.dumps({'id': uid, 'setup_complete': setup})+'\n'); out.flush(); os.fsync(out.fileno())
    directory = os.open(accounts.directory, os.O_RDONLY)
    try: os.fsync(directory)
    finally: os.close(directory)


def restore(accounts):
    path = accounts.directory/'account-deletions.jsonl'
    if not path.exists():
        return
    # Fail closed on a damaged ledger; never silently resurrect old accounts.
    ids = []
    setup_complete = False
    for line in path.read_text().splitlines():
        entry = json.loads(line)
        if set(entry) != {'id', 'setup_complete'} or type(entry['id']) is not int or entry['id'] < 1 or type(entry['setup_complete']) is not bool:
            raise ValueError('Invalid account deletion ledger; restore it before starting Arrify.')
        ids.append(entry['id'])
        setup_complete = setup_complete or entry['setup_complete']
    with accounts.transaction():
        for uid in ids:
            accounts.db.execute('INSERT OR IGNORE INTO deleted_account_ids VALUES(?)', (uid,))
            erase(accounts, uid)
        if setup_complete:
            accounts.db.execute('UPDATE account_lifecycle SET setup_complete=1 WHERE id=1')
        install_guards(accounts)

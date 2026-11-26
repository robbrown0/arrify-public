"""Verify an installed provider using disposable, restricted test accounts only.

Requires a private JSON file with username/password. Never prints credentials,
titles, paths, account identities, tokens, or audio. Existing users are untouched.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from app.accounts import Accounts
from app.audiobooks import Audiobooks
from app.listening import Listening
from app.library import Player
from test_parties import Library

parser = argparse.ArgumentParser()
parser.add_argument('--origin', required=True)
parser.add_argument('--credentials', required=True)
args = parser.parse_args()
credentials = json.loads(Path(args.credentials).read_text())
with tempfile.TemporaryDirectory(prefix='arrify-book-verification-') as directory:
    accounts = Accounts(directory)
    for username in ('verificationone', 'verificationtwo'):
        accounts.register(username, 'disposable-verification-password', email=username+'@example.invalid')
        accounts.approve(username)
    listening = Listening(Player(accounts, Library()))
    books = Audiobooks(accounts, listening, directory)
    try:
        config = books.provider.configure({'origin': args.origin, 'username': credentials['username'],
                                          'password': credentials['password'], 'enabled': True})
        before = {x['id'] for x in books.provider.admin('GET', '/api/users')['users']}
        first = books.library(1); second = books.library(2)
        assert first['items'] and len(first['items']) == len(second['items'])
        managed = [books.provider.saved_user(uid) for uid in (1, 2)]
        assert managed[0]['provider_id'] != managed[1]['provider_id']
        for value in managed:
            user = books.provider.admin('GET', '/api/users/' + value['provider_id'])
            assert user['type'] == 'user' and user['isActive']
            assert not user['permissions']['accessAllLibraries'] and not user['permissions']['delete']
            assert set(user['librariesAccessible']) == set(config['libraries'])
        token = accounts.login('verificationone', 'disposable-verification-password')
        state = listening.state(1)['listening']
        book_id = first['items'][0]['id']
        result = books.begin(1, {'book_id': book_id, 'request_id': 'verification-first', 'device_id': 'a'*32,
                                'music_revision': state['revision']}, token, 'web')
        target = min(11, result['book']['duration']/2)
        saved = books.progress(1, {'lease':result['lease'], 'device_id':'a'*32, 'seq':1, 'position':target, 'playing':False})
        assert saved['saved'] and not saved['pending']
        assert abs(books.remote_progress(1, book_id)['currentTime']-target) < .1
        assert books.remote_progress(2, book_id)['currentTime'] == 0
        _, session, part = books.stream(result['lease'], 0)
        with books.provider.user(1, 'HEAD', part['path'], raw=True) as response:
            assert response.status == 200 and int(response.headers['Content-Length']) > 0
        with books.provider.user(1, 'GET', part['path'], headers={'Range':'bytes=0-1'}, raw=True) as response:
            assert response.status == 206 and len(response.read()) == 2
        ids = [value['provider_id'] for value in managed]
        books.close(); books = Audiobooks(accounts, listening, directory)
        books.library(1)
        assert books.provider.saved_user(1)['provider_id'] == ids[0]
        for uid in (1, 2): accounts.delete_account('disposable-verification-password', user_id=uid)
        books.cleanup()
        after = {x['id'] for x in books.provider.admin('GET', '/api/users')['users']}
        assert before <= after and not set(ids) & after
        print(json.dumps({'passed':True,'library_books_on_first_page':len(first['items']),
            'chapters':len(result['book']['chapters']),'transparent_restricted_accounts':True,
            'per_user_canonical_progress':True,'head_and_ranges':True,'restart_identity':True,
            'test_accounts_removed':True,'existing_accounts_untouched':True}))
    finally:
        # Recover any account created before a failed response and remove it.
        for uid in (1, 2):
            with accounts.lock: accounts.db.execute('UPDATE users SET deleted_at=1 WHERE id=?', (uid,))
        try: books.cleanup()
        finally:
            config = books.provider.config()
            if config.get('refresh'):
                try: books.provider.call('POST', '/logout', {}, headers={'x-refresh-token':config['refresh']})
                except Exception: pass
            books.close(); accounts.db.close()

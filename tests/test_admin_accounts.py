import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.accounts import Accounts
from app.admin import request_rows

PASSWORD='A long fixture password 123!'
class AdminAccountTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.a=Accounts(self.root)
        for username in ('owner','listener'):
            self.a.register(username,PASSWORD,username+'@example.invalid');self.a.approve(username,admin=username=='owner')
        self.owner=1;self.uid=2
    def tearDown(self):self.a.db.close();self.temp.cleanup()
    def test_freeze_revokes_both_session_types_and_unfreeze_requires_new_login(self):
        tokens=[(self.a.login('listener',PASSWORD,kind),kind) for kind in ('web','native')]
        self.a.enable_recovery('listener');reset=self.a.claim_recovery()
        self.a.manage_user(self.owner,{'id':self.uid,'action':'freeze'})
        for token,kind in tokens:
            with self.assertRaises(PermissionError):self.a.identity(token,kind)
        with self.assertRaises(PermissionError):self.a.login('listener',PASSWORD)
        with self.assertRaises(PermissionError):self.a.check_recovery(reset)
        self.a.manage_user(self.owner,{'id':self.uid,'action':'unfreeze'})
        self.assertEqual(self.a.identity(self.a.login('listener',PASSWORD),'web')['id'],self.uid)
        with self.assertRaises(PermissionError):self.a.identity(tokens[0][0],'web')
    def test_self_actions_and_non_admin_actor_fail(self):
        for action in ('freeze','delete'):
            with self.assertRaises(ValueError):self.a.manage_user(self.owner,{'id':self.owner,'action':action,'confirm_username':'owner'})
        with self.assertRaises(PermissionError):self.a.manage_user(self.uid,{'id':self.owner,'action':'freeze'})
        self.assertTrue(self.a.identity(self.a.login('owner',PASSWORD),'web')['admin'])
    def test_freeze_during_password_verification_cannot_issue_a_session(self):
        original=self.a.hash_password
        def race(*args,**kwargs):
            self.a.manage_user(self.owner,{'id':self.uid,'action':'freeze'})
            return original(*args,**kwargs)
        with patch.object(self.a,'hash_password',side_effect=race):
            with self.assertRaises(PermissionError):self.a.login('listener',PASSWORD)
        self.assertEqual(self.a.db.execute('SELECT count(*) FROM sessions WHERE user_id=2').fetchone()[0],0)
    def test_delete_purges_private_data_retains_requests_and_never_reuses_id(self):
        token=self.a.login('listener',PASSWORD)
        for sql,args in [('INSERT INTO favorites VALUES(?,99)',(self.uid,)),('INSERT INTO personal_playlists(user_id,name) VALUES(?,?)',(self.uid,'Private mix')),('INSERT INTO player VALUES(?,?)',(self.uid,'{}')),('INSERT INTO history(user_id,track_id,played_at) VALUES(?,99,1)',(self.uid,))]:self.a.db.execute(sql,args)
        self.a.edit_search_history(self.uid,{'action':'remember','query':'Private query'})
        self.a.db.execute('INSERT INTO music_requests(user_id,request_id,created,metadata) VALUES(?,?,1,?)',(self.uid,'a'*32,json.dumps({'title':'Song','artist':'Artist','album':'Album'})))
        diagnostics=self.root/'playback-diagnostics';diagnostics.mkdir();(diagnostics/'2.json').write_text('{}')
        self.a.enable_recovery('listener');reset=self.a.claim_recovery()
        with self.assertRaises(ValueError):self.a.manage_user(self.owner,{'id':self.uid,'action':'delete','confirm_username':'wrong'})
        self.a.manage_user(self.owner,{'id':self.uid,'action':'delete','confirm_username':'listener'})
        for table in ('sessions','favorites','personal_playlists','player','history','search_history','preferences','profiles'):
            self.assertEqual(self.a.db.execute('SELECT count(*) FROM '+table+' WHERE user_id=2').fetchone()[0],0,table)
        self.assertFalse((diagnostics/'2.json').exists())
        self.assertEqual(self.a.db.execute('SELECT count(*) FROM music_requests WHERE user_id=2').fetchone()[0],1)
        with self.assertRaises(PermissionError):self.a.identity(token,'web')
        with self.assertRaises(PermissionError):self.a.check_recovery(reset)
        self.a.register('listener',PASSWORD,'listener@example.invalid')
        new_id=self.a.db.execute("SELECT id FROM users WHERE username='listener'").fetchone()[0]
        self.assertGreater(new_id,self.uid)
        self.assertEqual(len(self.a.admin_users()['items']),2)
        self.assertEqual(self.a.pending_count(),1)
        with patch('app.admin.acquisition.call',side_effect=ValueError('offline')):
            rows=request_rows(self.a)
            self.assertEqual(rows['items'][0]['username'],'Deleted account')
            self.assertEqual(rows['items'][0]['album'],'Album');self.assertTrue(rows['status_unavailable'])
            self.assertEqual(request_rows(self.a,new_id)['items'],[])
    def test_statistics_and_pending_approval_stay_separate_from_freeze(self):
        self.a.db.execute('INSERT INTO history(user_id,track_id,played_at) VALUES(2,99,1)')
        self.a.login('listener',PASSWORD)
        member=next(u for u in self.a.admin_users()['items'] if u['id']==2)
        self.assertEqual(member['tracks_started'],1);self.assertGreater(member['last_login'],0)
        self.a.register('pending',PASSWORD,'pending@example.invalid')
        self.a.manage_user(1,{'id':3,'action':'freeze'});self.assertEqual(self.a.pending_count(),0)
        with self.assertRaises(ValueError):self.a.manage_user(1,{'id':3,'action':'approve'})
        self.a.manage_user(1,{'id':3,'action':'unfreeze'});self.assertEqual(self.a.pending_count(),1)
        self.a.manage_user(1,{'id':3,'action':'approve'});self.assertEqual(self.a.pending_count(),0)
    def test_stale_administrator_cannot_mutate_accounts(self):
        self.a.approve('listener',admin=True)
        self.a.manage_user(2,{'id':1,'action':'freeze'})
        with self.assertRaises(PermissionError):self.a.manage_user(1,{'id':2,'action':'delete','confirm_username':'listener'})

import tempfile
import unittest
from types import SimpleNamespace
from app.accounts import Accounts
from app.playlists import Playlists

class PlaylistTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.a=Accounts(self.tmp.name)
        for name in ('owner','listener'):
            self.a.db.execute('INSERT INTO users(username,password,approved,created) VALUES(?,?,1,1)',(name,'fixture'))
        self.lib=SimpleNamespace(tracks={1:{},2:{}},track=lambda tid:self.lib.tracks[tid])
        self.p=Playlists(self.a,self.lib)
    def tearDown(self):self.a.db.close();self.tmp.cleanup()
    def test_private_default_and_owner_only_mutation(self):
        pid=self.p.edit(1,{'name':'Private'})['id']
        self.assertEqual(self.p.get(1,pid)['visibility'],'private');self.assertEqual(self.p.rows(2,'shared'),[])
        with self.assertRaises(LookupError):self.p.get(2,pid)
        for data in ({'name':'stolen'},{'visibility':'shared'},{'delete':True},{'add_track_id':1}):
            with self.assertRaises(LookupError):self.p.edit(2,{'id':pid,**data})
    def test_share_read_play_unshare_without_copying(self):
        pid=self.p.edit(1,{'name':'A mix','visibility':'shared','add_track_id':1})['id']
        shared=self.p.get(2,pid);self.assertEqual(shared['track_ids'],[1]);self.assertFalse(shared['editable']);self.assertEqual(shared['owner'],'owner')
        self.assertEqual(len(self.p.rows(2,'shared')),1)
        self.p.edit(1,{'id':pid,'add_track_id':2});self.p.edit(1,{'id':pid,'add_track_id':2})
        self.assertEqual(self.p.get(2,pid)['track_ids'],[1,2])
        self.p.edit(1,{'id':pid,'visibility':'private'})
        with self.assertRaises(LookupError):self.p.get(2,pid)
        self.assertEqual(self.p.rows(2,'shared'),[])
    def test_frozen_owner_and_deleted_playlist_not_visible(self):
        pid=self.p.edit(1,{'name':'A mix','visibility':'shared'})['id']
        self.a.db.execute('UPDATE users SET frozen=1 WHERE id=1')
        with self.assertRaises(LookupError):self.p.get(2,pid)
        self.a.db.execute('UPDATE users SET frozen=0 WHERE id=1');self.p.edit(1,{'id':pid,'delete':True})
        with self.assertRaises(LookupError):self.p.get(2,pid)
    def test_validation_atomic_add_and_missing_tracks(self):
        with self.assertRaises(ValueError):self.p.edit(1,{'name':'Invalid','visibility':'public'})
        pid=self.p.edit(1,{'name':'Mix','track_ids':[1,2,1]})['id']
        self.lib.tracks.pop(2);self.assertEqual(self.p.get(1,pid)['track_ids'],[1])
        self.p.edit(1,{'id':pid,'name':'Still mine'});self.assertEqual(self.p.get(1,pid)['name'],'Still mine')
        with self.assertRaises(KeyError):self.p.edit(1,{'id':pid,'add_track_id':999})
        self.assertEqual(self.p.get(1,pid)['track_ids'],[1])

    def test_personal_default_and_explicit_creation_override(self):
        self.a.set_playlist_shared_default(1,True)
        shared=self.p.edit(1,{'name':'Default shared'})['id']
        private=self.p.edit(1,{'name':'Explicit private','visibility':'private'})['id']
        other=self.p.edit(2,{'name':'Other account'})['id']
        self.assertEqual(self.p.get(1,shared)['visibility'],'shared')
        self.assertEqual(self.p.get(1,private)['visibility'],'private')
        self.assertEqual(self.p.get(2,other)['visibility'],'private')
        self.a.set_playlist_shared_default(1,False)
        self.p.edit(1,{'id':shared,'name':'Still shared'})
        self.assertEqual(self.p.get(1,shared)['visibility'],'shared')
        self.assertEqual(self.p.get(1,self.p.edit(1,{'name':'Now private'})['id'])['visibility'],'private')
        self.assertEqual(self.p.get(1,self.p.edit(1,{'name':'Explicit shared','visibility':'shared'})['id'])['visibility'],'shared')

    def test_sharing_default_validation_and_existing_preferences(self):
        self.a.update_profile(1,'Owner','owner@example.invalid','largest')
        self.a.set_playlist_shared_default(1,True)
        self.assertEqual(self.a.profile(1)['text_size'],'largest')
        self.a.update_profile(1,'Owner','owner@example.invalid','standard')
        self.assertTrue(self.a.playlist_shared_default(1))
        for value in ('yes',1,None):
            with self.assertRaises(ValueError):self.a.set_playlist_shared_default(1,value)
        self.assertTrue(self.a.playlist_shared_default(1))

    def test_legacy_preferences_migrate_to_private_without_changing_text(self):
        self.a.db.execute("INSERT INTO preferences(user_id,text_size) VALUES(1,'largest')")
        self.a.db.execute('ALTER TABLE preferences DROP COLUMN playlist_shared_default')
        self.a.db.close();self.a=Accounts(self.tmp.name)
        self.assertEqual(self.a.profile(1)['text_size'],'largest')
        self.assertFalse(self.a.playlist_shared_default(1))

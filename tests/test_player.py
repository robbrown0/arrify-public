import tempfile
import unittest
from app.accounts import Accounts
from app.library import Player
from types import SimpleNamespace
import threading

class Library:
    def __init__(self):
        self.music=SimpleNamespace(lock=threading.RLock(),collection_section=lambda _: 'channels',radio_pick=self.pick)
        self.turn=0
    def track(self,i):
        if i not in (1,2,3):raise ValueError('Unavailable')
        return {'id':i}
    def pick(self,slug,history):
        self.turn+=1
        return next(i for i in (1,2,3) if not history or i!=history[-1])
    def collection_ids(self,slug):return [1,2,3]

class PlayerTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.a=Accounts(self.tmp.name);self.p=Player(self.a,Library())
    def tearDown(self):self.a.db.close();self.tmp.cleanup()
    def test_stable_radio_next_and_history(self):
        first=self.p.command(1,{'action':'play','channel':'test'})
        self.assertEqual(len(first['upcoming']),2)
        expected=list(first['upcoming'])
        self.assertEqual(self.p.state(1)['upcoming'],expected)
        nxt=first['next']['id']
        self.assertEqual(self.p.state(1)['next']['id'],nxt)
        moved=self.p.command(1,{'action':'next'})
        self.assertEqual(moved['current'],nxt)
        self.assertEqual(self.p.command(1,{'action':'previous'})['current'],first['current'])
    def test_channel_selected_song_preserves_continuous_rotation(self):
        first=self.p.command(1,{'action':'play','channel':'test','track_id':3})
        self.assertEqual(first['current'],3)
        self.assertEqual(first['channel'],'test')
        for _ in range(8):
            before=self.p.state(1)
            after=self.p.command(1,{'action':'next'})
            self.assertEqual(after['current'],before['next']['id'])
            self.assertEqual(after['channel'],'test')
            self.assertIsNotNone(after['next'])
    def test_channel_rejects_song_outside_membership(self):
        self.p.lib.collection_ids=lambda slug:[1,2]
        with self.assertRaises(ValueError):self.p.command(1,{'action':'play','channel':'test','track_id':3})
        self.assertIsNone(self.p.state(1)['current'])
    def test_accounts_have_isolated_queues(self):
        self.p.command(1,{'action':'play','ids':[1,2,3]})
        self.assertIsNone(self.p.state(2)['current'])
        self.assertEqual(self.p.state(2)['upcoming'],[])
    def test_reorder_and_remove(self):
        self.p.command(1,{'action':'play','ids':[1,2,3]})
        self.assertEqual(self.p.command(1,{'action':'reorder','from':0,'to':1})['upcoming'],[3,2])
        self.assertEqual(self.p.command(1,{'action':'remove','index':0})['upcoming'],[2])
    def test_unavailable_cannot_enter_queue(self):
        with self.assertRaises(ValueError):self.p.command(1,{'action':'queue','ids':[999]})

if __name__=='__main__':unittest.main()

class TransitionTests(unittest.TestCase):
    setUp=PlayerTests.setUp
    tearDown=PlayerTests.tearDown
    def test_retried_advance_does_not_skip_another_song(self):
        self.p.command(1,{'action':'play','ids':[1,2,3]})
        request={'action':'next','expected_current':1,'expected_next':2,'transition_id':'same-transition'}
        first=self.p.command(1,request);second=self.p.command(1,request)
        self.assertEqual(first['current'],2);self.assertEqual(second['current'],2)
        self.assertEqual(second['upcoming'],[3]);self.assertEqual(second['history'],[1])
    def test_changed_queue_cannot_play_stale_prepared_track(self):
        from app.library import PlaybackConflict
        self.p.command(1,{'action':'play','ids':[1,2,3]})
        self.p.command(1,{'action':'reorder','from':0,'to':1})
        with self.assertRaises(PlaybackConflict):self.p.command(1,{'action':'next','expected_current':1,'expected_next':2})
        self.assertEqual(self.p.state(1)['current'],1)
        self.assertEqual(self.p.state(1)['next']['id'],3)
    def test_late_position_save_cannot_change_next_song_position(self):
        self.p.command(1,{'action':'play','ids':[1,2,3]})
        self.p.command(1,{'action':'next'})
        self.p.command(1,{'action':'position','expected_current':1,'position':123})
        self.assertEqual(self.p.state(1)['position'],0)

class RepeatAndQueueEditTests(unittest.TestCase):
    setUp=PlayerTests.setUp
    tearDown=PlayerTests.tearDown
    def test_repeat_one_automatic_but_manual_next_advances(self):
        self.p.command(1,{'action':'play','ids':[1,2,3]})
        self.p.command(1,{'action':'repeat','mode':'one'})
        s=self.p.command(1,{'action':'next','automatic':True,'transition_id':'end'})
        self.assertEqual((s['current'],s['upcoming'],s['position']),(1,[2,3],0))
        self.assertEqual(self.p.command(1,{'action':'next','automatic':True,'transition_id':'end'})['current'],1)
        self.assertEqual(self.p.command(1,{'action':'next'})['current'],2)
    def test_repeat_all_cycles_and_off_finishes(self):
        self.p.command(1,{'action':'play','ids':[1,2,3]})
        self.p.command(1,{'action':'repeat','mode':'all'})
        self.assertEqual([self.p.command(1,{'action':'next'})['current'] for _ in range(3)],[2,3,1])
        self.p.command(1,{'action':'repeat','mode':'off'})
        self.assertEqual([self.p.command(1,{'action':'next'})['current'] for _ in range(3)],[2,3,None])
    def test_single_track_repeat_and_isolation(self):
        self.p.command(1,{'action':'play','ids':[1]});self.p.command(1,{'action':'repeat','mode':'all'})
        self.assertEqual(self.p.command(1,{'action':'next','automatic':True})['current'],1)
        self.assertEqual(self.p.state(2).get('repeat'),'off')
    def test_queue_edit_occurrences_and_stale_guard(self):
        from app.library import PlaybackConflict
        self.p.command(1,{'action':'play','ids':[1,2,2,3]})
        s=self.p.command(1,{'action':'queue_edit','expected_upcoming':[2,2,3],'order':[2,0]})
        self.assertEqual(s['upcoming'],[3,2]);self.assertEqual(s['current'],1)
        with self.assertRaises(PlaybackConflict):self.p.command(1,{'action':'queue_edit','expected_upcoming':[2,2,3],'order':[]})
        self.assertEqual(self.p.state(1)['upcoming'],[3,2])
    def test_repeat_rejected_for_party_and_radio(self):
        self.p.command(1,{'action':'play','channel':'test'})
        with self.assertRaises(ValueError):self.p.command(1,{'action':'repeat','mode':'one'})
        s=self.p.load(1);s['party_id']='test';self.p.save(1,s)
        for action in [{'action':'repeat','mode':'all'},{'action':'queue_edit','order':[]}]:
            with self.assertRaises(ValueError):self.p.command(1,action)

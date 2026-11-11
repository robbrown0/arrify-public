import unittest
import test_player
from app.listening import Listening
from app.library import PlaybackConflict

class ListeningTests(unittest.TestCase):
    setUp=test_player.PlayerTests.setUp
    tearDown=test_player.PlayerTests.tearDown
    def session(self):
        self.clock=1000
        return Listening(self.p,now=lambda:self.clock)
    def call(self,l,action,device='a'*32,uid=1,**data):return l.command(uid,{'expected_revision':l.state(uid)['listening']['revision'],'action':action,'device_id':device,'device_name':'iPhone' if device[0]=='a' else 'iPad',**data})
    def test_ten_minute_interruption_freezes_cursor_without_rewriting_play_intent(self):
        l=self.session(); first=self.call(l,'play',ids=[1,2]); d=first['listening']
        args=dict(owner_epoch=d['epoch'],intent=d['intent'],expected_current=1,position=81,duration=180)
        self.call(l,'heartbeat',observed='playing',report_seq=1,**args)
        self.call(l,'heartbeat',observed='interrupted',report_seq=2,**args)
        self.clock += 600
        stopped=l.state(1)
        self.assertEqual(stopped['current'],1); self.assertEqual(stopped['position'],81)
        self.assertEqual(l.position(l.load(1)),81)
        self.assertTrue(stopped['listening']['playing'])
        self.assertEqual(stopped['listening']['intent'],d['intent'])
        self.call(l,'heartbeat',observed='playing',report_seq=3,**args)
        self.assertEqual(self.a.db.execute('SELECT count(*) FROM history').fetchone()[0],1)
        paused=self.call(l,'pause','b'*32)
        self.call(l,'heartbeat',observed='interrupted',report_seq=4,**args)
        self.assertFalse(l.state(1)['listening']['playing'])
        self.assertEqual(l.state(1)['listening']['intent'],paused['listening']['intent'])

    def test_buffer_observation_is_owner_scoped_and_keeps_original_sample_time(self):
        import json
        l=self.session();first=self.call(l,'play',ids=[1,2,3]);d=first['listening']
        report={'state':'active','items':[{'id':1,'loaded_bytes':10,'total_bytes':20,'state':'downloading'}],'player_seconds':4,'sample_age_seconds':2}
        args=dict(owner_epoch=d['epoch'],intent=d['intent'],expected_current=1,position=0,duration=180,observed='playing')
        self.call(l,'heartbeat',buffer=report,**args)
        saved=lambda:json.loads(self.a.db.execute('SELECT status FROM listening_status WHERE user_id=1').fetchone()[0])
        self.assertEqual(saved()['buffer']['downloaded_bytes'],10);self.assertEqual(saved()['buffer_at'],998)
        self.clock+=5;self.call(l,'heartbeat',**args);self.assertEqual(saved()['buffer_at'],998)
        with self.assertRaises(PlaybackConflict):self.call(l,'heartbeat',device='b'*32,buffer=report,**args)
        self.call(l,'play',ids=[2,3]);self.call(l,'heartbeat',buffer=report,**args)
        self.assertNotIn('buffer',saved())

    def test_remote_controls_preserve_output_and_stale_heartbeat_cannot_undo_selection(self):
        l=self.session();first=self.call(l,'play',ids=[1,2,3]);d=first['listening']
        second=self.call(l,'play','b'*32,ids=[3,2]);self.assertEqual(second['current'],3);self.assertEqual(second['listening']['owner'],'a'*32)
        stale=self.call(l,'heartbeat',owner_epoch=d['epoch'],intent=d['intent'],expected_current=1,position=99,duration=180,observed='playing')
        self.assertEqual(stale['position'],0);self.assertTrue(stale['listening']['pending'])
        self.assertFalse(l.stream_allowed(1,'b'*32,d['epoch']));self.assertTrue(l.stream_allowed(1,'a'*32,d['epoch']))
    def test_takeover_preserves_position_queue_and_fences_old_output(self):
        l=self.session();first=self.call(l,'play',ids=[1,2,3]);d=first['listening']
        self.call(l,'heartbeat',owner_epoch=d['epoch'],intent=d['intent'],expected_current=1,position=20,duration=180,observed='playing')
        self.clock+=2;take=self.call(l,'takeover','b'*32)
        self.assertEqual(take['position'],22);self.assertEqual(take['upcoming'],[2,3]);self.assertEqual(take['listening']['epoch'],2)
        self.assertFalse(l.stream_allowed(1,'a'*32,1));self.assertTrue(l.stream_allowed(1,'b'*32,2))
        with self.assertRaises(PlaybackConflict):self.call(l,'heartbeat',owner_epoch=1,intent=1,expected_current=1)
        with self.assertRaises(PlaybackConflict):self.call(l,'next',automatic=True,owner_epoch=1)
    def test_account_isolation_and_explicit_offline_takeover(self):
        l=self.session();self.call(l,'play',ids=[1,2]);self.clock+=100
        result=self.call(l,'play','b'*32,ids=[3]);self.assertEqual(result['listening']['owner'],'a'*32);self.assertFalse(result['listening']['online'])
        self.assertIsNone(l.state(2)['current']);self.assertIsNone(l.state(2)['listening']['owner'])
        self.call(l,'play','b'*32,uid=2,ids=[2]);self.assertEqual(l.state(1)['current'],3)
    def test_idempotent_next_does_not_restart_or_repeat_history(self):
        l=self.session();self.call(l,'play',ids=[1,2,3]);data={'expected_current':1,'expected_next':2,'transition_id':'fixture-next'}
        first=self.call(l,'next',**data);again=self.call(l,'next',**data)
        self.assertEqual(first['current'],2);self.assertEqual(first['listening']['intent'],again['listening']['intent']);self.assertEqual(first['history'],again['history'])
    def test_pause_seek_resume_and_heartbeat_status(self):
        l=self.session();first=self.call(l,'play',ids=[1,2]);self.call(l,'pause','b'*32)
        seek=self.call(l,'seek','b'*32,position=35);self.assertFalse(seek['listening']['playing']);self.assertEqual(seek['position'],35)
        resume=self.call(l,'resume','b'*32);d=resume['listening'];self.assertTrue(d['playing'])
        ack=self.call(l,'heartbeat',owner_epoch=d['epoch'],intent=d['intent'],expected_current=1,position=36,duration=180,observed='playing',quality='Lossless',profile='original')
        self.assertFalse(ack['listening']['pending']);self.assertEqual(ack['listening']['quality'],'Lossless')
    def test_invalid_selection_rolls_back_first_claim_and_position_validation(self):
        l=self.session()
        with self.assertRaises(ValueError):self.call(l,'play',ids=[999])
        self.assertIsNone(l.state(1)['listening']['owner']);self.call(l,'play',ids=[1])
        for value in (float('nan'),float('inf'),-1):
            with self.assertRaises(ValueError):self.call(l,'seek',position=value)
    def test_pending_command_rejects_old_ack_even_for_same_track(self):
        l=self.session();first=self.call(l,'play',ids=[1]);self.call(l,'seek','b'*32,position=40)
        result=self.call(l,'heartbeat',owner_epoch=1,intent=first['listening']['intent'],expected_current=1,position=3,duration=120,observed='paused')
        self.assertEqual(result['position'],40);self.assertTrue(result['listening']['playing']);self.assertTrue(result['listening']['pending'])
    def test_compact_status_does_not_rewrite_queue_json(self):
        import json
        l=self.session();first=self.call(l,'play',ids=[1,2,3]*2000);d=first['listening']
        before=self.a.db.execute('SELECT state FROM player WHERE user_id=1').fetchone()[0]
        self.call(l,'heartbeat',owner_epoch=d['epoch'],intent=d['intent'],expected_current=1,position=9,duration=180,observed='playing')
        after=self.a.db.execute('SELECT state FROM player WHERE user_id=1').fetchone()[0]
        self.assertEqual(before,after)
        compact=l.state(1,revision=d['revision']);self.assertTrue(compact['_partial']);self.assertNotIn('upcoming',compact);self.assertNotIn('queue',compact);self.assertLess(len(json.dumps(compact)),1200);self.assertEqual(compact['position'],9)
    def test_latest_desired_seek_survives_coalesced_pause_and_resume(self):
        l=self.session();self.call(l,'play',ids=[1,2]);seek=self.call(l,'seek','b'*32,position=42);self.call(l,'pause','b'*32);latest=self.call(l,'resume','b'*32)
        self.assertEqual(latest['listening']['action'],'resume');self.assertEqual(latest['listening']['seek_target'],42);self.assertEqual(latest['listening']['seek_intent'],seek['listening']['seek_intent']);self.assertTrue(latest['listening']['playing'])
    def test_delayed_control_cannot_replace_newer_state(self):
        l=self.session();first=self.call(l,'play',ids=[1,2]);self.call(l,'play','b'*32,ids=[3])
        with self.assertRaises(PlaybackConflict):self.call(l,'pause',expected_revision=first['listening']['revision'])
        self.assertEqual(l.state(1)['current'],3);self.assertTrue(l.state(1)['listening']['playing'])
    def test_notifications_follow_commit_and_are_account_scoped(self):
        from app.listening_events import ListeningEvents
        hub=ListeningEvents();l=Listening(self.p,events=hub)
        original=hub.publish
        def check(uid):self.assertFalse(self.a.db.in_transaction);original(uid)
        hub.publish=check
        self.call(l,'play',ids=[1]);self.assertEqual(hub.version(1),1);self.assertEqual(hub.version(2),0)
        with self.assertRaises(ValueError):self.call(l,'play',ids=[999])
        self.assertEqual(hub.version(1),1)
        self.assertEqual(hub.wait(1,0,.01),1)
        for _ in range(4):self.assertTrue(hub.open(1))
        self.assertFalse(hub.open(1));hub.close(1);self.assertTrue(hub.open(1))
    def test_history_counts_applied_playback_not_sleeping_device_requests(self):
        l=self.session();self.call(l,'play',ids=[1,2]);latest=self.call(l,'play','b'*32,ids=[3])
        self.assertEqual(self.a.db.execute('SELECT count(*) FROM history').fetchone()[0],0)
        d=latest['listening']
        for _ in range(2):self.call(l,'heartbeat',owner_epoch=d['epoch'],intent=d['intent'],expected_current=3,position=2,duration=120,observed='playing')
        self.assertEqual(self.a.db.execute('SELECT count(*) FROM history').fetchone()[0],1)
        transfer=self.call(l,'takeover','b'*32);d=transfer['listening']
        self.call(l,'heartbeat','b'*32,owner_epoch=d['epoch'],intent=d['intent'],expected_current=3,position=3,duration=120,observed='playing')
        self.assertEqual(self.a.db.execute('SELECT count(*) FROM history').fetchone()[0],1)
    def test_reload_reattaches_confirmed_cursor_and_fences_old_page_without_extra_history(self):
        l=self.session();first=self.call(l,'play',ids=[1,2,3]);d=first['listening']
        self.call(l,'heartbeat',owner_epoch=d['epoch'],intent=d['intent'],expected_current=1,position=35,duration=180,observed='playing',report_seq=4)
        self.clock+=7
        restored=self.call(l,'reattach',owner_epoch=d['epoch']);r=restored['listening']
        self.assertEqual(restored['position'],35);self.assertEqual(restored['upcoming'],[2,3]);self.assertEqual(r['seek_target'],35)
        self.assertTrue(r['playing']);self.assertEqual(r['observed'],'buffering');self.assertEqual(r['epoch'],d['epoch']+1);self.assertEqual(r['play_id'],d['play_id'])
        self.assertFalse(l.stream_allowed(1,'a'*32,d['epoch']))
        with self.assertRaises(PlaybackConflict):self.call(l,'heartbeat',owner_epoch=d['epoch'],intent=d['intent'],expected_current=1,position=36)
        self.call(l,'heartbeat',owner_epoch=r['epoch'],intent=r['intent'],expected_current=1,position=36,duration=180,observed='playing',report_seq=1)
        self.assertEqual(self.a.db.execute('SELECT count(*) FROM history').fetchone()[0],1)
    def test_paused_reload_preserves_cursor_and_does_not_claim_another_output(self):
        l=self.session();first=self.call(l,'play',ids=[1,2]);d=first['listening']
        self.call(l,'heartbeat',owner_epoch=d['epoch'],intent=d['intent'],expected_current=1,position=25,duration=180,observed='paused')
        restored=self.call(l,'reattach',owner_epoch=d['epoch']);self.assertEqual(restored['position'],25);self.assertFalse(restored['listening']['playing'])
        old=restored['listening'];self.call(l,'takeover','b'*32)
        with self.assertRaises(PlaybackConflict):self.call(l,'reattach',owner_epoch=old['epoch'])
        self.assertEqual(l.state(1)['listening']['owner'],'b'*32)
    def test_reload_revision_conflict_and_out_of_order_unload_observations(self):
        l=self.session();first=self.call(l,'play',ids=[1,2]);d=first['listening']
        self.call(l,'heartbeat',owner_epoch=d['epoch'],intent=d['intent'],expected_current=1,position=40,duration=180,observed='playing',report_seq=3)
        self.call(l,'heartbeat',owner_epoch=d['epoch'],intent=d['intent'],expected_current=1,position=30,duration=180,observed='paused',report_seq=2)
        self.assertEqual(l.state(1)['position'],40);self.assertTrue(l.state(1)['listening']['playing'])
        self.call(l,'seek','b'*32,position=60)
        with self.assertRaises(PlaybackConflict):self.call(l,'reattach',owner_epoch=d['epoch'],expected_revision=d['revision'])
        self.assertEqual(l.state(1)['position'],60)
    def test_owner_logout_pauses_releases_and_preserves_cursor_for_direct_resume(self):
        l=self.session();first=self.call(l,'play',ids=[1,2]);d=first['listening']
        l.logout(1,{'device_id':d['owner'],'owner_epoch':d['epoch'],'expected_current':1,'intent':d['intent'],'position':39},'fixture')
        state=l.state(1);self.assertEqual(state['position'],39);self.assertFalse(state['listening']['playing']);self.assertIsNone(state['listening']['owner']);self.assertEqual(state['upcoming'],[2])
        self.assertFalse(l.stream_allowed(1,'a'*32,d['epoch']))
        with self.assertRaises(PlaybackConflict):self.call(l,'heartbeat',owner_epoch=d['epoch'],intent=d['intent'],expected_current=1,position=40,observed='playing')
        resumed=self.call(l,'resume','b'*32);self.assertEqual(resumed['current'],1);self.assertEqual(resumed['position'],39);self.assertTrue(resumed['listening']['playing'])
    def test_controller_or_displaced_owner_logout_leaves_live_output_untouched(self):
        l=self.session();first=self.call(l,'play',ids=[1,2]);d=first['listening']
        l.logout(1,{'device_id':'b'*32,'owner_epoch':d['epoch']},'fixture')
        self.assertEqual(l.state(1)['listening']['owner'],'a'*32);self.assertTrue(l.state(1)['listening']['playing'])
        take=self.call(l,'takeover','b'*32)
        l.logout(1,{'device_id':'a'*32,'owner_epoch':d['epoch']},'fixture')
        self.assertEqual(l.state(1)['listening']['epoch'],take['listening']['epoch']);self.assertTrue(l.state(1)['listening']['playing'])

    def test_explicit_play_can_claim_an_offline_output_atomically(self):
        l=self.session();first=self.call(l,'play',ids=[1,2]);epoch=first['listening']['epoch']
        self.clock+=21
        result=self.call(l,'play','b'*32,ids=[3,2],takeover_if_offline=True)
        self.assertEqual(result['current'],3)
        self.assertEqual(result['listening']['owner'],'b'*32)
        self.assertEqual(result['listening']['epoch'],epoch+1)
        self.assertTrue(result['listening']['playing'])
        self.assertFalse(l.stream_allowed(1,'a'*32,epoch))
        self.assertTrue(l.stream_allowed(1,'b'*32,epoch+1))
        with self.assertRaises(PlaybackConflict):
            self.call(l,'heartbeat',owner_epoch=epoch,intent=first['listening']['intent'],expected_current=1)

    def test_offline_claim_flag_preserves_a_live_remote_output(self):
        l=self.session();first=self.call(l,'play',ids=[1,2]);d=first['listening']
        self.clock+=21
        self.call(l,'heartbeat',owner_epoch=d['epoch'],intent=d['intent'],expected_current=1,position=12,duration=180,observed='paused')
        result=self.call(l,'play','b'*32,ids=[3],takeover_if_offline=True)
        self.assertEqual(result['current'],3)
        self.assertEqual(result['listening']['owner'],'a'*32)
        self.assertEqual(result['listening']['epoch'],d['epoch'])

    def test_failed_offline_claim_does_not_replace_owner_or_queue(self):
        l=self.session();first=self.call(l,'play',ids=[1,2]);self.clock+=21
        with self.assertRaises(ValueError):
            self.call(l,'play','b'*32,ids=[999],takeover_if_offline=True)
        result=l.state(1)
        self.assertEqual(result['current'],first['current'])
        self.assertEqual(result['listening']['owner'],first['listening']['owner'])
        self.assertEqual(result['listening']['epoch'],first['listening']['epoch'])

class RepeatListeningTests(ListeningTests):
    def test_repeat_restarts_same_track_with_new_intent_once(self):
        l=self.session();self.call(l,'play',ids=[1,2]);before=self.call(l,'repeat',mode='one')
        data={'automatic':True,'owner_epoch':before['listening']['epoch'],'expected_current':1,'expected_next':2,'transition_id':'repeat-once'}
        after=self.call(l,'next',**data)
        self.assertEqual(after['current'],1);self.assertEqual(after['upcoming'],[2])
        self.assertGreater(after['listening']['intent'],before['listening']['intent'])
        self.assertEqual(after['listening']['seek_target'],0)
        retry=self.call(l,'next',**data)
        self.assertEqual(retry['listening']['intent'],after['listening']['intent'])
    def test_queue_edit_does_not_restart_output(self):
        l=self.session();before=self.call(l,'play',ids=[1,2,3])
        after=self.call(l,'queue_edit',expected_upcoming=[2,3],order=[1,0])
        self.assertEqual(after['upcoming'],[3,2]);self.assertEqual(after['current'],1)
        self.assertEqual(after['listening']['intent'],before['listening']['intent'])

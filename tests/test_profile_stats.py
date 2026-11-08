import json
import time
import unittest
import test_party_http as fixtures
from app.profile_stats import summary


class ProfileStatsTests(unittest.TestCase):
    setUp=fixtures.PartyHTTPTests.setUp
    tearDown=fixtures.PartyHTTPTests.tearDown
    call=fixtures.PartyHTTPTests.call

    def test_empty_account_has_no_estimated_minutes(self):
        data=summary(self.a,self.lib,self.uid)
        self.assertEqual(data['starts'],0);self.assertIsNone(data['top_artist']);self.assertEqual(data['recent'],[])
        self.assertNotIn('minutes',data)

    def test_native_and_apple_history_is_private_and_windowed(self):
        now=int(time.time())
        self.a.db.executemany('INSERT INTO history(user_id,track_id,played_at) VALUES(?,?,?)',[(self.uid,1,now),(self.uid,1,now-10),(self.uid,2,now-8*86400),(99,3,now),(self.uid,4,now+60)])
        self.a.db.execute('CREATE TABLE IF NOT EXISTS apple_music_history(user_id INTEGER,party_id TEXT,entry_id INTEGER,title TEXT,artist TEXT,started INTEGER,PRIMARY KEY(user_id,party_id,entry_id))')
        self.a.db.executemany('INSERT INTO apple_music_history VALUES(?,?,?,?,?,?)',[(self.uid,'p',1,'Apple song','Apple artist',now-1),(99,'p',2,'Private song','Other artist',now)])
        data=summary(self.a,self.lib,self.uid,now)
        self.assertEqual(data['starts'],3);self.assertEqual(data['top_artist'],'Artist');self.assertEqual(data['top_artist_starts'],2)
        self.assertTrue(any(t['source']=='apple' for t in data['recent']));self.assertFalse(any(t['artist']=='Other artist' for t in data['recent']))
        code,body,_=self.call('/api/v1/profile/stats?user_id=99',cookie=self.hostcookie)
        self.assertEqual(code,200);self.assertEqual(json.loads(body)['top_artist'],'Artist')
        self.assertEqual(self.call('/api/v1/profile/stats')[0],403)

    def test_summary_does_not_change_playlist_visibility(self):
        self.a.db.execute("INSERT INTO personal_playlists(user_id,name,tracks,visibility) VALUES(?,?,?,?)",(self.uid,'Private list','[]','private'))
        self.a.db.execute("INSERT INTO personal_playlists(user_id,name,tracks,visibility) VALUES(?,?,?,?)",(self.uid,'Shared list','[]','shared'))
        self.a.db.execute('INSERT INTO favorites VALUES(?,?)',(self.uid,1))
        data=summary(self.a,self.lib,self.uid)
        self.assertEqual((data['playlists'],data['shared_playlists'],data['liked_songs']),(2,1,1))
        self.assertEqual(self.a.db.execute("SELECT visibility FROM personal_playlists WHERE name='Private list'").fetchone()[0],'private')

    def test_recent_activity_is_bounded_and_combines_sources(self):
        now=int(time.time())
        self.a.db.execute('INSERT INTO history(user_id,track_id,played_at) VALUES(?,?,?)',(self.uid,1,now))
        self.a.db.execute('CREATE TABLE IF NOT EXISTS apple_music_history(user_id INTEGER,party_id TEXT,entry_id INTEGER,title TEXT,artist TEXT,started INTEGER,PRIMARY KEY(user_id,party_id,entry_id))')
        self.a.db.executemany('INSERT INTO apple_music_history VALUES(?,?,?,?,?,?)',[(self.uid,'p',i,'Song '+str(i),'Apple artist',now-i-1) for i in range(60)])
        data=summary(self.a,self.lib,self.uid,now)
        self.assertEqual(len(data['recent']),50)
        self.assertEqual(data['recent'][0]['source'],'arrify')
        self.assertEqual(data['recent'][-1]['title'],'Song 48')
        self.assertEqual([x['played_at'] for x in data['recent']],sorted([x['played_at'] for x in data['recent']],reverse=True))

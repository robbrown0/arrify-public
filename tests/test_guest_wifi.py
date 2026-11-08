import json
import unittest
import test_party_http as fixtures
from app.guest_wifi import GuestWiFi


class GuestWiFiHTTPTests(unittest.TestCase):
    setUp=fixtures.PartyHTTPTests.setUp
    tearDown=fixtures.PartyHTTPTests.tearDown
    call=fixtures.PartyHTTPTests.call
    join=fixtures.PartyHTTPTests.join

    def post(self,path,data):return self.call(path,data,self.hostcookie,self.a.csrf(self.session))
    def save_default(self,ssid='Guest; network'):
        code,body,_=self.post('/api/v1/parties/guest-wifi',dict(action='save',ssid=ssid,password='guest-passphrase',security='WPA',hidden=False))
        self.assertEqual(code,200,body)
        return json.loads(body)

    def test_editor_script_is_served_without_credentials(self):
        code,body,headers=self.call('/guest-wifi.js')
        self.assertEqual(code,200);self.assertIn(b'ArrifyWiFi',body)

    def test_personal_network_is_user_editable_and_guest_capability_scoped(self):
        endpoint='/api/v1/parties/guest-wifi'
        self.assertEqual(self.post(endpoint,{'action':'delete'})[0],200)
        self.save_default()
        self.assertEqual(self.call(endpoint,{'action':'delete'},self.hostcookie,'wrong')[0],403)
        self.assertEqual(self.call(endpoint)[0],403)
        code,body,_=self.call(self.public)
        self.assertEqual(code,200);wifi=json.loads(body)['wifi'];self.assertEqual(wifi['ssid'],'Guest; network');self.assertFalse(wifi['include_print'])
        self.assertNotIn('guest-passphrase',wifi['qr_url'])
        code,svg,headers=self.call(wifi['qr_url']);self.assertEqual(code,200);self.assertIn(b'<svg',svg);self.assertEqual(headers['Cache-Control'],'private, no-store')
        self.assertEqual(self.call(self.public+'/bad-token/wifi.svg')[0],403)

    def test_each_user_has_a_private_saved_network(self):
        self.save_default('First network')
        self.a.register('secondhost','a-strong-password',email='second@example.invalid');self.a.approve('secondhost')
        uid=self.a.db.execute("SELECT id FROM users WHERE username='secondhost'").fetchone()[0]
        token=self.a.login('secondhost','a-strong-password');cookie='arrify_session='+token;csrf=self.a.csrf(token)
        endpoint='/api/v1/parties/guest-wifi'
        self.assertIsNone(json.loads(self.call(endpoint,cookie=cookie)[1])['profile'])
        code,body,_=self.call(endpoint,dict(action='save',ssid='Second network',password='second-password',security='WPA',user_id=self.uid),cookie,csrf)
        self.assertEqual(code,200,body);self.assertTrue(json.loads(body)['editable'])
        other=self.s.parties.host(uid,data={'name':'Second party'})
        self.assertEqual(self.s.parties.wifi.settings(other['id'])['profile']['ssid'],'Second network')
        self.assertEqual(self.s.parties.wifi.settings(self.party['id'])['profile']['ssid'],'First network')
        self.assertEqual(json.loads(self.call(endpoint+'?user_id='+str(self.uid),cookie=cookie)[1])['profile']['ssid'],'Second network')
        self.assertEqual(self.call('/api/v1/parties/'+self.party['id']+'/wifi',cookie=cookie)[0],400)
        self.assertEqual(self.call(endpoint,dict(action='delete'),cookie,csrf)[0],200)
        self.assertIsNone(self.s.parties.wifi.settings(other['id'])['profile'])
        self.assertEqual(self.s.parties.wifi.default(self.uid)['ssid'],'First network')

    def test_legacy_shared_network_is_preserved_only_on_existing_parties(self):
        network=GuestWiFi.validate(dict(ssid='Old shared network',password='old-password'))
        self.a.db.execute('INSERT INTO guest_wifi_profiles VALUES(?,?)',('default',json.dumps(network)))
        self.a.db.execute("INSERT INTO party_wifi(party_id,mode,include_print,include_display) VALUES(?,'inherit',1,0)",(self.party['id'],))
        for _ in range(2):GuestWiFi(self.a)
        result=self.s.parties.wifi.settings(self.party['id'])
        self.assertEqual(result['mode'],'custom');self.assertEqual(result['profile'],network)
        self.assertTrue(result['include_print']);self.assertFalse(result['include_display'])
        self.assertIsNone(self.s.parties.wifi.default(self.uid))
        new=self.s.parties.host(self.uid,data={'name':'New party'})
        self.assertIsNone(self.s.parties.wifi.settings(new['id'])['profile'])

    def test_overrides_print_preference_and_deletion_do_not_affect_other_parties(self):
        self.save_default();pid=self.party['id'];endpoint='/api/v1/parties/'+pid+'/wifi'
        code,body,_=self.post(endpoint,dict(action='save',mode='custom',ssid='Other party',password='other-passphrase',security='WPA',hidden=True,include_print=True));self.assertEqual(code,200,body)
        self.assertTrue(json.loads(body)['include_print'])
        other=self.s.parties.host(self.uid,data={'name':'Other'})
        self.save_default('Updated default')
        with self.a.transaction():
            self.assertEqual(self.s.parties.view(self.s.parties.row(pid,self.uid),host=True)['wifi']['ssid'],'Other party')
            self.assertEqual(self.s.parties.view(self.s.parties.row(other['id'],self.uid),host=True)['wifi']['ssid'],'Updated default')
        self.assertEqual(self.post(endpoint,{'action':'delete'})[0],200)
        self.assertNotIn('wifi',json.loads(self.call(self.public)[1]))
        self.assertEqual(self.s.parties.wifi.default(self.uid)['ssid'],'Updated default')
        self.assertEqual(self.post(endpoint,dict(action='save',mode='inherit',include_print=True))[0],200)
        self.assertTrue(json.loads(self.call(self.public)[1])['wifi']['include_print'])
        self.assertEqual(self.post('/api/v1/parties/guest-wifi',{'action':'delete'})[0],200)
        self.assertNotIn('wifi',json.loads(self.call(self.public)[1]))

    def test_display_and_print_preferences_are_independent(self):
        self.save_default();endpoint='/api/v1/parties/'+self.party['id']+'/wifi'
        display='/api/v1/party-display/'+self.party['display_url'].split('#')[1]
        self.assertTrue(json.loads(self.call(display)[1])['wifi']['include_display'])
        for printed,shown in [(True,False),(False,True),(False,False),(True,True)]:
            code,body,_=self.post(endpoint,dict(action='save',mode='inherit',include_print=printed,include_display=shown))
            self.assertEqual(code,200,body)
            wifi=json.loads(self.call(display)[1])['wifi']
            self.assertEqual(wifi['include_print'],printed);self.assertEqual(wifi['include_display'],shown)
        self.assertEqual(self.post(endpoint,dict(action='save',mode='inherit',include_display='yes'))[0],400)

    def test_guests_cannot_change_wifi_and_end_revokes_wifi_routes(self):
        self.save_default();cookie,csrf=self.join()
        self.assertEqual(self.call('/api/v1/parties/'+self.party['id']+'/wifi',{'action':'delete'},cookie,csrf)[0],403)
        display='/api/v1/party-display/'+self.party['display_url'].split('#')[1]
        self.assertEqual(self.call(display+'/wifi.svg')[0],200)
        self.assertEqual(self.post('/api/v1/parties/'+self.party['id'],{'action':'end'})[0],200)
        self.assertEqual(self.call(self.public+'/wifi.svg')[0],410)
        self.assertEqual(self.call(display+'/wifi.svg')[0],410)
        self.assertNotIn('wifi',json.loads(self.call(self.public)[1]))

    def test_deleting_party_cleans_override_but_keeps_default(self):
        self.save_default();pid=self.party['id']
        self.assertEqual(self.post('/api/v1/parties/'+pid+'/wifi',dict(action='save',mode='custom',ssid='Party',password='party-password',security='WPA',include_print=False))[0],200)
        self.assertEqual(self.post('/api/v1/parties/'+pid,dict(action='delete',confirm_end=True))[0],200)
        self.assertIsNone(self.s.parties.wifi.profile('party:'+pid))
        self.assertIsNone(self.a.db.execute('SELECT * FROM party_wifi WHERE party_id=?',(pid,)).fetchone())
        self.assertIsNotNone(self.s.parties.wifi.default(self.uid))

    def test_account_deletion_cleans_only_its_party_networks(self):
        self.save_default()
        self.a.register('otherhost','a-strong-password',email='other@example.invalid');self.a.approve('otherhost')
        uid=self.a.db.execute("SELECT id FROM users WHERE username='otherhost'").fetchone()[0]
        party=self.s.parties.host(uid,data={'name':'Other host party'})
        self.s.parties.wifi.settings(party['id'],dict(action='save',mode='custom',ssid='Private party',password='another-password',security='WPA'))
        self.s.parties.wifi.default(uid,dict(action='save',ssid='Other user network',password='another-password',security='WPA'))
        self.a.db.execute('UPDATE users SET admin=1 WHERE id=?',(self.uid,))
        self.a.manage_user(self.uid,dict(action='delete',id=uid,confirm_username='otherhost'))
        self.assertIsNone(self.s.parties.wifi.default(uid))
        self.assertIsNone(self.s.parties.wifi.profile('party:'+party['id']))
        self.assertIsNotNone(self.s.parties.wifi.default(self.uid))


class GuestWiFiFormatTests(unittest.TestCase):
    def test_existing_preferences_migrate_without_loss(self):
        import sqlite3
        from types import SimpleNamespace
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
        db.execute("CREATE TABLE party_wifi(party_id TEXT PRIMARY KEY,mode TEXT,include_print INTEGER)")
        db.execute("INSERT INTO party_wifi VALUES('old','none',1)")
        for _ in range(2):GuestWiFi(SimpleNamespace(db=db,transaction=lambda:__import__('contextlib').nullcontext()))
        row=db.execute("SELECT * FROM party_wifi WHERE party_id='old'").fetchone()
        self.assertEqual((row['mode'],row['include_print'],row['include_display']),('none',1,1))
        db.close()

    def test_special_characters_are_escaped(self):
        p=GuestWiFi.validate(dict(ssid='Guest;,:"\\',password='pass;,:"\\word',hidden=True))
        self.assertEqual(GuestWiFi.payload(p),'WIFI:T:WPA;S:Guest\\;\\,\\:\\"\\\\;P:pass\\;\\,\\:\\"\\\\word;H:true;;')
    def test_validation_and_open_network(self):
        p=GuestWiFi.validate(dict(ssid='Guest',security='nopass',password='ignored'))
        self.assertEqual(p['password'],'')
        for data in [dict(ssid='x'*33,password='password'),dict(ssid='Guest',password='short'),dict(ssid='Guest\n',password='password'),dict(ssid='Guest',password='password',security='enterprise')]:
            with self.assertRaises(ValueError):GuestWiFi.validate(data)

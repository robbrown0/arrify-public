import json
import unittest
from test_party_http import PartyHTTPTests
from app.request_progress import RequestProgress

class NotificationHTTPTests(unittest.TestCase):
    setUp=PartyHTTPTests.setUp
    tearDown=PartyHTTPTests.tearDown
    call=PartyHTTPTests.call
    def test_dismiss_requires_auth_csrf_and_keeps_other_account_rows(self):
        self.s.request_progress=RequestProgress(self.a,self.lib)
        for uid in (1,2):
            self.a.db.execute('INSERT INTO request_progress VALUES(?,?,?)',(uid,'request',json.dumps({'title':'Test'})))
            self.a.db.execute('INSERT INTO request_notifications(user_id,request_id,created) VALUES(?,?,1)',(uid,'request'))
        path='/api/v1/notifications/dismiss';data={'all':True};csrf=self.a.csrf(self.session)
        self.assertEqual(self.call(path,data)[0],403)
        self.assertEqual(self.call(path,data,self.hostcookie,'wrong')[0],403)
        self.assertEqual(self.call(path,data,self.hostcookie,csrf,origin='https://other.invalid')[0],403)
        self.assertEqual(self.call(path,{'ids':[True]},self.hostcookie,csrf)[0],400)
        self.assertEqual(self.call(path,data,self.hostcookie,csrf)[0],200)
        self.assertEqual(self.s.request_progress.notifications(1),{'items':[],'unread':0})
        self.assertEqual(self.s.request_progress.notifications(2)['unread'],1)
        self.assertEqual(self.a.db.execute('SELECT count(*) FROM request_progress').fetchone()[0],2)

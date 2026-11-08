"""User-owned guest networks and owner-scoped party overrides; never alters network configuration."""
import json
import re


class GuestWiFi:
    def __init__(self, accounts):
        self.a=accounts
        accounts.db.executescript('''
            CREATE TABLE IF NOT EXISTS guest_wifi_profiles(scope TEXT PRIMARY KEY,profile TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS party_wifi(party_id TEXT PRIMARY KEY,mode TEXT NOT NULL DEFAULT 'inherit',include_print INTEGER NOT NULL DEFAULT 0);
        ''')
        if 'include_display' not in {row['name'] for row in accounts.db.execute('PRAGMA table_info(party_wifi)')}:
            accounts.db.execute('ALTER TABLE party_wifi ADD COLUMN include_display INTEGER NOT NULL DEFAULT 1')

        # Preserve old parties' effective network without giving it to new users.
        with accounts.transaction():
            legacy=self.profile('default')
            if legacy:
                if accounts.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='parties'").fetchone():
                    rows=accounts.db.execute("SELECT p.id,coalesce(w.include_print,0) include_print,coalesce(w.include_display,1) include_display FROM parties p LEFT JOIN party_wifi w ON w.party_id=p.id WHERE coalesce(w.mode,'inherit')='inherit'").fetchall()
                    for row in rows:
                        accounts.db.execute('INSERT OR REPLACE INTO guest_wifi_profiles VALUES(?,?)',('party:'+row['id'],json.dumps(legacy)))
                        accounts.db.execute("INSERT OR REPLACE INTO party_wifi(party_id,mode,include_print,include_display) VALUES(?,'custom',?,?)",(row['id'],row['include_print'],row['include_display']))
                accounts.db.execute('INSERT OR REPLACE INTO guest_wifi_profiles VALUES(?,?)',('legacy:default',json.dumps(legacy)))
                accounts.db.execute("DELETE FROM guest_wifi_profiles WHERE scope='default'")

    @staticmethod
    def validate(data):
        ssid=data.get('ssid');password=data.get('password','');security=data.get('security','WPA');hidden=data.get('hidden',False)
        if not isinstance(ssid,str) or not ssid.strip() or len(ssid.encode())>32 or any(ord(c)<32 or ord(c)==127 for c in ssid):raise ValueError('Enter a network name of up to 32 bytes.')
        if security not in ('WPA','nopass'):raise ValueError('Choose a password-protected or open network.')
        if not isinstance(password,str) or any(ord(c)<32 or ord(c)==127 for c in password):raise ValueError('Enter a valid Wi-Fi password.')
        if security=='nopass':password=''
        elif not (8<=len(password.encode())<=63 or len(password)==64 and re.fullmatch('[a-fA-F0-9]+',password)):raise ValueError('Wi-Fi passwords need 8–63 bytes, or a 64-digit hexadecimal key.')
        if not isinstance(hidden,bool):raise ValueError('Choose whether the network is hidden.')
        return dict(ssid=ssid,password=password,security=security,hidden=hidden)

    def profile(self,scope):
        row=self.a.db.execute('SELECT profile FROM guest_wifi_profiles WHERE scope=?',(scope,)).fetchone()
        return json.loads(row['profile']) if row else None

    def default(self,uid,data=None):
        scope='user:'+str(uid)
        with self.a.transaction():
            if data is not None:
                if data.get('action')=='delete':self.a.db.execute('DELETE FROM guest_wifi_profiles WHERE scope=?',(scope,))
                elif data.get('action')=='save':self.a.db.execute('INSERT OR REPLACE INTO guest_wifi_profiles VALUES(?,?)',(scope,json.dumps(self.validate(data))))
                else:raise ValueError('Choose Save or Delete guest Wi-Fi.')
            return self.profile(scope)

    def settings(self,pid,data=None):
        with self.a.transaction():
            owner=self.a.db.execute('SELECT owner FROM parties WHERE id=?',(pid,)).fetchone()
            if not owner:raise ValueError('Party not found.')
            personal=self.default(owner['owner'])
            if data is not None:
                mode=data.get('mode','custom') if data.get('action')!='delete' else 'none'
                if mode not in ('inherit','custom','none') or data.get('action') not in ('save','delete'):raise ValueError('Choose a guest Wi-Fi option.')
                include=data.get('include_print',False)
                if not isinstance(include,bool):raise ValueError('Choose whether to include Wi-Fi when printing.')
                display=data.get('include_display',True)
                if not isinstance(display,bool):raise ValueError('Choose whether to include Wi-Fi on the display.')
                if mode=='custom':self.a.db.execute('INSERT OR REPLACE INTO guest_wifi_profiles VALUES(?,?)',('party:'+pid,json.dumps(self.validate(data))))
                else:self.a.db.execute('DELETE FROM guest_wifi_profiles WHERE scope=?',('party:'+pid,))
                self.a.db.execute('INSERT OR REPLACE INTO party_wifi(party_id,mode,include_print,include_display) VALUES(?,?,?,?)',(pid,mode,int(include and mode!='none'),int(display and mode!='none')))
            row=self.a.db.execute('SELECT mode,include_print,include_display FROM party_wifi WHERE party_id=?',(pid,)).fetchone()
            mode=row['mode'] if row else 'inherit';profile=personal if mode=='inherit' else self.profile('party:'+pid) if mode=='custom' else None
            return dict(mode=mode,include_display=bool(row['include_display']) if row else True,include_print=bool(row and row['include_print']),profile=profile,default_available=bool(personal))

    def delete_party(self,pid):
        self.a.db.execute('DELETE FROM party_wifi WHERE party_id=?',(pid,))
        self.a.db.execute('DELETE FROM guest_wifi_profiles WHERE scope=?',('party:'+pid,))

    @staticmethod
    def payload(profile):
        def escape(value):return ''.join('\\'+c if c in '\\;,:"' else c for c in value)
        return 'WIFI:T:'+profile['security']+';S:'+escape(profile['ssid'])+';P:'+escape(profile['password'])+';H:'+str(profile['hidden']).lower()+';;'

    def svg(self,profile):
        if not profile:raise ValueError('No guest Wi-Fi is configured.')
        from .parties import qr_svg
        return qr_svg(self.payload(profile)).replace(b'Scan to join the party',b'Connect to guest Wi-Fi')

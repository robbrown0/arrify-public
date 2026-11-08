"""Cached Gravatar image proxy. Email/hash URLs never reach other clients.

Protocol: https://docs.gravatar.com/sdk/images/ (normalized email SHA-256).
"""
import hashlib
import html
import threading
import time
from http.client import HTTPException
from urllib.error import HTTPError,URLError
from urllib.request import Request,HTTPRedirectHandler,build_opener
from app.artwork_local import image_type

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

class Avatars:
    def __init__(self,accounts):
        self.accounts=accounts
        self.fetch_lock=threading.Lock()
        self.opener=build_opener(NoRedirect())

    @staticmethod
    def url(email):
        digest=hashlib.sha256(email.strip().lower().encode('utf-8')).hexdigest()
        return f'https://gravatar.com/avatar/{digest}?s=192&d=404&r=g'

    @staticmethod
    def fallback(name):
        words=name.split()
        initials=(''.join(w[0] for w in words[:2]) if len(words)>1 else name[:2]).upper() or 'A'
        text=html.escape(initials)
        return 'image/svg+xml',f'<svg xmlns="http://www.w3.org/2000/svg" width="192" height="192" viewBox="0 0 192 192"><rect width="192" height="192" rx="96" fill="#324332"/><text x="96" y="100" dominant-baseline="middle" text-anchor="middle" fill="#c5efad" font-family="Arial,sans-serif" font-size="68" font-weight="600">{text}</text></svg>'.encode()

    def image(self,user_id):
        a=self.accounts
        # This lock coalesces concurrent header/settings lookups. Passwords and
        # playback never wait on this lock; the provider has a 5-second timeout.
        with self.fetch_lock:
            profile=a.profile(user_id)
            if not profile['email']:return self.fallback(profile['display_name'])
            with a.lock:
                row=a.db.execute('SELECT avatar_data,avatar_mime,avatar_checked FROM profiles WHERE user_id=?',(user_id,)).fetchone()
            if row and row['avatar_checked']>int(time.time())-86400:
                return (row['avatar_mime'],bytes(row['avatar_data'])) if row['avatar_data'] else self.fallback(profile['display_name'])
            mime,data='',None
            checked=int(time.time())
            try:
                req=Request(self.url(profile['email']),headers={'User-Agent':'Arrify/0.1','Accept':'image/png,image/jpeg,image/webp,image/gif'})
                with self.opener.open(req,timeout=5) as response:
                    raw=response.read(2*1024*1024+1)
                if len(raw)<=2*1024*1024:
                    kind=image_type(raw)
                    if kind:mime,data=kind,raw
            except HTTPError as exc:
                if exc.code!=404:checked-=86400-600
            except (URLError,TimeoutError,OSError,HTTPException):
                checked-=86400-600
            with a.lock:
                # A profile change while the network request was running must
                # never restore the previous email's avatar.
                a.db.execute('''UPDATE profiles SET avatar_data=?,avatar_mime=?,avatar_checked=?
                    WHERE user_id=? AND avatar_version=?''',(data,mime,checked,user_id,profile['avatar_version']))
                current=a.profile(user_id)
            if current['avatar_version']!=profile['avatar_version']:
                return self.fallback(current['display_name'])
            return (mime,data) if data else self.fallback(profile['display_name'])

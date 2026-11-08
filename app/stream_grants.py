"""Revocable, track-only capabilities for receivers without browser cookies."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import tempfile
import time

class StreamGrants:
    TTL=3600

    def __init__(self,accounts,player,directory,now=time.time):
        self.a,self.p,self.now=accounts,player,now
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
        path=directory/'stream-grants.key'
        if not path.exists():
            fd,name=tempfile.mkstemp(prefix='.stream-grants-',dir=directory)
            try:
                with os.fdopen(fd,'wb') as f:f.write(secrets.token_bytes(32));f.flush();os.fsync(f.fileno())
                try:
                    os.link(name,path)
                    directory_fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
                    try:os.fsync(directory_fd)
                    finally:os.close(directory_fd)
                except FileExistsError:pass
            finally:os.unlink(name)
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as f:
            if os.fstat(f.fileno()).st_mode&0o077:raise ValueError('Stream grant key must be private.')
            self.key=f.read(33)
        if len(self.key)!=32:raise ValueError('Invalid stream grant key; refusing to replace it.')

    @staticmethod
    def encode(value):return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')

    def attach(self,result,user,token,kind):
        # Heartbeats keep their small response and do not regenerate capabilities.
        if result.get('_partial'):return result
        d=result.get('listening',{});grants={}
        if d.get('owner'):
            expiry=int(self.now())+self.TTL
            for tid in {result.get('current'),(result.get('next') or {}).get('id')} - {None}:
                claims={'v':1,'u':user['id'],'t':tid,'d':d['owner'],'e':d['epoch'],
                        's':self.a.digest(token),'k':kind,'x':expiry}
                body=self.encode(json.dumps(claims,separators=(',',':'),sort_keys=True).encode())
                signature=self.encode(hmac.digest(self.key,body.encode(),'sha256'))
                grants[str(tid)]={'token':body+'.'+signature,'expires':expiry}
        return dict(result,stream_grants=grants)

    def identity(self,token,tid,device,epoch,check_expiry=True):
        try:
            if not isinstance(token,str) or len(token)>1600:raise ValueError()
            body,signature=token.split('.')
            expected=self.encode(hmac.digest(self.key,body.encode('ascii'),'sha256'))
            if not hmac.compare_digest(signature,expected):raise ValueError()
            c=json.loads(base64.b64decode(body+'='*(-len(body)%4),altchars=b'-_',validate=True))
            if c['v']!=1 or c['t']!=tid or c['d']!=device or str(c['e'])!=str(epoch):raise ValueError()
            if check_expiry and self.now()>=c['x']:raise ValueError()
            with self.a.lock:
                row=self.a.db.execute('''SELECT u.id FROM sessions s JOIN users u ON u.id=s.user_id
                    WHERE s.digest=? AND s.kind=? AND s.expires>? AND u.id=?
                    AND u.approved=1 AND u.frozen=0 AND u.deleted_at=0''',
                    (c['s'],c['k'],int(self.now()),c['u'])).fetchone()
                if not row:raise ValueError()
                state=self.p.load(c['u']);d=state.get('listening',{})
                allowed=[state.get('current')]+state.get('upcoming',[])[:1]
                if d.get('owner')!=c['d'] or d.get('epoch')!=c['e'] or tid not in allowed:raise ValueError()
            return {'id':c['u']}
        except (ValueError,TypeError,KeyError,UnicodeError,AttributeError,OverflowError):
            raise PermissionError('Playback link expired or is no longer authorized.') from None

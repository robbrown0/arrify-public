"""Account email grants: hashed, purpose-bound, single-use and email-bound."""
import secrets
import time
import os
import json
import ssl
import smtplib
import queue
import threading
import html
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlsplit

GENERIC = 'If the account is eligible, an email will arrive shortly. Check your inbox and spam folder.'

class Mailer:
    def __init__(self, config):
        self.config = config
        self.jobs = queue.Queue(maxsize=100)
        threading.Thread(target=self.work, daemon=True, name='account-email').start()

    def send(self, recipient, subject, body):
        # Never log recipients, credentials, or token-bearing bodies.
        self.jobs.put_nowait((recipient, subject, body))

    def work(self):
        while True:
            recipient, subject, body = self.jobs.get()
            try:
                message = EmailMessage()
                message['From'] = 'Arrify <'+self.config['from_address']+'>'
                message['To'] = recipient
                message['Subject'] = subject
                message.set_content(body)
                paragraphs=''.join('<p style="line-height:1.6">'+html.escape(line)+'</p>' for line in body.split('\n\n'))
                for line in body.splitlines():
                    if line.startswith('https://'):
                        paragraphs=paragraphs.replace(html.escape(line),'<a style="display:inline-block;background:#55e87c;color:#07120b;padding:14px 22px;border-radius:12px;text-decoration:none;font-weight:bold" href="'+html.escape(line,quote=True)+'">Continue securely</a>')
                message.add_alternative('<!doctype html><html><body style="margin:0;background:#0b110e;color:#f3f6f4;font-family:Arial,sans-serif"><main style="max-width:520px;margin:auto;padding:40px 24px"><h1>Arr<span style="color:#55e87c">ify</span></h1>'+paragraphs+'<p style="color:#a5b0a8;font-size:13px">Account security · Arrify</p></main></body></html>',subtype='html')
                with smtplib.SMTP(self.config['host'], self.config['port'], timeout=20) as smtp:
                    smtp.ehlo(); smtp.starttls(context=ssl.create_default_context()); smtp.ehlo()
                    smtp.login(self.config['username'], Path(self.config['password_file']).read_text().strip())
                    smtp.send_message(message)
            except Exception:
                import logging
                logging.getLogger(__name__).error('Account email delivery failed; request a new link.')
            finally:
                self.jobs.task_done()

class AccountEmail:
    def __init__(self, accounts, origin, mailer):
        parsed = urlsplit(origin)
        if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.query or parsed.fragment:
            raise ValueError('Account email requires a trusted HTTPS public origin.')
        self.a, self.origin, self.mailer = accounts, origin.rstrip('/'), mailer
        accounts.db.executescript('''
          CREATE TABLE IF NOT EXISTS email_grants(
            digest TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            purpose TEXT NOT NULL, email TEXT NOT NULL, expires INTEGER NOT NULL);
          CREATE INDEX IF NOT EXISTS email_grants_user ON email_grants(user_id,purpose);
        ''')

    def request(self, identifier, purpose):
        if purpose not in ('reset', 'verify'): raise ValueError('Invalid email purpose.')
        identifier = str(identifier or '').strip().lower()[:254]
        try: self.a.rate_limit(('account-email', self.a.digest(identifier), purpose), 3, 900)
        except ValueError: return {'message': GENERIC}
        with self.a.transaction():
            rows = self.a.db.execute('''SELECT u.id,u.username,u.frozen,u.deleted_at,p.email,p.email_verified_at
              FROM users u JOIN profiles p ON p.user_id=u.id
              WHERE u.username=? OR lower(trim(p.email))=?''', (identifier, identifier)).fetchall()
            # Ambiguous legacy email addresses must use a username.
            rows = [r for r in rows if r['username'] == identifier] or rows
            if len(rows) != 1: return {'message': GENERIC}
            row = rows[0]
            if row['deleted_at'] or row['frozen'] or not row['email']: return {'message': GENERIC}
            if (purpose == 'reset') != bool(row['email_verified_at']): return {'message': GENERIC}
            token = secrets.token_urlsafe(32)
            self.a.db.execute('DELETE FROM email_grants WHERE expires<=?', (int(time.time()),))
            self.a.db.execute('INSERT INTO email_grants VALUES(?,?,?,?,?)',
                (self.a.digest(token),row['id'],purpose,row['email'],int(time.time())+1800))
            action = 'Reset your password' if purpose == 'reset' else 'Verify your email'
            url = self.origin+'/account-access.html#'+purpose+':'+token
            try:
                self.mailer.send(row['email'], action+' · Arrify',
                    action+'\n\n'+url+'\n\nThis link expires in 30 minutes and can be used once. '
                    'If you did not request this, you can ignore this email. Never share this link.')
            except queue.Full:
                self.a.db.execute('DELETE FROM email_grants WHERE digest=?',(self.a.digest(token),))
        return {'message': GENERIC}

    def grant(self, token, purpose):
        if purpose not in ('reset','verify') or not isinstance(token,str) or len(token)!=43: raise ValueError('This link has expired or was already used. Request a new link.')
        row = self.a.db.execute('''SELECT g.*,u.approved,u.frozen,u.deleted_at,p.email current_email,p.email_verified_at
           FROM email_grants g JOIN users u ON u.id=g.user_id JOIN profiles p ON p.user_id=u.id
           WHERE g.digest=? AND g.purpose=? AND g.expires>?''',
           (self.a.digest(token),purpose,int(time.time()))).fetchone()
        if not row or row['frozen'] or row['deleted_at'] or row['email']!=row['current_email']:
            raise ValueError('This link has expired or was already used. Request a new link.')
        return row

    def finish(self, token, purpose, password=None):
        if purpose not in ('verify','reset'): raise ValueError('Invalid email purpose.')
        if purpose=='reset':
            from .password_policy import validate
            validate(password)
            encoded=self.a.hash_password(password)
        with self.a.recovery_lock(), self.a.transaction():
            row=self.grant(token,purpose); uid=row['user_id']
            if purpose=='verify':
                self.a.db.execute('UPDATE profiles SET email_verified_at=? WHERE user_id=?',(int(time.time()),uid))
            else:
                if not row['email_verified_at']: raise ValueError('Verify your email before resetting your password.')
                self.a.db.execute('UPDATE users SET password=? WHERE id=?',(encoded,uid))
                self.a.db.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
                legacy=self.a.directory/'recovery.json'
                try:
                    username=self.a.db.execute('SELECT username FROM users WHERE id=?',(uid,)).fetchone()[0]
                    if json.loads(legacy.read_text()).get('username')==username:legacy.unlink()
                except (OSError,ValueError):pass
            self.a.db.execute('DELETE FROM email_grants WHERE user_id=?',(uid,))
        if purpose=='reset':
            try:self.mailer.send(row['email'],'Your Arrify password changed','Your password has been changed and all devices have been signed out. If this was not you, contact your Arrify administrator immediately.')
            except queue.Full: pass
        return {'message': ('Email verified. Your account is approved. You can sign in now.' if row['approved'] else 'Email verified. An administrator must approve your account before you can listen.') if purpose=='verify' else 'Password updated. Sign in with your new password. All devices have been signed out.'}


def configure(accounts):
    filename=os.environ.get('ARRIFY_SMTP_CONFIG')
    if not filename: return None
    config=json.loads(Path(filename).read_text())
    service=AccountEmail(accounts,os.environ['ARRIFY_ORIGIN'],Mailer(config))
    # Explicit owner opt-in; a durable marker prevents trusting later email changes.
    if config.get('trust_existing_emails') is True:
        with accounts.transaction():
            accounts.db.execute('CREATE TABLE IF NOT EXISTS email_migrations(name TEXT PRIMARY KEY)')
            if not accounts.db.execute("SELECT 1 FROM email_migrations WHERE name='existing-emails-v1'").fetchone():
                accounts.db.execute("UPDATE profiles SET email_verified_at=? WHERE trim(email)<>'' AND email_verified_at=0 AND user_id IN (SELECT id FROM users WHERE email_required=0 AND deleted_at=0)",(int(time.time()),))
                accounts.db.execute("INSERT INTO email_migrations VALUES('existing-emails-v1')")
    return service

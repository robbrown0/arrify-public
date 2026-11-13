"""Loopback-only HTTPS fixture with in-memory email; never sends real mail."""
import json,os,ssl,tempfile
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from app.accounts import Accounts
from app.account_email import AccountEmail
from app.server import Handler
from app.branding import Branding
from pathlib import Path
import app.server as runtime
from app.web_assets import WebAssets
runtime.WEB=Path("/audit/web")
runtime.WEB_ASSETS=WebAssets(runtime.WEB)

class Mail:
    def __init__(self):self.items=[]
    def send(self,to,subject,body):self.items.append({'to':to,'subject':subject,'body':body})
class Fixture(Handler):
    def get(self):
        if self.path=='/__test-mail':return self.json(self.server.accounts.account_email.mailer.items)
        return super().get()
with tempfile.TemporaryDirectory() as directory:
    accounts=Accounts(directory)
    accounts.bootstrap('owner','Fixture owner password 893','owner@example.invalid')
    accounts.account_email=AccountEmail(accounts,os.environ['ARRIFY_ORIGIN'],Mail())
    server=ThreadingHTTPServer(('127.0.0.1',18893),Fixture)
    server.accounts=accounts;server.library=server.player=SimpleNamespace()
    server.branding=Branding(directory,Path('/audit/web/assets/icon-512.png'))
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain('/fixture/cert.pem','/fixture/key.pem')
    server.socket=context.wrap_socket(server.socket,server_side=True)
    server.serve_forever()

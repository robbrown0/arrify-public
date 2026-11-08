import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import signal
import threading
import time
from datetime import datetime,timedelta,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse,parse_qs
from .config import Config
from .engine import run
from .store import Store


def next_run(current):
    target=current.replace(hour=9,minute=0,second=0,microsecond=0)
    target+=timedelta(days=(0-current.weekday())%7)
    if target<=current: target+=timedelta(days=7)
    return target


def serve(config):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path=urlparse(self.path)
            if path.path=='/health':
                from .worker_health import read
                worker=read(config.data/'acquisition-worker.json') if config.expansion else {'stale':False}
                body=json.dumps({'service':'degraded' if worker['stale'] else 'running','acquisition_enabled':config.acquire and config.reviewed,'worker':worker}).encode(); typ='application/json'
                if worker['stale']:
                    self.send_response(503);self.send_header('Content-Type',typ);self.end_headers();self.wfile.write(body);return
            elif path.path in ('/spectrum.json','/spectrum.csv'):
                name='spectrum-status.json' if path.path.endswith('.json') else 'spectrum-songs.csv'
                try: body=(config.data/name).read_bytes()
                except FileNotFoundError: self.send_error(404,'Channel report not ready'); return
                typ='application/json' if name.endswith('.json') else 'text/csv; charset=utf-8'
            elif path.path in ('/airplay.json','/jazz-for-sleep.json','/80son8.json','/thebridge.json','/classicrewind.json','/1stwave.json'):
                name='airplay-control.json' if path.path=='/airplay.json' else path.path[1:-5]+'-status.json'
                try:body=(config.data/name).read_bytes()
                except FileNotFoundError:self.send_error(404,'Report not ready');return
                typ='application/json'
            elif path.path in ('/report','/report.json','/missing.csv','/expansion.json','/year-coverage.json','/year-coverage.csv'):
                name={'/report':'latest.md','/report.json':'latest.json','/missing.csv':'missing-songs.csv','/expansion.json':'expansion-status.json','/year-coverage.json':'year-coverage.json','/year-coverage.csv':'year-coverage.csv'}[path.path]
                try: body=(config.reports/name).read_bytes()
                except FileNotFoundError: self.send_error(404,'No completed report'); return
                typ='application/json' if name.endswith('.json') else 'text/csv' if name.endswith('.csv') else 'text/plain; charset=utf-8'
            elif path.path=='/history':
                term=parse_qs(path.query).get('q',[''])[0][:200]
                store=Store(config.data/'discovery.sqlite3')
                rows=store.db.execute('SELECT * FROM decisions WHERE artist LIKE ? OR album LIKE ? OR detail LIKE ? ORDER BY updated DESC LIMIT 200',('%'+term+'%','%'+term+'%','%'+term+'%')).fetchall()
                body=json.dumps([dict(r) for r in rows]).encode(); store.db.close(); typ='application/json'
            elif path.path=='/':
                body=b'<!doctype html><title>Music discovery</title><h1>Private music discovery</h1><p><a href="/report">Latest report</a> | <a href="/report.json">JSON</a> | <a href="/missing.csv">CSV</a> | <a href="/expansion.json">Download budget and checks</a> | <a href="/year-coverage.json">Year coverage</a></p><form action="/history"><label>Search recommendation history <input name="q"></label><button>Search</button></form>'; typ='text/html; charset=utf-8'
            else: self.send_error(404); return
            self.send_response(200); self.send_header('Content-Type',typ); self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff'); self.end_headers(); self.wfile.write(body)
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('0.0.0.0',8080),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('command',choices=['run','daemon','inspect','reject','maintain','coverage']); parser.add_argument('--dry-run',action='store_true'); parser.add_argument('--release-group'); parser.add_argument('--reason',default='user rejected')
    args=parser.parse_args(); config=Config(); config.validate()
    if args.dry_run: config.acquire=False
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s',handlers=[logging.StreamHandler(),RotatingFileHandler(config.data/'discovery.log',maxBytes=5_000_000,backupCount=5)])
    if args.command=='reject':
        if not args.release_group: parser.error('--release-group required')
        store=Store(config.data/'discovery.sqlite3'); store.decision(args.release_group,'','','rejected',args.reason); return
    if args.command=='inspect':
        from .engine import inspect
        from .api import HTTP,Lidarr
        store=Store(config.data/'discovery.sqlite3'); print(json.dumps(inspect(Lidarr(config,HTTP(store)),store),indent=2)); return
    if args.command=='coverage':
        from .coverage import snapshot
        print(json.dumps(snapshot(config))); return
    if args.command=='maintain':
        from .expansion import maintenance
        print(json.dumps(maintenance(config))); return
    if args.command=='run':
        report=run(config); print(json.dumps({'missing':len(report['missing_songs']),'requested':report['newly_requested_albums'],'report':str(config.reports/'latest.md')})); return
    serve(config)
    if os.getenv('SPECTRUM_ENABLED','false').lower()=='true':
        def channel_worker():
            from .spectrum import collect,publish
            from .api import HTTP
            from pathlib import Path
            while True:
                store=Store(config.data/'discovery.sqlite3')
                try:
                    collect(HTTP(store),config.data)
                    summary=publish(config.data,Path(config.catalog_db),Path('/channels/spectrum.json'))
                    logging.info('Spectrum channel: %s', {k:v for k,v in summary.items() if k!='songs'})
                    from .stations import refresh,publish_imports,backfill_one
                    logging.info('SiriusXM channels: %s',refresh(HTTP(store),config.data,Path(config.catalog_db),Path('/channels')))
                    logging.info('Imported playlists: %s',publish_imports(config.data,Path(config.catalog_db),Path('/channels')))
                    from .jazz import publish_collections
                    logging.info('Jazz collections: %s',publish_collections(config.data,Path(config.catalog_db),Path('/channels')))
                    logging.info('Airplay history: %s',backfill_one(HTTP(store),config.data))
                except Exception: logging.exception('Spectrum refresh failed; previous channel retained')
                finally: store.db.close()
                # Refresh availability between upstream feed polls; same thread
                # serializes manifest writes with the full channel refresh.
                from .station_subscriptions import requested
                from .stations import publish_cached
                known=requested(config.data)
                for _ in range(10):
                    time.sleep(60)
                    if requested(config.data)!=known:break
                    try:publish_cached(config.data,Path(config.catalog_db),Path('/channels'))
                    except Exception:logging.exception('Local channel availability refresh deferred')
        threading.Thread(target=channel_worker,name='spectrum-channel',daemon=True).start()
    stop=threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:stop.set()); signal.signal(signal.SIGINT,lambda *_:stop.set())
    # First scan after deployment; weekly Monday at 09:00 UTC thereafter. Completed runs survive restarts.
    if config.expansion:
        def acquisition_worker():
            from .expansion import maintenance
            from .preparation import prepare
            from .worker_health import beat
            while not stop.is_set():
                for phase in (maintenance,prepare,maintenance):
                    beat(config.data/'acquisition-worker.json',phase.__name__)
                    try: phase(config)
                    except Exception: logging.exception('Acquisition phase %s failed; other phases will continue; reservations preserved',phase.__name__)
                beat(config.data/'acquisition-worker.json','waiting')
                stop.wait(config.maintenance_seconds)
        threading.Thread(target=acquisition_worker,name='acquisition',daemon=True).start()
    first_cycle=True
    while not stop.is_set():
        store=Store(config.data/'discovery.sqlite3')
        row=store.db.execute("SELECT finished FROM runs WHERE status='complete' ORDER BY id DESC LIMIT 1").fetchone(); store.db.close()
        current=datetime.now(timezone.utc)
        due=not row or current>=next_run(datetime.fromisoformat(row[0]))
        if first_cycle and os.getenv('SPECTRUM_ENABLED','false').lower()=='true': due=True
        first_cycle=False
        if config.expansion and row:
            due=due or (current-datetime.fromisoformat(row[0])).total_seconds()>=config.discovery_seconds
        if due:
            try: run(config)
            except Exception: logging.exception('Discovery failed; retrying in one hour')
            stop.wait(60 if config.expansion else 3600)
        else: stop.wait(min(60,(next_run(datetime.fromisoformat(row[0]))-current).total_seconds()))

if __name__=='__main__': main()

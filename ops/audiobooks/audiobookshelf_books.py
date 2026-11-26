"""Expose reviewed chapter publications as ordinary Audiobookshelf books.

Original production/NAS previews and Plex paths are preserved. No narration,
analysis, billing, or job-state operation is invoked by this module.
"""
import json
import hashlib
from pathlib import Path
import time
import urllib.request
from book_metadata import identity_for
from book_publication import publish_chapters
from plex_audiobooks import chapter_files

ROOT = Path('/home/rob/audiobook')
CONFIG = ROOT / 'config/book-library.json'

class LibraryAPI:
    def __enter__(self):
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        credentials = json.loads((ROOT / 'config/library-login.json').read_text())
        self.token = None
        user = self.call('POST', '/login', credentials, {'x-return-tokens':'true'})['user']
        self.token, self.refresh = user['accessToken'], user['refreshToken']
        return self
    def __exit__(self, *unused):
        self.call('POST', '/logout', {}, {'x-refresh-token':self.refresh})
    def call(self, method, path, data=None, headers=None):
        h = {'Content-Type':'application/json', **(headers or {})}
        if self.token: h['Authorization'] = 'Bearer ' + self.token
        request = urllib.request.Request('http://127.0.0.1:13378' + path, data=json.dumps(data).encode() if data is not None else None, method=method, headers=h)
        with self.opener.open(request, timeout=15) as response:
            raw = response.read()
            return json.loads(raw) if raw and response.headers.get('Content-Type','').startswith('application/json') else {}

def sync_book(job, *, scan=True):
    settings = json.loads(CONFIG.read_text())
    metadata = json.loads((ROOT / 'studio/state/audio' / job / 'metadata.json').read_text())
    identity = identity_for(job, metadata, ROOT / 'studio/data')
    source_id = identity.get('source_upload_id')
    if not source_id:
        raise ValueError('This publication has no verified source-book identity')
    entries = []
    for path, detail in chapter_files(source_id).items():
        source = Path('/mnt/media/audiobooks') / Path(path).relative_to('/mnt/audiobooks')
        entries.append({**detail, 'path':str(source), 'cover':str(source.parent / 'cover.jpg')})
    receipt_dir = ROOT / 'work/book-publications'; receipt_dir.mkdir(mode=0o700, exist_ok=True)
    receipt_file = receipt_dir / (source_id + '.json')
    old = json.loads(receipt_file.read_text()) if receipt_file.exists() else {}
    def stamp(path):
        stat = Path(path).stat()
        return [stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns]
    fingerprint = hashlib.sha256(json.dumps([(e['track'],stamp(e['path']),stamp(e['cover'])) for e in sorted(entries,key=lambda e:e['track'])]).encode()).hexdigest()
    target = Path(settings['published_root']) / source_id
    intact = old.get('published_stamps') == {p.name:stamp(p) for p in target.glob('*') if p.suffix in ('.m4b','.jpg')}
    if scan and old.get('verified') and old.get('library_id') == settings['library_id'] and old.get('input_fingerprint') == fingerprint and intact:
        return {**old,'unchanged':True}
    result = publish_chapters(source_id, entries, settings['published_root'])
    if not scan: return result
    with LibraryAPI() as api:
        library = settings['library_id']
        api.call('POST', '/api/libraries/' + library + '/scan', {})
        deadline = time.monotonic() + 60
        while True:
            rows = api.call('GET', '/api/libraries/' + library + '/items?limit=100')['results']
            matching = [row for row in rows if row.get('relPath') == source_id]
            if len(matching) == 1:
                book = api.call('GET', '/api/items/' + matching[0]['id'] + '?expanded=1')
                tracks = book['media']['tracks']
                if len(tracks) == result['available']:
                    if [track['metadata']['filename'] for track in tracks] != [entry['filename'] for entry in result['chapters']]:
                        raise ValueError('Audiobookshelf chapter order does not match the verified source')
                    if [chapter['title'] for chapter in book['media']['chapters']] != [entry['title'] for entry in result['chapters']]:
                        raise ValueError('Audiobookshelf chapter titles do not match the source')
                    break
            if time.monotonic() >= deadline:
                raise ValueError('Book scan is still pending; retry publication after indexing')
            time.sleep(2)
        receipt_dir = ROOT / 'work/book-publications'; receipt_dir.mkdir(mode=0o700, exist_ok=True)
        receipt_file = receipt_dir / (source_id + '.json')
        old = json.loads(receipt_file.read_text()) if receipt_file.exists() else {}
        if old.get('provider_item_id') != book['id']:
            values = {'title':identity['album'], 'authors':[{'name':identity['author']}], 'narrators':['AI full cast']}
            if identity.get('summary'): values['description'] = identity['summary']
            if identity.get('series'): values['series'] = [{'name':identity['series'], 'sequence':str(identity.get('series_index') or '')}]
            api.call('PATCH', '/api/items/' + book['id'] + '/media', {'metadata':values})
        result.update(provider_item_id=book['id'], duration=book['media']['duration'], library_id=library, verified=True, input_fingerprint=fingerprint, published_stamps={p.name:stamp(p) for p in target.glob('*') if p.suffix in ('.m4b','.jpg')})
        temporary = receipt_file.with_suffix('.partial')
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2)); temporary.chmod(0o600); temporary.replace(receipt_file)
    return result

def sync_all():
    jobs = {}
    for entry in (ROOT / 'studio/data/trials').glob('*/job.json'):
        job = json.loads(entry.read_text()).get('job_id','')
        if not (Path('/mnt/media/audiobooks/Podflix-AI/Chapters') / job.replace('-','') / 'preview.m4b').is_file():
            continue
        folder = ROOT / 'studio/state/audio' / job
        if not (entry.parent / 'publication.json').is_file():
            continue
        metadata = json.loads((folder / 'metadata.json').read_text())
        identity = identity_for(job, metadata, ROOT / 'studio/data')
        if identity.get('source_upload_id'): jobs[identity['source_upload_id']] = job
    values = [sync_book(job) for job in jobs.values()]
    return {'verified_books':len(values),'updated_books':sum(not value.get('unchanged',False) for value in values)}

if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('job',nargs='?');parser.add_argument('--all',action='store_true');args=parser.parse_args()
    if args.all: print(json.dumps(sync_all()))
    else:
        if not args.job: parser.error('Specify a reviewed job or --all')
        result=sync_book(args.job)
        print(json.dumps({key:result[key] for key in ('verified','available','total','duration','provider_item_id','library_id')}))

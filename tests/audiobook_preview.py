"""Loopback-only synthetic provider for browser qualification; no real accounts."""
import io
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import wave

from test_audiobooks import ShelfFixture, Response
from app.audiobook_provider import BookError

fixture = ShelfFixture()
buffer = io.BytesIO()
with wave.open(buffer, 'wb') as audio:
    audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000); audio.writeframes(bytes(120*8000*2))
fixture.audio = buffer.getvalue()
multipart = os.environ.get('ARRIFY_MULTIPART_BOOK') == '1'
original_item = fixture.item
def item(book_id='book-one'):
    result = original_item(book_id)
    if multipart and book_id == 'book-one':
        result['media']['duration'] = 0.4
        result['media']['tracks'] = [
            {'startOffset':0,'duration':0.25,'mimeType':'audio/wav','contentUrl':'/api/items/book-one/file/123'},
            {'startOffset':0.25,'duration':0.15,'mimeType':'audio/wav','contentUrl':'/api/items/book-one/file/124'}]
        result['media']['chapters'] = [{'title':'Prologue','start':0,'end':0.25},{'title':'Chapter One','start':0.25,'end':0.4}]
    return result
fixture.item = item
original_fetch = fixture.fetch
def fetch(method, path, data, headers, raw):
    headers = {k.lower():v for k,v in headers.items()}
    headers['Authorization'] = headers.get('authorization', '')
    if path.startswith('/api/items/') and path.split('?')[0].endswith('/cover'):
        data = Path('/opt/arrify/web/assets/icon-512.png').read_bytes()
        return Response(data, headers={'Content-Type': 'image/png', 'Content-Length': str(len(data))})
    result = original_fetch(method, path, data, headers, raw)
    if multipart and '/api/items/book-one/file/' in path:
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000)
            audio.writeframes(bytes(int((0.25 if path.endswith('/123') else 0.15)*8000)*2))
        body = buffer.getvalue(); length = len(body); response_headers = {'Content-Type':'audio/wav','Accept-Ranges':'bytes'}; status = 200
        if headers.get('range'):
            left,right = headers['range'][6:].split('-'); start = int(left or 0); end = min(length-1,int(right) if right else length-1)
            response_headers['Content-Range'] = f'bytes {start}-{end}/{length}'; body = body[start:end+1]; status = 206
        response_headers['Content-Length'] = str(len(body))
        return Response(b'' if method == 'HEAD' else body,status,response_headers)
    if isinstance(result, Response) and '/file/' in path: result.headers['Content-Type'] = 'audio/wav'
    return result

class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.handle_request()
    def do_HEAD(self): self.handle_request()
    def do_POST(self): self.handle_request()
    def do_PATCH(self): self.handle_request()
    def do_DELETE(self): self.handle_request()
    def handle_request(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        try:
            result = fetch(self.command, self.path, json.loads(body) if body else None, dict(self.headers), '/file/' in self.path or '/cover' in self.path)
            if isinstance(result, Response):
                self.send_response(result.status)
                for key, value in result.headers.items(): self.send_header(key, str(value))
                self.end_headers()
                if self.command != 'HEAD': self.wfile.write(result.read())
            else:
                value = json.dumps(result).encode(); self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(value))); self.end_headers(); self.wfile.write(value)
        except BookError as exc:
            self.send_response(exc.status); self.send_header('Content-Length', '0'); self.end_headers()
    def log_message(self, *args): pass

ThreadingHTTPServer(('0.0.0.0', 13378), Handler).serve_forever()

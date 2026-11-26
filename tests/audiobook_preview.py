"""Loopback-only synthetic provider for browser qualification; no real accounts."""
import io
import json
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
original_fetch = fixture.fetch
def fetch(method, path, data, headers, raw):
    headers = {k.lower():v for k,v in headers.items()}
    headers['Authorization'] = headers.get('authorization', '')
    if path.startswith('/api/items/') and path.split('?')[0].endswith('/cover'):
        data = Path('/opt/arrify/web/assets/icon-512.png').read_bytes()
        return Response(data, headers={'Content-Type': 'image/png', 'Content-Length': str(len(data))})
    result = original_fetch(method, path, data, headers, raw)
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

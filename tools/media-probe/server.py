"""Isolated, range-capable HTTP fixture. Only serves synthesized silence."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

audio = base64.b64decode(Path(__file__).with_name('silence.flac.base64').read_text())
class Handler(BaseHTTPRequestHandler):
    def do_HEAD(self): self.respond(False)
    def do_GET(self): self.respond(True)
    def respond(self, body):
        first, last = 0, len(audio)-1
        value = self.headers.get('Range')
        if value:
            left, right = value.removeprefix('bytes=').split('-')
            first = int(left) if left else max(0, len(audio)-int(right))
            last = min(last, int(right)) if left and right else last
        if first > last:
            self.send_response(416); self.send_header('Content-Range', f'bytes */{len(audio)}'); self.end_headers(); return
        self.send_response(206 if value else 200)
        self.send_header('Content-Type', 'audio/flac')
        self.send_header('Content-Length', str(last-first+1))
        self.send_header('Accept-Ranges', 'bytes')
        if value:self.send_header('Content-Range', f'bytes {first}-{last}/{len(audio)}')
        self.end_headers()
        if body:self.wfile.write(audio[first:last+1])
    def log_message(self, *args): pass
ThreadingHTTPServer(('127.0.0.1', 18742), Handler).serve_forever()

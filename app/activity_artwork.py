"""Small inline covers for Live Activities, including updates while iOS sleeps."""
import base64
from collections import OrderedDict
import subprocess


class ActivityArtwork:
    def __init__(self, catalog):
        self.catalog = catalog
        self.cache = OrderedDict()

    def get(self, track_id):
        if not self.catalog or not track_id:
            return None
        try:
            key = (track_id, self.catalog.artwork_version(track_id))
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            mime, data, source = self.catalog.artwork(track_id)
            result = None
            if source != 'fallback' and mime in ('image/jpeg', 'image/png', 'image/webp', 'image/gif') and len(data) <= 5 * 1024 * 1024:
                for size in (44, 32, 24):
                    image = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1',
                        '-protocol_whitelist', 'pipe', '-i', 'pipe:0', '-frames:v', '1',
                        '-vf', f'scale={size}:{size}', '-threads', '1', '-q:v', '8',
                        '-f', 'image2pipe', '-vcodec', 'mjpeg', 'pipe:1'], input=data,
                        capture_output=True, check=True, timeout=3).stdout
                    if image.startswith(b'\xff\xd8') and len(image) <= 1400:
                        result = base64.b64encode(image).decode('ascii'); break
            self.cache[key] = result
            while len(self.cache) > 64:
                self.cache.popitem(last=False)
            return result
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            # Missing artwork must not prevent transport controls or status updates.
            return None

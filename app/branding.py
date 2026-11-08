"""Administrator-uploaded branding, normalized to bounded PNG data."""
import base64
import binascii
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import threading

MAX_UPLOAD = 1024 * 1024
PNG = b'\x89PNG\r\n\x1a\n'

class Branding:
    def __init__(self, directory, default_icon):
        self.directory = Path(directory) / 'branding'
        self.icon = self.directory / 'icon.png'
        self.default_icon = Path(default_icon)
        self.lock = threading.RLock()

    def image(self):
        with self.lock:
            return (self.icon if self.icon.exists() else self.default_icon).read_bytes()

    def status(self):
        with self.lock:
            custom = self.icon.exists()
            revision = hashlib.sha256(self.image()).hexdigest()[:20]
            return {'custom': custom, 'revision': revision,
                    'icon_url': '/api/v1/branding/icon?v=' + revision}

    def update(self, user, data):
        if not user.get('admin'):
            raise PermissionError('Administrator access required.')
        action = data.get('action')
        if action == 'reset':
            with self.lock:
                self.icon.unlink(missing_ok=True)
                return self.status()
        if action != 'upload':
            raise ValueError('Choose Upload or Restore default.')
        encoded = data.get('image')
        if not isinstance(encoded, str) or len(encoded) > (MAX_UPLOAD * 4 // 3 + 4):
            raise ValueError('Icon exceeds the 1 MB upload limit.')
        try:
            image = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('Invalid icon data.') from None
        if len(image) > MAX_UPLOAD or len(image) < 33 or not image.startswith(PNG) or image[12:16] != b'IHDR':
            raise ValueError('Upload a PNG image.')
        width, height = struct.unpack('>II', image[16:24])
        if not (32 <= width <= 2048 and 32 <= height <= 2048):
            raise ValueError('Icon dimensions must be between 32 and 2048 pixels.')
        # No filenames or network protocols are accepted. Decode a single PNG,
        # strip metadata, and write a square icon with bounded work and allocation.
        try:
            result = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-max_alloc', '67108864',
                '-threads', '1', '-filter_threads', '1', '-protocol_whitelist', 'pipe',
                '-f', 'image2pipe', '-c:v', 'png', '-i', 'pipe:0', '-frames:v', '1',
                '-vf', 'scale=512:512:force_original_aspect_ratio=decrease,pad=512:512:(ow-iw)/2:(oh-ih)/2:color=black@0',
                '-map_metadata', '-1', '-threads', '1', '-c:v', 'png', '-f', 'image2pipe', 'pipe:1'],
                input=image, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8, check=True)
        except (OSError, subprocess.SubprocessError):
            raise ValueError('This image could not be read. Choose another PNG.') from None
        if not result.stdout.startswith(PNG) or len(result.stdout) > MAX_UPLOAD:
            raise ValueError('This image could not be converted.')
        with self.lock:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            filename = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.directory, prefix='icon-', delete=False) as stream:
                    filename = stream.name
                    stream.write(result.stdout); stream.flush(); os.fsync(stream.fileno())
                os.replace(filename, self.icon)
            finally:
                if filename: Path(filename).unlink(missing_ok=True)
            return self.status()

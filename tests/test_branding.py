import base64
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib
from unittest.mock import patch

from app.branding import Branding, PNG
from app import server
import test_party_http as fixtures


def png(width=64, height=32):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return PNG + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress((b'\0' + b'\x10\x90\x60' * width) * height)) + chunk(b'IEND', b'')


class BrandingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.default = self.root/'default.png'; self.default.write_bytes(png())
        self.brand = Branding(self.root, self.default)

    def test_normalized_upload_survives_restart_and_reset_restores_default(self):
        before = self.brand.status()
        after = self.brand.update({'admin': True}, {'action': 'upload', 'image': base64.b64encode(png()).decode()})
        self.assertTrue(after['custom']); self.assertNotEqual(after['revision'], before['revision'])
        self.assertEqual(struct.unpack('>II', self.brand.image()[16:24]), (512, 512))
        self.assertEqual(Branding(self.root, self.default).status(), after)
        self.assertEqual(self.brand.update({'admin': True}, {'action': 'reset'}), before)

    def test_non_admin_cannot_upload_or_reset(self):
        for action in ('upload', 'reset'):
            with self.assertRaises(PermissionError): self.brand.update({'admin': False}, {'action': action})

    def test_invalid_or_oversized_images_do_not_replace_existing_icon(self):
        original = self.brand.image()
        invalid = [b'<svg onload="alert(1)"/>', PNG+b'x'*40, png()[:30], png(2049, 32)]
        for image in invalid:
            with self.assertRaises(ValueError):
                self.brand.update({'admin': True}, {'action': 'upload', 'image': base64.b64encode(image).decode()})
        with self.assertRaises(ValueError): self.brand.update({'admin': True}, {'action': 'upload', 'image': '!'*100})
        with self.assertRaises(ValueError): self.brand.update({'admin': True}, {'action': 'upload', 'image': 'a'*1400000})
        self.assertEqual(self.brand.image(), original)

    def test_decoder_failure_preserves_previous_image(self):
        original = self.brand.image()
        with patch('app.branding.subprocess.run', side_effect=OSError('unavailable')):
            with self.assertRaises(ValueError): self.brand.update({'admin': True}, {'action':'upload','image':base64.b64encode(png()).decode()})
        self.assertEqual(self.brand.image(), original)


class BrandingHTTPTests(unittest.TestCase):
    tearDown = fixtures.PartyHTTPTests.tearDown
    call = fixtures.PartyHTTPTests.call
    def setUp(self):
        fixtures.PartyHTTPTests.setUp(self)
        self.s.branding = Branding(self.tmp.name, server.WEB/'assets/icon-512.png')
        self.csrf = self.a.csrf(self.session)

    def test_public_branding_and_admin_csrf_boundary(self):
        status, raw, _ = self.call('/api/v1/branding'); self.assertEqual(status, 200)
        uri = json.loads(raw)['icon_url']
        status, image, headers = self.call(uri); self.assertEqual(status, 200)
        self.assertTrue(image.startswith(PNG)); self.assertEqual(headers['Content-Type'], 'image/png')
        self.assertEqual(self.call('/api/v1/admin/branding', {'action':'reset'})[0], 403)
        self.assertEqual(self.call('/api/v1/admin/branding', {'action':'reset'}, self.hostcookie, self.csrf)[0], 403)
        self.a.approve('partyhost', admin=True)
        self.assertEqual(self.call('/api/v1/admin/branding', {'action':'reset'}, self.hostcookie, 'bad')[0], 403)
        self.assertEqual(self.call('/api/v1/admin/branding', {'action':'reset'}, self.hostcookie, self.csrf, origin='https://other.invalid')[0], 403)
        self.assertEqual(self.call('/api/v1/admin/branding', {'action':'reset'}, self.hostcookie, self.csrf)[0], 200)

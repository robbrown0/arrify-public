import threading
import time
import unittest

from app.artwork_reads import ArtworkReads


class ArtworkReadTests(unittest.TestCase):
    def test_stalled_reads_are_bounded_coalesced_and_release_http_callers(self):
        gate = threading.Event()
        calls = []
        reader = ArtworkReads(workers=1, capacity=2, timeout=0.025)
        def stalled():
            calls.append('read'); gate.wait(2); return b'art'
        try:
            self.assertEqual(reader.read('one', stalled, b'fallback'), b'fallback')
            self.assertEqual(reader.read('one', stalled, b'fallback'), b'fallback')
            self.assertEqual(reader.read('two', stalled, b'fallback'), b'fallback')
            started = time.monotonic()
            self.assertEqual(reader.read('overflow', stalled, b'fallback'), b'fallback')
            self.assertLess(time.monotonic()-started, 0.1)
            self.assertEqual(calls, ['read'])
            self.assertEqual(len(reader.pending), 2)
        finally:
            gate.set(); reader.queue.join()
        self.assertEqual(reader.read('recovered', lambda: b'ready', b'fallback'), b'ready')

    def test_an_unavailable_file_falls_back_without_killing_worker(self):
        reader = ArtworkReads(timeout=0.1)
        def unavailable(): raise OSError('storage unavailable')
        self.assertEqual(reader.read(1, unavailable, b'fallback'), b'fallback')
        self.assertEqual(reader.read(2, lambda: b'ready', b'fallback'), b'ready')

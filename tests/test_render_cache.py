import io
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from app.render_cache import RenderCache
from app.server import Handler

class RenderCacheTests(unittest.TestCase):
    def test_aac_is_seekable_and_repeated_requests_reuse_identical_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'tone.wav'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=duration=2',str(source)],check=True)
            cache=RenderCache(directory)
            output=cache.render(source,'aac',0)
            data=output.read_bytes()
            self.assertLess(data.index(b'moov'),data.index(b'mdat'))
            with patch('app.render_cache.subprocess.run',side_effect=AssertionError('rendered twice')):
                self.assertEqual(cache.render(source,'aac',0).read_bytes(),data)
            self.assertNotEqual(cache.render(source,'aac',-3),output)

    def test_failed_render_does_not_publish_partial_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'bad';source.write_bytes(b'bad')
            with self.assertRaises(subprocess.CalledProcessError):RenderCache(directory).render(source,'aac',0)
            self.assertEqual(list((Path(directory)/'stream-cache').iterdir()),[])

    def test_http_ranges_and_head_use_same_rendered_resource(self):
        from email.message import Message
        with tempfile.TemporaryDirectory() as directory:
            resource=Path(directory)/'song.m4a';resource.write_bytes(b'0123456789')
            for method,requested,code,body in [('GET','bytes=2-5',206,b'2345'),('GET','bytes=-3',206,b'789'),('HEAD','bytes=0-1',206,b''),('GET','bytes=99-',416,b'')]:
                h=Handler.__new__(Handler);h.command=method;h.headers=Message();h.headers['Range']=requested;h.wfile=io.BytesIO()
                headers={};statuses=[]
                h.send_response=statuses.append;h.send_header=lambda key,value:headers.update({key:value});h.end_headers=lambda:None
                h.send=lambda status,**kw:statuses.append(status)
                h.identity=lambda:{'id':1}
                cache=SimpleNamespace(open=lambda *args:resource.open('rb'))
                h.server=SimpleNamespace(player=None,transcodes=threading.BoundedSemaphore(2),render_cache=cache,sound=SimpleNamespace(status=lambda *args:{'gain_db':0,'status':'off'}),library=SimpleNamespace(music=SimpleNamespace(_track_file=lambda tid:({'mime_type':'audio/flac'},'source'))))
                from app.connections import ActiveStreams
                h.server.active_streams=ActiveStreams()
                with patch('app.server.Listening') as listening:
                    listening.return_value.stream_allowed.return_value=True
                    h.stream(1,{'profile':['aac']})
                self.assertEqual(statuses,[code]);self.assertEqual(h.wfile.getvalue(),body)
                self.assertEqual(h.server.active_streams.snapshot()['items'],[])
                if code==206:
                    self.assertEqual(headers['Content-Type'],'audio/mp4');self.assertEqual(headers['Accept-Ranges'],'bytes')
                    self.assertEqual(headers['Content-Length'],'2' if method=='HEAD' else str(len(body)))

    def test_render_validator_survives_lru_timestamp_touch(self):
        from email.message import Message
        import os
        with tempfile.TemporaryDirectory() as directory:
            resource=Path(directory)/'song.m4a';resource.write_bytes(b'0123456789')
            tags=[]
            for _ in range(2):
                h=Handler.__new__(Handler);h.command='HEAD';h.headers=Message();h.wfile=io.BytesIO()
                h.send_response=lambda code:None;h.end_headers=lambda:None
                h.send_header=lambda key,value:tags.append(value) if key=='ETag' else None
                with resource.open('rb') as audio:h.serve_audio(audio,'audio/mp4',lambda:True,immutable=True)
                os.utime(resource,ns=(123456789,123456789))
            self.assertEqual(tags[0],tags[1])

    def test_cached_audio_bypasses_unrelated_render_and_exhausted_slots(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as directory:
            cache=RenderCache(directory)
            a=Path(directory)/'a';a.write_bytes(b'a')
            b=Path(directory)/'b';b.write_bytes(b'b')
            def render(args,**kwargs):Path(args[-1]).write_bytes(b'audio')
            with patch('app.render_cache.subprocess.run',side_effect=render):cache.render(a,'mp3',0)
            entered=threading.Event();release=threading.Event()
            def slow(args,**kwargs):
                entered.set()
                if not release.wait(3):raise AssertionError('Render was not released')
                render(args)
            def read_cached():
                with cache.open(a,'mp3',0,threading.BoundedSemaphore(0)) as audio:return audio.read()
            with ThreadPoolExecutor(2) as pool,patch('app.render_cache.subprocess.run',side_effect=slow):
                pending=pool.submit(cache.render,b,'mp3',0)
                try:
                    self.assertTrue(entered.wait(1))
                    self.assertEqual(pool.submit(read_cached).result(timeout=1),b'audio')
                finally:release.set()
                pending.result(timeout=2)

    def test_open_cached_audio_survives_eviction(self):
        with tempfile.TemporaryDirectory() as directory:
            cache=RenderCache(directory,limit=5)
            a=Path(directory)/'a';a.write_bytes(b'a')
            b=Path(directory)/'b';b.write_bytes(b'b')
            def render(args,**kwargs):Path(args[-1]).write_bytes(b'audio')
            with patch('app.render_cache.subprocess.run',side_effect=render):
                old=cache.render(a,'mp3',0)
                with cache.open(a,'mp3',0,threading.BoundedSemaphore(0)) as audio:
                    cache.render(b,'mp3',0)
                    self.assertFalse(old.exists())
                    self.assertEqual(audio.read(),b'audio')

    def test_uncached_audio_still_obeys_conversion_limit(self):
        from app.render_cache import RenderBusy
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source';source.write_bytes(b'x')
            with self.assertRaises(RenderBusy):
                with RenderCache(directory).open(source,'mp3',0,threading.BoundedSemaphore(0)):pass

class ParallelRenderTests(unittest.TestCase):
    def test_distinct_misses_can_render_together_without_removing_temporary_files(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as directory:
            cache=RenderCache(directory);a=Path(directory)/'a';b=Path(directory)/'b';a.write_bytes(b'a');b.write_bytes(b'b')
            entered=threading.Barrier(2);release=threading.Event()
            def render(args,**kwargs):
                tmp=Path(args[-1]);tmp.write_bytes(b'audio');entered.wait(timeout=2)
                if str(a)==args[args.index('-i')+1]:release.wait(2);self.assertTrue(tmp.exists())
                else:release.set()
            with ThreadPoolExecutor(2) as pool,patch('app.render_cache.subprocess.run',side_effect=render):
                jobs=[pool.submit(cache.render,x,'mp3',0,threading.BoundedSemaphore(2)) for x in [a,b]]
                for job in jobs:self.assertEqual(job.result(timeout=3).read_bytes(),b'audio')
            self.assertEqual(cache.render_locks,{})
    def test_same_rendition_renders_once(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as directory:
            cache=RenderCache(directory);a=Path(directory)/'a';a.write_bytes(b'a');calls=[]
            def render(args,**kwargs):calls.append(1);Path(args[-1]).write_bytes(b'audio')
            with ThreadPoolExecutor(4) as pool,patch('app.render_cache.subprocess.run',side_effect=render):
                jobs=[pool.submit(cache.render,a,'mp3',0) for _ in range(4)]
                self.assertEqual(len({str(j.result()) for j in jobs}),1)
            self.assertEqual(len(calls),1);self.assertEqual(cache.render_locks,{})

    def test_source_change_during_render_is_not_published(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'song';source.write_bytes(b'original')
            cache=RenderCache(directory)
            def render(args,**kwargs):
                Path(args[-1]).write_bytes(b'mixed audio')
                source.write_bytes(b'changed!')
                # Guarantee a changed timestamp even on coarse-resolution filesystems.
                import os
                stamp=source.stat()
                os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns+1_000_000_000))
            with patch('app.render_cache.subprocess.run',side_effect=render):
                with self.assertRaisesRegex(ValueError,'Source changed'):
                    cache.render(source,'aac',0)
            self.assertEqual(list(cache.root.iterdir()),[])

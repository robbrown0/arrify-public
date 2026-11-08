import http.client
import json
import socket
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from hitdiscovery import request_service as r
from hitdiscovery.store import Store

class ServerTests(unittest.TestCase):
    def test_metadata_does_not_block_status_and_duplicate_submissions_are_serialized(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'inbox').mkdir();(root/'status').mkdir()
            cfg=SimpleNamespace(data=root,contact='https://example.invalid')
            mbid='11111111-1111-1111-1111-111111111111'
            s=Store(root/'discovery.sqlite3');s.cache('arrify-selection:'+mbid,{'recording_id':mbid,'title':'Fixture','artist':'Fixture'});s.db.close()
            server=r.UnixServer(str(root/'socket'),r.Handler);server.config=cfg
            started=threading.Event();release=threading.Event()
            def slow(*args):started.set();release.wait(5);return {'items':[]}
            class Connection(http.client.HTTPConnection):
                def connect(self):self.sock=socket.socket(socket.AF_UNIX);self.sock.settimeout(4);self.sock.connect(str(root/'socket'))
            def call(path,data=None):
                c=Connection('localhost');c.request('POST' if data else 'GET',path,body=json.dumps(data) if data else None,headers={'Content-Type':'application/json'});response=c.getresponse();result=response.status,json.loads(response.read());c.close();return result
            with patch.object(r,'ROOT',root),patch.object(r,'search',side_effect=slow):
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                try:
                    with ThreadPoolExecutor(max_workers=3) as pool:
                        search=pool.submit(call,'/search?q=fixture');self.assertTrue(started.wait(2))
                        self.assertEqual(call('/status/'+'0'*32)[0],200)
                        first=pool.submit(call,'/requests',{'selection':mbid,'user_id':1})
                        second=pool.submit(call,'/requests',{'selection':mbid,'user_id':2})
                        self.assertEqual(first.result()[1]['id'],second.result()[1]['id'])
                        self.assertEqual(len(list((root/'inbox').glob('*.json'))),1)
                        release.set();self.assertEqual(search.result()[0],200)
                finally:release.set();server.shutdown();server.server_close();thread.join()

    def test_preparing_radio_selection_only_caches_metadata_without_requesting(self):
        from test_request_catalog import recording,RID
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'inbox').mkdir();(root/'status').mkdir()
            cfg=SimpleNamespace(data=root,contact='https://example.invalid')
            rec=recording()
            class Response:
                def __init__(self,data):self.data=data
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def read(self,*args):return json.dumps(self.data).encode()
            def fetch(request,**kwargs):return Response({'releases':rec['releases']} if '/release/?' in request.full_url else rec)
            with patch.object(r,'ROOT',root),patch.object(r,'urlopen',side_effect=fetch),patch.object(r.time,'sleep'):
                result=r.search(cfg,'',recording_id=RID)
            self.assertEqual(len(result['items']),1);self.assertTrue(result['items'][0]['requestable'])
            store=Store(root/'discovery.sqlite3');saved=store.cached('arrify-selection:'+result['items'][0]['selection']);store.db.close()
            self.assertEqual(saved['resolved']['album'],'Original Album');self.assertEqual(list((root/'inbox').glob('*.json')),[])

    def test_named_song_lookup_caches_verified_release_without_submitting(self):
        from test_request_catalog import recording
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'inbox').mkdir();(root/'status').mkdir();cfg=SimpleNamespace(data=root,contact='https://example.invalid');rec=recording()
            class Response:
                def __init__(self,data):self.data=data
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def read(self,*args):return json.dumps(self.data).encode()
            def fetch(request,**kwargs):
                if '/release/?' in request.full_url:return Response({'releases':rec['releases']})
                if '/recording/?' in request.full_url:return Response({'recordings':[rec],'recording-count':1})
                return Response(rec)
            with patch.object(r,'ROOT',root),patch.object(r,'urlopen',side_effect=fetch),patch.object(r.time,'sleep'):
                d=r.search(cfg,'',song={'artist':'Test Artist','title':'Test Song'})
                self.assertEqual(d['requested']['title'],'Test Song');self.assertNotIn('resolved',d['items'][0]);store=Store(root/'discovery.sqlite3');item=store.cached('arrify-selection:'+d['items'][0]['selection']);store.db.close();self.assertEqual(item['resolved']['album'],'Original Album');self.assertEqual(list((root/'inbox').glob('*.json')),[])

    def test_song_verification_failure_has_clear_error_and_no_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);server=r.UnixServer(str(root/'socket'),r.Handler);server.config=SimpleNamespace(data=root,contact='https://example.invalid')
            class Connection(http.client.HTTPConnection):
                def connect(self):self.sock=socket.socket(socket.AF_UNIX);self.sock.settimeout(4);self.sock.connect(str(root/'socket'))
            with patch.object(r,'search',side_effect=ValueError('No exact album match. Nothing was requested.')):
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                try:
                    c=Connection('localhost');c.request('GET','/song?artist=Artist&title=Song');response=c.getresponse();self.assertEqual(response.status,400);self.assertIn('Nothing was requested',json.loads(response.read())['error']);c.close();self.assertFalse((root/'inbox').exists())
                finally:server.shutdown();server.server_close();thread.join()

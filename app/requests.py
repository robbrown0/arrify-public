"""Narrow local adapter; public routes never accept arbitrary upstream URLs."""
import http.client
import json
import socket
from urllib.parse import urlencode

class UnixHTTP(http.client.HTTPConnection):
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.sock.settimeout(65)
        self.sock.connect('/requests/request.sock')

def call(path,data=None):
    conn=UnixHTTP('localhost',timeout=65)
    try:
        conn.request('POST' if data is not None else 'GET',path,body=json.dumps(data) if data is not None else None,headers={'Content-Type':'application/json'})
        response=conn.getresponse();value=json.loads(response.read(1024*1024))
        if response.status>=400:raise ValueError(value.get('error','Request service unavailable.'))
        return value
    except (OSError,TimeoutError,http.client.HTTPException,json.JSONDecodeError):raise ValueError('Request service is temporarily unavailable. Try again shortly.') from None
    finally:conn.close()

def search(query,offset=0):return call('/search?'+urlencode({'q':query,'offset':offset}))

def selection(recording_id):return call('/selection?'+urlencode({'recording_id':recording_id}))

def song(artist,title):return call('/song?'+urlencode({'artist':artist,'title':title}))

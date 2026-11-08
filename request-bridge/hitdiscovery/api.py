import json
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from pathlib import Path

class APIError(RuntimeError): pass

class HTTP:
    def __init__(self, store=None):
        self.store=store; self.last={}
    def json(self,url,method='GET',body=None,headers=None,interval=0,cache_ttl=0):
        if cache_ttl and self.store:
            saved=self.store.cached(url,cache_ttl)
            if saved is not None: return saved
        host=url.split('/')[2]
        for attempt in range(4 if method=='GET' else 1):
            time.sleep(max(0,interval-(time.monotonic()-self.last.get(host,0))))
            self.last[host]=time.monotonic()
            req=Request(url, data=None if body is None else json.dumps(body).encode(), method=method,
                        headers={'Accept':'application/json','Content-Type':'application/json',**(headers or {})})
            try:
                with urlopen(req,timeout=45) as r:
                    raw=r.read(); result=json.loads(raw) if raw else None
                if cache_ttl and self.store: self.store.cache(url,result)
                return result
            except HTTPError as e:
                if e.code in (429,500,502,503,504) and method=='GET' and attempt<3:
                    delay=e.headers.get('Retry-After','')
                    time.sleep(min(60,float(delay)) if delay.isdigit() else 2**(attempt+1)); continue
                raise APIError(f'{method} {host}{req.selector.split("?")[0]} HTTP {e.code}') from None
            except (URLError,TimeoutError,OSError) as e:
                if method=='GET' and attempt<3:
                    time.sleep(2**attempt); continue
                raise APIError(f'{method} {host}: connectivity failure ({type(e).__name__})') from None
        raise APIError('Retries exhausted')

class Lidarr:
    def __init__(self,config,http):
        self.c=config; self.http=http
        self.headers={'X-Api-Key':Path(config.key_file).read_text().strip()}
        if not self.headers['X-Api-Key']: raise APIError('Lidarr API key file is empty')
    def get(self,path,**params):
        return self.http.json(self.c.url.rstrip('/')+'/api/v1/'+path+('?' + urlencode(params,doseq=True) if params else ''),headers=self.headers,interval=.05)
    def write(self,path,method,body):
        if not (self.c.acquire and self.c.reviewed): raise APIError('Acquisition disabled: review initial report and enable configuration')
        if self.http.store:
            self.http.store.audit('lidarr-write-intent',{'path':path,'method':method,'albumIds':body.get('albumIds',[]),'foreignAlbumId':body.get('foreignAlbumId'),'foreignArtistId':body.get('foreignArtistId')})
        return self.http.json(self.c.url.rstrip('/')+'/api/v1/'+path,method,body,self.headers)
    def capabilities(self):
        status=self.get('system/status')
        # Release-tagged schema, never the moving develop branch.
        version=status['version']
        schema=self.http.json(f'https://raw.githubusercontent.com/Lidarr/Lidarr/v{version}/src/Lidarr.Api.V1/openapi.json',cache_ttl=86400*30)
        required={'/api/v1/artist':'post','/api/v1/album':'post','/api/v1/album/monitor':'put','/api/v1/command':'post','/api/v1/qualityprofile':'post','/api/v1/metadataprofile':'post','/api/v1/album/{id}':'put','/api/v1/artist/{id}':'put'}
        for path,method in required.items():
            if method not in schema.get('paths',{}).get(path,{}): raise APIError(f'Unsupported capability {method} {path}')
        return {'version':version,'schema_url':f'https://github.com/Lidarr/Lidarr/tree/v{version}', 'operations':required}

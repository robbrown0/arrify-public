/* Ephemeral local-file read-ahead. Bounded temporary storage; no cross-client sharing. */
'use strict';
// Browser storage owns payload bytes. JavaScript retains metadata and Blob/File handles.
class ArrifyAudioFiles {
 constructor(directory){this.directory=directory}
 static async open(provider){
  if(!provider)return null;
  try{
   const root=await provider();if(!root)return null;
   const directory=await root.getDirectoryHandle('arrify-audio-files-v1',{create:true});
   const probe=await directory.getFileHandle('probe',{create:true});
   if(typeof probe.createWritable!=='function'){await directory.removeEntry('probe');return null}
   const writer=await probe.createWritable();
   try{
    await writer.write({type:'write',position:0,data:new Blob(['arrify-'])});
    await writer.write({type:'write',position:7,data:new Blob(['probe'])});
    await writer.close();
    const check=await(await directory.getFileHandle('probe')).getFile();
    if(await check.text()!=='arrify-probe')throw Error('Storage round-trip failed');
   }catch(error){try{await writer.abort()}catch{}throw error}
   finally{await directory.removeEntry('probe')}
   return new ArrifyAudioFiles(directory);
  }catch{return null}
 }
 async key(owner,key){
  const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(owner+'|'+key));
  return Array.from(new Uint8Array(hash),x=>x.toString(16).padStart(2,'0')).join('');
 }
 async remove(name){for(const suffix of ['.json','.audio'])try{await this.directory.removeEntry(name+suffix)}catch{}}
 async prune(keep){
  for await(const name of this.directory.keys()){
   if(!keep.has(name.replace(/\.(json|audio)$/,'')))try{await this.directory.removeEntry(name)}catch{}
  }
 }
 async restore(name,maxFile){
  try{
   const metaFile=await(await this.directory.getFileHandle(name+'.json')).getFile();
   if(metaFile.size>2048)throw Error('Invalid metadata');
   const meta=JSON.parse(await metaFile.text());
   if(meta.expires<Date.now()||!Number.isFinite(meta.expires)||!Number.isSafeInteger(meta.total)||meta.total<=0||meta.total>maxFile||!meta.type?.startsWith('audio/'))throw Error('Invalid metadata');
   const handle=await this.directory.getFileHandle(name+'.audio'),file=await handle.getFile();
   if(file.size!==meta.total)throw Error('Incomplete file');
   return {meta,handle,file};
  }catch{await this.remove(name);return null}
 }
 async create(name){return this.directory.getFileHandle(name+'.audio',{create:true})}
 async commit(name,e){
  const handle=await this.directory.getFileHandle(name+'.json',{create:true}),writer=await handle.createWritable();
  try{await writer.write(JSON.stringify({total:e.total,type:e.type,etag:e.etag,expires:Date.now()+10*60*1000}));await writer.close()}
  catch(error){try{await writer.abort()}catch{}throw error}
 }
}

class ArrifyDeviceBuffer {
 constructor({fetcher=(...args)=>fetch(...args),origin=self.location.origin,maxBytes=96*1024*1024,maxFile=32*1024*1024,storage=self.caches,idleTimeoutMs=30000,headerTimeoutMs=120000,retryDelays=[500,1500,4000],retryCooldownMs=30000,directoryProvider=()=>self.navigator?.storage?.getDirectory?.(),diskBytes=512*1024*1024,diskFile=256*1024*1024}={}){this.memoryBytes=maxBytes;this.memoryFile=maxFile;this.directoryProvider=directoryProvider;this.diskBytes=diskBytes;this.diskFile=diskFile;this.files=null;this.diskInit=null;this.idleTimeoutMs=idleTimeoutMs;this.headerTimeoutMs=headerTimeoutMs;this.retryDelays=retryDelays;this.retryCooldownMs=retryCooldownMs;this.storage=storage;this.cacheName='arrify-audio-buffer-v1';this.cleanup=Promise.resolve();this.fetcher=fetcher;this.origin=origin;this.maxBytes=maxBytes;this.maxFile=maxFile;this.entries=new Map();this.failures=new Map();this.plan=[];this.client='';this.scope='';this.serial=0;this.running=null;this.bytes=0;this.until=0;this.cleared=false;}
 async initDisk(){
  if(!this.diskInit)this.diskInit=ArrifyAudioFiles.open(this.directoryProvider).then(files=>{this.files=files;if(files){this.maxBytes=this.diskBytes;this.maxFile=this.diskFile}});
  await this.diskInit;
 }
 async storageWork(work){try{return await work()}catch(error){error.cacheStorage=true;throw error}}
 reserved(except){return [...this.entries.values()].reduce((n,e)=>n+(e!==except&&!e.error?e.total:0),0)}
 valid(item,e){return this.entries.get(item.key)===e&&!e.controller.signal.aborted}
 snapshot(client){
  // A restarted worker has not yet reconstructed its plan; unknown is not zero.
  if(!this.client&&!this.cleared)return null;
  const active=client===this.client&&Date.now()<this.until;
  const items=active?this.plan.map(item=>{const e=this.entries.get(item.key),failure=this.failures.get(item.key);return {failure_reason:failure?.reason||'',id:Number(new URL(item.url,this.origin).pathname.split('/').pop()),loaded_bytes:e?.loaded||0,total_bytes:e?.total||0,state:!e?(failure?'retrying':'queued'):e.skipped?(e.skipReason||'oversized'):e.done?'ready':e.failed?'partial':e.retrying?'retrying':e.loaded?'downloading':'waiting',retries:e?.retries||0}}):[];
  return {worker_version:'20261108.2',state:active?'active':'inactive',downloaded_bytes:items.reduce((sum,x)=>sum+x.loaded_bytes,0),limit_bytes:this.maxBytes,storage:this.files?'disk':'memory',items};
 }
 key(raw){try{const u=new URL(raw,this.origin);if(u.origin!==this.origin||!/^\/api\/v1\/stream\/[0-9]+$/.test(u.pathname)||!['mp3','aac','original'].includes(u.searchParams.get('profile')))return null;return u.pathname+'?'+['profile','level','device_id','owner_epoch'].map(k=>k+'='+encodeURIComponent(u.searchParams.get(k)||'')).join('&')}catch{return null}}
 clear(){this.cleared=true;const pending=[...this.entries.values()].map(e=>e.finished);this.cleanup=this.cleanup.then(()=>Promise.allSettled(pending)).then(()=>Promise.all([this.storage?.delete(this.cacheName),this.storage?.delete('arrify-audio-policy-v1'),this.files?.prune(new Set())])).catch(()=>{});++this.serial;for(const e of this.entries.values()){e.controller.abort();e.error=true;e.headersResolve?.(false);this.wake(e)}this.entries.clear();this.failures.clear();this.bytes=0;this.plan=[];this.client='';this.scope='';this.running=null;this.policySignature='';this.policyAt=0;}
 wake(e){for(const f of e.waiters.splice(0))f()}
 discard(key){const e=this.entries.get(key);if(!e)return;e.controller.abort();e.error=true;e.headersResolve?.(false);this.wake(e);this.bytes-=e.loaded;this.entries.delete(key)}
 update(client,data){
  if(!data.enabled||!data.scope){if((!this.client&&!this.cleared)||client===this.client)this.clear();return this.cleanup}
  const urls=Array.isArray(data.urls)?data.urls.slice(0,3).filter(u=>this.key(u)):[];
  if(!urls.length){if((!this.client&&!this.cleared)||client===this.client)this.clear();return this.cleanup}
  if(this.client&&(client!==this.client||data.scope!==this.scope))this.clear();
  this.cleared=false;this.client=client;this.scope=data.scope;this.until=Date.now()+10*60*1000;
  const next=urls.map(url=>({url,key:this.key(url)})),signature=next.map(x=>x.key).join('|');
  if(this.storage&&(this.policySignature!==signature||Date.now()-(this.policyAt||0)>60000)){
   this.policySignature=signature;this.policyAt=Date.now();const policy={enabled:true,scope:data.scope,urls,until:this.until};
   this.cleanup=this.cleanup.then(async()=>{const cache=await this.storage.open('arrify-audio-policy-v1');await cache.put(this.origin+'/_buffer-policy/'+encodeURIComponent(client),new Response(JSON.stringify(policy)))}).catch(()=>{});
  }
  if(signature===this.signature&&this.running)return this.running;
  const previous=this.running,changed=signature!==this.signature;this.signature=signature;const run=++this.serial;this.plan=next;
  // A later queue change must not cancel retained in-flight audio.
  for(const [key,e] of this.entries)if(!next.some(x=>x.key===key)||changed&&e.skipped)this.discard(key);
  for(const key of this.failures.keys())if(!next.some(x=>x.key===key))this.failures.delete(key);
  this.running=(async()=>{await previous;await this.cleanup;await this.initDisk();if(run!==this.serial)return;
   if(this.files){const owner=this.client+'|'+this.scope;const names=await Promise.all(next.map(item=>this.files.key(owner,item.key)));if(run!==this.serial)return;await this.files.prune(new Set(names));}
   for(const item of next){if(run!==this.serial)return;try{const e=this.obtain(item);await e.finished;if(e.failed||e.error&&!e.skipped)return}catch{return}}})().finally(()=>{if(run===this.serial)this.running=null});
  return this.running;
 }
 obtain(item){
  const failure=this.failures.get(item.key);
  if(failure&&Date.now()<failure.retryAt)return {failed:true,finished:Promise.resolve()};
  this.failures.delete(item.key);
  if(this.entries.has(item.key)){
   const existing=this.entries.get(item.key);
   if(existing.failed&&Date.now()>=existing.retryAt){
    if(existing.loaded&&existing.etag){existing.failed=false;existing.finished=this.download(item,existing);}
    else {this.discard(item.key);return this.obtain(item);}
   }
   return existing;
  }
  const owner=this.client+'|'+this.scope;const e={controller:new AbortController(),chunks:[],loaded:0,total:0,done:false,error:false,waiters:[]};this.entries.set(item.key,e);
  e.cacheReady=new Promise(resolve=>e.cacheResolve=resolve);
  e.headersReady=new Promise(resolve=>e.headersResolve=resolve);
  e.finished=this.download(item,e,owner);return e;
 }
 async download(item,e,owner=this.client+'|'+this.scope){
  let disk=e.disk,key=e.diskKey,cached;
  try{
   await this.cleanup;await this.initDisk();
   if(!this.valid(item,e))throw this.fatal('Cancelled');
   if(this.files&&!e.loaded){
    e.fileName=await this.files.key(owner,item.key);
    const saved=await this.files.restore(e.fileName,this.maxFile);
    if(!this.valid(item,e))throw this.fatal('Cancelled');
    if(saved){
     if(this.reserved(e)+saved.meta.total>this.maxBytes){e.skipped=true;e.skipReason='budget';throw this.fatal('Cache budget')}
     Object.assign(e,{total:saved.meta.total,type:saved.meta.type,etag:saved.meta.etag,file:saved.file,fileHandle:saved.handle,loaded:saved.meta.total,done:true,initialized:true});
     this.bytes+=e.loaded;e.headersResolve(true);e.cacheResolve();return;
    }
    e.fileHandle=await this.files.create(e.fileName);
   }
   if(!this.files&&!e.loaded&&this.storage){try{
    const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(owner));
    const scope=Array.from(new Uint8Array(digest),b=>b.toString(16).padStart(2,'0')).join('');
    key=this.origin+'/_device-buffer/'+scope+'/'+encodeURIComponent(item.key);
    disk=await this.storage.open(this.cacheName);cached=await disk.match(key);
    if(cached&&Number(cached.headers.get('X-Buffer-Expires'))<Date.now()){await disk.delete(key);cached=null}
   }catch{disk=null;cached=null}}
   e.disk=disk;e.diskKey=key;
   if(cached){await this.transfer(item,e,cached);e.done=true;this.wake(e);e.cacheResolve();return}
   e.cacheResolve();
   for(let attempt=0;;attempt++){
    if(e.controller.signal.aborted)throw this.fatal('Cancelled');
    try{e.retrying=false;await this.transfer(item,e,cached);break}
    catch(error){
     if(error.fatal||error.cacheStorage||e.controller.signal.aborted||['QuotaExceededError','NotAllowedError','NoModificationAllowedError'].includes(error.name))throw error;
     // An unvalidated partial representation must never be spliced into a retry.
     if(e.initialized&&!e.etag||attempt>=this.retryDelays.length){e.failed=true;e.retryAt=Date.now()+this.retryCooldownMs;e.headersResolve(false);this.wake(e);return}
     e.retrying=true;e.retries=(e.retries||0)+1;await this.delay(this.retryDelays[attempt],e.controller.signal);
    }
   }
   if(e.fileHandle){
    if(!this.valid(item,e))throw this.fatal('Cancelled');
    e.file=await this.storageWork(()=>e.fileHandle.getFile());
    if(e.file.size!==e.total){
     // An acknowledged write is not proof of durable bytes. Fall back once
     // per worker to bounded memory instead of repeating a broken disk path.
     const files=this.files;await files.remove(e.fileName);
     if(!this.valid(item,e))throw this.fatal('Cancelled');
     for(const [other,entry] of this.entries)if(entry!==e)this.discard(other);
     this.bytes-=e.loaded;this.files=null;this.maxBytes=this.memoryBytes;this.maxFile=this.memoryFile;
     Object.assign(e,{loaded:0,total:0,initialized:false,fileHandle:null,file:null,fileName:null,chunks:[],etag:null});
     return await this.download(item,e,owner);
    }
    await this.storageWork(()=>this.files.commit(e.fileName,e));
    if(!this.valid(item,e))throw this.fatal('Cancelled');
   }
   e.done=true;this.wake(e);
    if(disk&&!cached&&this.entries.get(item.key)===e){
     // Completed files only. Cache is temporary, session/client scoped and capped at three.
     try{
      const keys=await disk.keys();for(const old of keys)if(old.url!==key&&(!old.url.includes('/'+key.split('/_device-buffer/')[1].split('/')[0]+'/')||!this.plan.some(x=>old.url.endsWith('/'+encodeURIComponent(x.key)))))await disk.delete(old);
      const remaining=await disk.keys();for(const old of remaining.slice(0,Math.max(0,remaining.length-2)))await disk.delete(old);
      if(this.entries.get(item.key)===e&&!e.controller.signal.aborted){await disk.put(key,new Response(new Blob(e.chunks.map(x=>x.data),{type:e.type}),{headers:{'Content-Type':e.type,'Content-Length':String(e.total),...(e.etag?{'ETag':e.etag}:{}),'X-Buffer-Expires':String(Date.now()+10*60*1000)}}));if(this.entries.get(item.key)!==e||e.controller.signal.aborted)await disk.delete(key)}
     }catch{} // Quota/private-mode failures fall back to the in-memory buffer.
    }
  }catch(error){
   if(e.fileName)await this.files.remove(e.fileName);
   if(error.cacheStorage||error.name==='QuotaExceededError'||error.name==='NotAllowedError'||error.name==='NoModificationAllowedError'){e.skipped=true;e.skipReason='storage-full'}
   e.cacheResolve();e.error=true;e.controller.abort();e.headersResolve(false);this.wake(e);
   if(this.entries.get(item.key)===e){this.bytes-=e.loaded;e.loaded=0;e.chunks=[];if(!e.skipped){this.entries.delete(item.key);this.failures.set(item.key,{retryAt:Date.now()+this.retryCooldownMs,reason:error.reason||'download-failed'})}}
  }
 }
 fatal(message){return Object.assign(Error(message),{fatal:true,reason:({'Not bufferable':'unsupported-response','Representation changed or invalid resume':'representation-changed','Incomplete disk file':'storage-incomplete','Buffer limit':'buffer-limit'})[message]||'download-failed'})}
 delay(ms,signal){return new Promise((resolve,reject)=>{
  const finish=()=>{signal.removeEventListener('abort',cancel);resolve()};
  const cancel=()=>{clearTimeout(timer);signal.removeEventListener('abort',cancel);reject(this.fatal('Cancelled'))};
  const timer=setTimeout(finish,ms);signal.addEventListener('abort',cancel,{once:true});if(signal.aborted)cancel();
 })}
 async transfer(item,e,cached){
  const attempt=new AbortController();let timer,reader,writer;
  const cancel=()=>attempt.abort();e.controller.signal.addEventListener('abort',cancel,{once:true});
  const arm=ms=>{clearTimeout(timer);timer=setTimeout(()=>attempt.abort(),ms)};
  arm(this.headerTimeoutMs);
  try{
   if(e.controller.signal.aborted)throw this.fatal('Cancelled');
   const headers=e.initialized&&e.etag?{Range:'bytes='+e.loaded+'-','If-Range':e.etag}:{};
   const response=cached||await this.fetcher(item.url,{credentials:'same-origin',cache:'no-store',signal:attempt.signal,headers});
   reader=response.body?.getReader();
   if([401,403,409].includes(response.status)){this.clear();throw this.fatal('Session unavailable')}
   if([408,429,500,502,503,504].includes(response.status))throw Error('Temporary server failure');
   const size=Number(response.headers.get('Content-Length')),type=response.headers.get('Content-Type')||'',etag=response.headers.get('ETag');
   if(e.initialized){
    const range=/^bytes (\d+)-(\d+)\/(\d+)$/.exec(response.headers.get('Content-Range')||'');
    if(response.status!==206||etag!==e.etag||type!==e.type||!range||Number(range[1])!==e.loaded||Number(range[2])!==e.total-1||Number(range[3])!==e.total||size!==e.total-e.loaded)throw this.fatal('Representation changed or invalid resume');
   }else{
    if(response.status===200&&size>this.maxFile){e.skipped=true;throw this.fatal('File exceeds cache limit')}
    if(response.status!==200||!Number.isSafeInteger(size)||size<=0||size>this.maxFile||!type.startsWith('audio/')||!reader)throw this.fatal('Not bufferable');
    if(this.reserved(e)+size>this.maxBytes){e.skipped=true;e.skipReason='budget';throw this.fatal('Cache budget')}
    e.initialized=true;e.total=size;e.type=type;e.etag=etag&&/^"[^"\r\n]*"$/.test(etag)?etag:null;e.headersResolve(true);
   }
   if(!reader)throw this.fatal('Missing audio body');
   if(e.fileHandle){writer=await this.storageWork(()=>e.fileHandle.createWritable({keepExistingData:e.loaded>0}));if(e.loaded)await this.storageWork(()=>writer.seek(e.loaded))}
   arm(this.idleTimeoutMs);
   // Abort both fetch and a cached/custom reader when progress stops.
   const stopReader=()=>{reader.cancel().catch(()=>{})};attempt.signal.addEventListener('abort',stopReader,{once:true});
   try{
    for(;;){
     const {value,done}=await reader.read();
     if(attempt.signal.aborted)throw Error('No download progress');
     if(done)break;
     if(this.bytes+value.byteLength>this.maxBytes||e.loaded+value.byteLength>e.total)throw this.fatal('Buffer limit');
     if(value.byteLength){
      if(writer){for(let offset=0;offset<value.byteLength;offset+=1024*1024){
       if(e.controller.signal.aborted)throw this.fatal('Cancelled');
       const data=new Blob([value.subarray(offset,offset+1024*1024)]);
       await this.storageWork(()=>writer.write({type:'write',position:e.loaded+offset,data}));
      }}else e.chunks.push({offset:e.loaded,data:value});
      e.loaded+=value.byteLength;this.bytes+=value.byteLength;arm(this.idleTimeoutMs);this.wake(e)
     }
    }
    if(e.loaded!==e.total)throw Error('Incomplete audio');
   }finally{attempt.signal.removeEventListener('abort',stopReader)}
  }finally{
   clearTimeout(timer);attempt.abort();e.controller.signal.removeEventListener('abort',cancel);
   if(reader)try{await reader.cancel()}catch{}
   if(writer){if(e.controller.signal.aborted){try{await writer.abort()}catch{}}else await this.storageWork(()=>writer.close())}
  }
 }
 async handle(client,request,event){
  if(client&&!this.client&&!this.cleared&&this.storage){
   try{const cache=await this.storage.open('arrify-audio-policy-v1');const saved=await cache.match(this.origin+'/_buffer-policy/'+encodeURIComponent(client));if(saved){const policy=await saved.json();if(policy.until>Date.now()){const work=this.update(client,policy);event?.waitUntil(work)}}}catch{}
  }
  return this.handles(client,request)?this.response(request):this.fetcher(request);
 }
 handles(client,request){return client===this.client&&Date.now()<this.until&&request.method==='GET'&&this.plan.some(x=>x.key===this.key(request.url))}
 async response(request){
  const item=this.plan.find(x=>x.key===this.key(request.url));if(!item)return this.fetcher(request);
  const e=this.obtain(item);await e.cacheReady;
  // Never tie native playback to a live JS producer or a suspended read-ahead job.
  if(!e.initialized||e.error)return this.fetcher(request);
  const range=request.headers.get('Range');let start=0,end=e.total-1;
  if(range){const m=/^bytes=(\d*)-(\d*)$/.exec(range);if(!m||!m[1]&&!m[2])return this.fetcher(request);if(m[1]){start=Number(m[1]);if(m[2])end=Math.min(end,Number(m[2]))}else start=Math.max(0,e.total-Number(m[2]));if(start>end||start>=e.total)return new Response(null,{status:416,headers:{'Content-Range':'bytes */'+e.total}})}
  const headers={'Content-Type':e.type,'Content-Length':String(end-start+1),'Accept-Ranges':'bytes','Cache-Control':'private, no-store','X-Arrify-Device-Buffer':e.done?'complete':'filling'};
  if(range)headers['Content-Range']=`bytes ${start}-${end}/${e.total}`;
  // A complete requested range is a native Blob snapshot. In-flight downloads
  // continue independently; unavailable bytes use the normal authenticated stream.
  if(end>=e.loaded||e.fileHandle&&!e.done)return this.fetcher(request);
  if(e.fileHandle){try{e.file=await e.fileHandle.getFile();if(e.file.size!==e.total)throw Error('Evicted cache file')}catch{this.discard(item.key);return this.fetcher(request)}}
  const body=(e.file||new Blob(e.chunks.map(x=>x.data),{type:e.type})).slice(start,end+1,e.type);
  return new Response(body,{status:range?206:200,headers});
 }
}
self.ArrifyDeviceBuffer=ArrifyDeviceBuffer;

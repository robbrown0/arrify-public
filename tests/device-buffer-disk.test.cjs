const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),{webcrypto}=require('node:crypto');
const url=id=>'https://music.example/api/v1/stream/'+id+'?profile=original&level=off&device_id=test&owner_epoch=1';
const plan={enabled:true,scope:'session',urls:[url(1),url(2),url(3)]};
function directory(fault=()=>{}){
 const data=new Map();return {data,async getDirectoryHandle(){return this},async getFileHandle(name,{create=false}={}){
  if(!data.has(name)){if(!create)throw Error('Not found');data.set(name,Buffer.alloc(0))}
  return {async getFile(){if(!data.has(name))throw Error('Not found');return new Blob([data.get(name)])},async createWritable(options={}){
   fault(name,'open',0);let buffer=options.keepExistingData?data.get(name):Buffer.alloc(0),position=0;
   return {async write(value){if(value?.type==='write'){position=value.position;value=value.data}const input=value instanceof Blob?Buffer.from(await value.arrayBuffer()):Buffer.from(value);fault(name,'write',input.length);const out=Buffer.alloc(Math.max(buffer.length,position+input.length));buffer.copy(out);input.copy(out,position);buffer=out;position+=input.length},async seek(n){position=n},async close(){fault(name,'close',0);data.set(name,buffer)},async abort(){}};
  }};
 },async removeEntry(name){data.delete(name)},async *keys(){yield* [...data.keys()]}};
}
function make(root,fetcher,extra={}){const ctx={self:{location:{origin:'https://music.example'}},fetch:fetcher,URL,AbortController,ReadableStream,Response,Blob,Date,setTimeout,clearTimeout,crypto:webcrypto,TextEncoder};vm.createContext(ctx);vm.runInContext(fs.readFileSync('web/device-buffer-worker.js','utf8'),ctx);return new ctx.self.ArrifyDeviceBuffer({fetcher,directoryProvider:async()=>root,...extra})}
const response=()=>new Response('abcdefghij',{headers:{'Content-Length':'10','Content-Type':'audio/flac',ETag:'"v1"'}});
test('disk payloads survive worker replacement without reloading payload into JS chunks',async()=>{
 const root=directory();let calls=0;const fetcher=async()=>{calls++;return response()};const a=make(root,fetcher);await a.update('client',plan);
 assert.equal(a.snapshot('client').storage,'disk');for(const e of a.entries.values()){assert.equal(e.chunks.length,0);assert.equal(e.file.size,10)}
 const b=make(root,fetcher);await b.update('client',plan);assert.equal(calls,3);assert.equal(await(await b.response(new Request(url(2),{headers:{Range:'bytes=2-5'}}))).text(),'cdef');
 await b.clear();await b.cleanup;assert.equal(root.data.size,0);
});
test('quota failure is visible and does not block other planned downloads',async()=>{
 let failed=false;const root=directory((name,operation)=>{if(name.endsWith('.audio')&&operation==='write'&&!failed){failed=true;throw Object.assign(Error('quota'),{name:'QuotaExceededError'})}});
 const b=make(root,async()=>response());await b.update('client',plan);const s=b.snapshot('client');assert.equal(s.items[0].state,'storage-full');assert.equal(s.items[1].state,'ready');assert.equal(s.items[2].state,'ready');assert.equal(s.downloaded_bytes,20);assert.equal(root.data.size,4);
});
test('total disk budget prioritizes current song and clears retired files on selection change',async()=>{
 const root=directory(),b=make(root,async()=>response(),{diskBytes:15,diskFile:12});await b.update('client',plan);const s=b.snapshot('client');assert.equal(s.items[0].state,'ready');assert.equal(s.items[1].state,'budget');assert.equal(s.items[2].state,'budget');assert.equal(b.bytes,10);
 await b.update('client',{...plan,urls:[url(4)]});assert.equal(b.bytes,10);assert.equal(root.data.size,2);
});
test('expired disk entries and new session scopes never reuse old files',async()=>{
 const root=directory();let calls=0;const fetcher=async()=>{calls++;return response()};let b=make(root,fetcher);await b.update('client',{...plan,urls:[url(1)]});
 const key=[...root.data.keys()].find(k=>k.endsWith('.json'));const meta=JSON.parse(root.data.get(key));meta.expires=Date.now()-1;root.data.set(key,Buffer.from(JSON.stringify(meta)));
 b=make(root,fetcher);await b.update('client',{...plan,urls:[url(1)]});assert.equal(calls,2);
 await b.update('client',{...plan,scope:'new-session',urls:[url(1)]});assert.equal(calls,3);assert.equal(root.data.size,2);
});
test('unavailable OPFS uses the bounded legacy memory path',async()=>{
 const b=make(null,async()=>response());await b.update('client',plan);assert.equal(b.snapshot('client').storage,'memory');assert.equal(b.maxFile,32*1024*1024);assert.equal(b.maxBytes,96*1024*1024);
});
test('cancelled disk download is removed without publishing partial file metadata',async()=>{
 const root=directory();let source;const b=make(root,async()=>new Response(new ReadableStream({start(c){source=c;c.enqueue(new TextEncoder().encode('ab'))}}),{headers:{'Content-Type':'audio/flac','Content-Length':'10',ETag:'"v1"'}}));
 const work=b.update('client',{...plan,urls:[url(1)]});while(!source)await new Promise(setImmediate);
 b.clear();await work;await b.cleanup;assert.equal(root.data.size,0);assert.equal(b.bytes,0);
});

test('interrupted disk write resumes the validated suffix without retained payload chunks',async()=>{
 const root=directory();let source;const calls=[];
 const b=make(root,async(u,o)=>{calls.push(o.headers);return calls.length===1?new Response(new ReadableStream({start(c){source=c;c.enqueue(new TextEncoder().encode('abcd'))}}),{headers:{'Content-Length':'10','Content-Type':'audio/flac',ETag:'"v1"'}}):new Response('efghij',{status:206,headers:{'Content-Length':'6','Content-Type':'audio/flac','Content-Range':'bytes 4-9/10',ETag:'"v1"'}})},{retryDelays:[0]});
 const work=b.update('client',{...plan,urls:[url(1)]});while(b.bytes!==4)await new Promise(setImmediate);source.error(Error('connection lost'));await work;
 assert.equal(calls[1].Range,'bytes=4-');assert.equal(calls[1]['If-Range'],'"v1"');assert.equal(b.entries.values().next().value.chunks.length,0);assert.equal(await(await b.response(new Request(url(1)))).text(),'abcdefghij');
});

test('unchanged storage-failed plans do not repeatedly download the same songs',async()=>{
 let calls=0;const root=directory((name,operation)=>{if(name.endsWith('.audio')&&operation==='write')throw Object.assign(Error('quota'),{name:'QuotaExceededError'})});
 const b=make(root,async()=>{calls++;return response()});await b.update('client',plan);for(let i=0;i<5;i++)await b.update('client',plan);assert.equal(calls,3);
});
test('evicted disk files fall back to normal streaming instead of stale File snapshots',async()=>{
 const root=directory();let calls=0;const b=make(root,async()=>{calls++;return response()});await b.update('client',{...plan,urls:[url(1)]});root.data.clear();
 assert.equal(await(await b.response(new Request(url(1)))).text(),'abcdefghij');assert.equal(calls,2);
});

test('a storage API that acknowledges writes but returns incomplete files falls back and fills all slots',async()=>{
 const root=directory(),get=root.getFileHandle.bind(root);let diskReads=0,calls=0;
 root.getFileHandle=async(name,options)=>{
  const handle=await get(name,options);
  if(name.endsWith('.audio'))handle.getFile=async()=>{diskReads++;return new Blob([])};
  return handle;
 };
 const b=make(root,async()=>{calls++;return response()});await b.update('client',plan);
 assert.equal(b.snapshot('client').storage,'memory');assert.equal(b.maxBytes,96*1024*1024);
 assert.deepEqual(Array.from(b.snapshot('client').items,x=>x.state),['ready','ready','ready']);
 assert.equal(b.bytes,30);assert.equal(calls,4);assert.equal(diskReads,1);
 assert.equal(await(await b.response(new Request(url(1)))).text(),'abcdefghij');
 await b.update('client',plan);assert.equal(calls,4);
});
test('failed write-read capability probe selects bounded memory before downloading',async()=>{
 const root=directory(),get=root.getFileHandle.bind(root);
 root.getFileHandle=async(name,options)=>{
  const handle=await get(name,options);
  if(name==='probe')handle.getFile=async()=>new Blob([]);
  return handle;
 };
 let calls=0;const b=make(root,async()=>{calls++;return response()});await b.update('client',plan);
 assert.equal(b.snapshot('client').storage,'memory');assert.equal(calls,3);assert.equal(root.data.size,0);
 assert.deepEqual(Array.from(b.snapshot('client').items,x=>x.state),['ready','ready','ready']);
});
test('disk corruption on a later slot resets accounting and recovers the earlier slot on the next plan update',async()=>{
 const root=directory(),get=root.getFileHandle.bind(root);let reads=0;
 root.getFileHandle=async(name,options)=>{
  const handle=await get(name,options),read=handle.getFile.bind(handle);
  if(name.endsWith('.audio'))handle.getFile=async()=>++reads===2?new Blob([]):read();
  return handle;
 };
 const b=make(root,async()=>response());await b.update('client',plan);await b.update('client',plan);
 assert.equal(b.snapshot('client').storage,'memory');assert.equal(b.bytes,30);
 assert.deepEqual(Array.from(b.snapshot('client').items,x=>x.state),['ready','ready','ready']);
});

const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function make(fetcher,options={}){const ctx={self:{location:{origin:'https://music.example'}},fetch:fetcher,URL,AbortController,ReadableStream,Response,Blob,Date,setTimeout,clearTimeout};vm.createContext(ctx);vm.runInContext(fs.readFileSync('web/device-buffer-worker.js','utf8'),ctx);return new ctx.self.ArrifyDeviceBuffer({fetcher,...options});}
const url=id=>'https://music.example/api/v1/stream/'+id+'?profile=mp3&level=off&device_id=one&owner_epoch=2';
const response=()=>new Response('abcdefghij',{headers:{'Content-Length':'10','Content-Type':'audio/mpeg'}});
test('one download per song; current and next two; full and range requests reuse downloaded bytes',async()=>{const calls=[],b=make(async u=>{calls.push(u);return response()});await b.update('client',{enabled:true,scope:'user:session',urls:[url(1),url(2),url(3),url(4)]});assert.equal(calls.length,3);const r=await b.response(new Request(url(2),{headers:{Range:'bytes=2-5'}}));assert.equal(r.status,206);assert.equal(await r.text(),'cdef');assert.equal(r.headers.get('Content-Range'),'bytes 2-5/10');assert.equal(calls.length,3);assert.equal(await (await b.response(new Request(url(1)))).text(),'abcdefghij');});
test('suffix, invalid ranges and client isolation',async()=>{const b=make(async()=>response());await b.update('a',{enabled:true,scope:'u',urls:[url(1)]});assert.equal(b.handles('b',new Request(url(1))),false);assert.equal(b.handles('a',new Request(url(1))),true);assert.equal(await (await b.response(new Request(url(1),{headers:{Range:'bytes=-3'}}))).text(),'hij');assert.equal((await b.response(new Request(url(1),{headers:{Range:'bytes=99-'}}))).status,416);b.update('a',{enabled:false});assert.equal(b.bytes,0);assert.equal(b.handles('a',new Request(url(1))),false);});
test('streaming responds before full download completes',async()=>{let source;const b=make(async()=>new Response(new ReadableStream({start(c){source=c;c.enqueue(new TextEncoder().encode('ab'))}}),{headers:{'Content-Length':'10','Content-Type':'audio/mpeg'}}));const job=b.update('a',{enabled:true,scope:'u',urls:[url(1)]});await new Promise(setImmediate);const r=await b.response(new Request(url(1),{headers:{Range:'bytes=0-1'}}));assert.equal(await r.text(),'ab');assert.equal(b.entries.values().next().value.done,false);source.enqueue(new TextEncoder().encode('cdefghij'));source.close();await job;assert.equal(b.bytes,10);});
test('new selection cancels pending work and session changes drop prior bytes',async()=>{const calls=[];const b=make((u,opts)=>{calls.push(u);if(u===url(1))return new Promise((resolve,reject)=>opts.signal.addEventListener('abort',()=>reject(Error('aborted'))));return Promise.resolve(response())});const first=b.update('a',{enabled:true,scope:'u',urls:[url(1),url(2),url(3)]});await new Promise(setImmediate);await b.update('a',{enabled:true,scope:'u',urls:[url(4),url(5)]});await first;assert.deepEqual(calls,[url(1),url(4),url(5)]);assert.equal(b.entries.has(b.key(url(1))),false);await b.update('a',{enabled:true,scope:'different-user',urls:[url(6)]});assert.equal(b.bytes,10);assert.equal(b.entries.size,1);});
test('oversized files and memory budget never retained',async()=>{const b=make(async()=>response(),{maxFile:5});await b.update('a',{enabled:true,scope:'u',urls:[url(1)]});assert.equal(b.bytes,0);const c=make(async()=>response(),{maxBytes:15});await c.update('a',{enabled:true,scope:'u',urls:[url(1),url(2)]});assert.ok(c.bytes<=15);});
const plan={enabled:true,scope:'session',urls:[url(1)]};
const fullHeaders={'Content-Length':'10','Content-Type':'audio/mpeg',ETag:'"v1"'};
const resumed=(body='efghij',etag='"v1"',range='bytes 4-9/10')=>new Response(body,{status:206,headers:{...fullHeaders,'Content-Length':String(body.length),ETag:etag,'Content-Range':range}});
test('interrupted read-ahead resumes exact validated range before cached playback',async()=>{
 let source;const calls=[];const b=make(async(u,o)=>{calls.push(o.headers);return calls.length===1?new Response(new ReadableStream({start(c){source=c;c.enqueue(new TextEncoder().encode('abcd'))}}),{headers:fullHeaders}):resumed()},{retryDelays:[0]});
 const job=b.update('a',plan);await new Promise(setImmediate);assert.equal(b.bytes,4);source.error(Error('connection lost'));await job;assert.equal(await (await b.response(new Request(url(1)))).text(),'abcdefghij');assert.equal(calls[1].Range,'bytes=4-');assert.equal(calls[1]['If-Range'],'"v1"');assert.equal(b.bytes,10);
});
test('temporary prefetch failure retries; unchanged failed plan can restart after cooldown',async()=>{
 let calls=0;const b=make(async()=>{calls++;if(calls<=2)throw Error('offline');return response()},{retryDelays:[0],retryCooldownMs:0});await b.update('a',plan);assert.equal(calls,2);assert.equal(b.running,null);await b.update('a',plan);assert.equal(calls,3);assert.equal(b.entries.values().next().value.done,true);
});
test('exhausted retries preserve playable prefix and later resume it',async()=>{
 let source,calls=0;const b=make(async()=>{calls++;if(calls===1)return new Response(new ReadableStream({start(c){source=c;c.enqueue(new TextEncoder().encode('abcd'))}}),{headers:fullHeaders});if(calls===2)throw Error('offline');return resumed()},{retryDelays:[0],retryCooldownMs:0});
 const job=b.update('a',plan);await new Promise(setImmediate);await (await b.response(new Request(url(1),{headers:{Range:'bytes=0-3'}}))).text();source.error(Error('offline'));await job;assert.equal(b.bytes,4);assert.equal(await (await b.response(new Request(url(1),{headers:{Range:'bytes=0-3'}}))).text(),'abcd');await b.update('a',plan);assert.equal(await (await b.response(new Request(url(1)))).text(),'abcdefghij');
});
test('changed representation and invalid Content-Range never splice audio',async()=>{
 for(const reply of [()=>new Response('XXXXXXXXXX',{headers:{...fullHeaders,ETag:'"v2"'}}),()=>resumed('XXXXXX','"v2"'),()=>resumed('efghij','"v1"','bytes 3-8/10')]){
  let source,calls=0;const b=make(async()=>++calls===1?new Response(new ReadableStream({start(c){source=c;c.enqueue(new TextEncoder().encode('abcd'))}}),{headers:fullHeaders}):reply(),{retryDelays:[0]});
  const job=b.update('a',plan);await new Promise(setImmediate);source.error(Error('offline'));await job;assert.equal(b.entries.size,0);assert.equal(b.bytes,0);
 }
});
test('slow but progressing download outlives idle timeout; actual stall resumes',async()=>{
 let source;const b=make(async()=>new Response(new ReadableStream({start(c){source=c}}),{headers:fullHeaders}),{idleTimeoutMs:80,headerTimeoutMs:80,retryDelays:[]});const job=b.update('a',plan);await new Promise(setImmediate);for(let i=0;i<5;i++){await new Promise(r=>setTimeout(r,25));source.enqueue(new TextEncoder().encode('ab'))}source.close();await job;assert.equal(b.bytes,10);assert.equal(b.entries.values().next().value.done,true);
 let calls=0;const c=make(async()=>++calls===1?new Response(new ReadableStream({start(x){x.enqueue(new TextEncoder().encode('abcd'))}}),{headers:fullHeaders}):resumed(),{idleTimeoutMs:15,retryDelays:[0]});await c.update('a',plan);assert.equal(calls,2);assert.equal(c.bytes,10);
});
test('auth failure clears partial buffers; cancellation interrupts retry delay',async()=>{
 const b=make(async()=>new Response('',{status:403}));await b.update('a',plan);assert.equal(b.entries.size,0);assert.equal(b.client,'');
 let calls=0;const c=make(async()=>{calls++;throw Error('offline')},{retryDelays:[10000]});const job=c.update('a',plan);await new Promise(setImmediate);c.clear();await job;assert.equal(calls,1);assert.equal(c.bytes,0);
});
test('missing or weak validators never authorize byte splicing',async()=>{
 for(const tag of [null,'W/"v1"']){
  let source,calls=0;const headers={...fullHeaders};if(tag)headers.ETag=tag;else delete headers.ETag;
  const b=make(async()=>{calls++;return new Response(new ReadableStream({start(c){source=c;c.enqueue(new TextEncoder().encode('abcd'))}}),{headers})},{retryDelays:[0]});
  const job=b.update('a',plan);await new Promise(setImmediate);assert.equal(await (await b.response(new Request(url(1),{headers:{Range:'bytes=0-3'}}))).text(),'abcd');source.error(Error('offline'));await job;assert.equal(calls,1);assert.equal(b.bytes,4);b.clear();
 }
});
test('retry cooldown prevents repeated plan updates from hammering unavailable server',async()=>{
 let calls=0;const b=make(async()=>{calls++;return new Response('',{status:503})},{retryDelays:[0],retryCooldownMs:10000});await b.update('a',plan);assert.equal(calls,2);for(let i=0;i<5;i++)await b.update('a',plan);assert.equal(calls,2);b.clear();
});
test('stalled response headers time out and retry without retaining partial metadata',async()=>{
 let calls=0;const b=make(async(u,o)=>{calls++;if(calls===1)return new Promise((resolve,reject)=>o.signal.addEventListener('abort',()=>reject(Error('stalled'))));return response()},{headerTimeoutMs:10,retryDelays:[0]});await b.update('a',plan);assert.equal(calls,2);assert.equal(b.bytes,10);
});
test('telemetry is client isolated and contains counts and track IDs without stream URLs',async()=>{
 const b=make(async()=>response());await b.update('a',{...plan,urls:[url(1),url(2)]});const snapshot=b.snapshot('a');assert.equal(snapshot.downloaded_bytes,20);assert.equal(snapshot.items[0].state,'ready');assert.equal(snapshot.items[1].id,2);assert.equal(JSON.stringify(snapshot).includes('device_id'),false);assert.equal(b.snapshot('other').downloaded_bytes,0);b.clear();assert.equal(b.snapshot('a').state,'inactive');
});

test('unavailable bytes use native network without waiting for read-ahead',async()=>{
 let source;const calls=[];
 const b=make(async(u,o)=>{calls.push(u);if(u instanceof Request)return new Response('network');return new Response(new ReadableStream({start(c){source=c;c.enqueue(new TextEncoder().encode('ab'))}}),{headers:fullHeaders})});
 const job=b.update('a',plan);await new Promise(setImmediate);
 const r=await b.response(new Request(url(1)));assert.equal(await r.text(),'network');assert.equal(calls.length,2);
 b.clear();await job;
});
test('cached range remains readable after cancellation and cache eviction',async()=>{
 const b=make(async()=>response());await b.update('a',plan);
 const r=await b.response(new Request(url(1),{headers:{Range:'bytes=2-8'}}));b.clear();
 assert.equal(await r.text(),'cdefghi');
});

test('oversized current song is skipped while next two songs download',async()=>{
 const b=make(async u=>u===url(1)?new Response('oversize',{headers:{'Content-Length':'100','Content-Type':'audio/flac'}}):response(),{maxFile:32});
 await b.update('a',{enabled:true,scope:'u',urls:[url(1),url(2),url(3)]});
 const status=b.snapshot('a');assert.equal(status.items[0].state,'oversized');assert.equal(status.items[1].state,'ready');assert.equal(status.items[2].state,'ready');assert.equal(status.downloaded_bytes,20);
 assert.equal(await (await b.response(new Request(url(2)))).text(),'abcdefghij');
 b.clear();assert.equal(b.bytes,0);
});

test('changing next two preserves a retained next-one download',async()=>{
 let started,release,calls=0,signal;
 const pending=new Promise(resolve=>started=resolve);
 const b=make(async(u,options)=>{
  if(u===url(2)){calls++;signal=options.signal;started();return new Promise((resolve,reject)=>{release=()=>resolve(response());options.signal.addEventListener('abort',()=>reject(Error('aborted')),{once:true})})}
  return response();
 });
 const first=b.update('a',{enabled:true,scope:'u',urls:[url(1),url(2),url(3)]});
 await pending;
 const second=b.update('a',{enabled:true,scope:'u',urls:[url(1),url(2),url(4)]});
 const cancelled=signal.aborted;release();
 if(cancelled){b.clear();await Promise.all([first,second]);assert.fail('Retained next-one download was cancelled')}
 await Promise.all([first,second]);
 assert.equal(calls,1);assert.deepEqual(Array.from(b.snapshot('a').items,x=>x.state),['ready','ready','ready']);
});
test('unbufferable responses report retry state and respect cooldown',async()=>{
 let calls=0;const b=make(async()=>{calls++;return new Response('unavailable',{status:422})});
 const plan={enabled:true,scope:'u',urls:[url(1)]};
 await b.update('a',plan);const status=b.snapshot('a');
 assert.equal(status.items[0].state,'retrying');assert.equal(status.items[0].failure_reason,'unsupported-response');
 await b.update('a',plan);assert.equal(calls,1);
 b.failures.get(b.key(url(1))).retryAt=0;b.fetcher=async()=>{calls++;return response()};
 await b.update('a',plan);assert.equal(calls,2);assert.equal(b.snapshot('a').items[0].state,'ready');
 b.clear();assert.equal(b.failures.size,0);
});

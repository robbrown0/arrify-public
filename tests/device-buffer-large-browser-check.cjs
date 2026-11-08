// Actual OPFS payloads larger than the legacy memory cap; no production media.
const {chromium,webkit}=require('playwright'),http=require('node:http'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const sizes=[91,40,40].map(m=>m*1024*1024+44),requests=[];let offline=false,browser;
 const header=size=>{const b=Buffer.alloc(44);b.write('RIFF');b.writeUInt32LE(size-8,4);b.write('WAVEfmt ',8);b.writeUInt32LE(16,16);b.writeUInt16LE(1,20);b.writeUInt16LE(1,22);b.writeUInt32LE(8000,24);b.writeUInt32LE(16000,28);b.writeUInt16LE(2,32);b.writeUInt16LE(16,34);b.write('data',36);b.writeUInt32LE(size-44,40);return b};
 const server=http.createServer(async(req,res)=>{
  const p=new URL(req.url,'http://localhost').pathname;
  if(p.startsWith('/api/v1/stream/')){
   if(offline){res.destroy();return}const id=Number(p.split('/').pop()),size=sizes[id-1];requests.push(id);
   res.writeHead(200,{'Content-Type':'audio/wav','Content-Length':size,ETag:'"large-'+id+'"'});res.write(header(size));
   const chunk=Buffer.alloc(128*1024);let left=size-44;
   while(left&&!res.destroyed){const length=Math.min(left,chunk.length);left-=length;if(!res.write(chunk.subarray(0,length)))await new Promise(r=>{const done=()=>{res.removeListener('drain',done);res.removeListener('close',done);r()};res.once('drain',done);res.once('close',done)});}
   res.end();return;
  }
  if(p==='/'){res.setHeader('Content-Type','text/html');res.end('<button id="play">Play</button><audio></audio>');return}
  let content=fs.existsSync(path.join('web',p))?fs.readFileSync(path.join('web',p)):'fixture';
  if(p==='/sw.js')content=String(content).replace("const work=deviceBuffer.update(event.source.id,event.data);","const work=deviceBuffer.update(event.source.id,event.data);work.catch(e=>deviceBuffer.testError=String(e.stack));").replace('postMessage(deviceBuffer.snapshot(event.source.id))','postMessage({...deviceBuffer.snapshot(event.source.id),testError:deviceBuffer.testError})');
  if(p==='/sw.js')content+='\nself.addEventListener("message",e=>{if(e.data.type==="test-memory")e.ports[0].postMessage([...deviceBuffer.entries.values()].map(x=>({chunks:x.chunks.reduce((n,c)=>n+c.data.byteLength,0),disk:!!x.fileHandle,ready:x.done})))})';
  if(p==='/sw.js')content+=';self.addEventListener("message",e=>{if(e.data.type==="test-capability")e.waitUntil(deviceBuffer.initDisk().then(()=>e.ports[0].postMessage(!!deviceBuffer.files)));if(e.data.type==="test-file-count")e.waitUntil((async()=>{let n=0;for await(const key of deviceBuffer.files.directory.keys())n++;e.ports[0].postMessage(n)})())})';
  res.setHeader('Content-Type',p.endsWith('.js')?'text/javascript':'text/plain');res.end(content);
 });await new Promise(r=>server.listen(0,'127.0.0.1',r));
 try{
  const engine=process.env.BROWSER==='webkit'?webkit:chromium;browser=await engine.launch({args:engine===chromium?['--no-sandbox']:[]});const context=await browser.newContext(),page=await context.newPage();await page.goto('http://127.0.0.1:'+server.address().port);
  await page.evaluate(async()=>{
   await navigator.serviceWorker.register('/sw.js');await navigator.serviceWorker.ready;if(!navigator.serviceWorker.controller)await new Promise(r=>navigator.serviceWorker.addEventListener('controllerchange',r,{once:true}));
   window.url=id=>location.origin+'/api/v1/stream/'+id+'?profile=original&level=off&device_id=test&owner_epoch=1';
   window.message=data=>new Promise(r=>{const c=new MessageChannel();c.port1.onmessage=e=>{c.port1.close();r(e.data)};navigator.serviceWorker.controller.postMessage(data,[c.port2])});
   window.diskSupported=await message({type:'test-capability'});
   if(diskSupported)await message({type:'device-buffer',enabled:true,scope:'large-test',urls:[1,2,3].map(url)});
   document.querySelector('#play').onclick=()=>{const a=document.querySelector('audio');a.src=url(1);a.play().catch(e=>window.playError=e.message)};
  });
  if(!await page.evaluate(()=>diskSupported)){console.log('UNSUPPORTED '+engine.name()+': this browser build lacks OPFS writable support; physical Safari validation required.');return}
  let snapshot;for(let i=0;i<1200;i++){
   snapshot=await page.evaluate(()=>message({type:'device-buffer-status'}));
   if(i%100===0)console.log('read-ahead',JSON.stringify(snapshot));
   if(snapshot?.items?.length===3&&snapshot.items.every(i=>i.state==='ready'))break;
   await page.waitForTimeout(100);
  }
  assert.equal(snapshot.storage,'disk','OPFS must be used, not memory fallback');assert.equal(snapshot.downloaded_bytes,sizes.reduce((a,b)=>a+b));assert.equal(snapshot.limit_bytes,512*1024*1024);assert.deepEqual(requests,[1,2,3]);
  assert.deepEqual(await page.evaluate(()=>message({type:'test-memory'})),[1,2,3].map(()=>({chunks:0,disk:true,ready:true})));
  offline=true;
  if(engine===chromium){const cdp=await context.newCDPSession(page);await cdp.send('ServiceWorker.enable');await cdp.send('ServiceWorker.stopAllWorkers')}
  const range=await page.evaluate(async()=>{const r=await fetch(url(1),{headers:{Range:'bytes=0-43'}});return {status:r.status,bytes:Array.from(new Uint8Array(await r.arrayBuffer())),range:r.headers.get('Content-Range')}});
  assert.equal(range.status,206);assert.deepEqual(range.bytes,[...header(sizes[0])]);assert.equal(range.range,'bytes 0-43/'+sizes[0]);assert.deepEqual(requests,[1,2,3]);
  await page.click('#play');await page.waitForFunction(()=>document.querySelector('audio').currentTime>.1&&!document.querySelector('audio').paused);
  await page.evaluate(()=>{const a=document.querySelector('audio');a.currentTime=a.duration-1});await page.waitForFunction(()=>document.querySelector('audio').ended,null,{timeout:15000});
  assert.equal(await page.evaluate(()=>window.playError),undefined);assert.deepEqual(requests,[1,2,3]);
  await page.evaluate(()=>message({type:'device-buffer',enabled:false}));
  let count=99;for(let i=0;i<100;i++){count=await page.evaluate(async()=>{return await message({type:'test-file-count'})});if(!count)break;await page.waitForTimeout(50)}assert.equal(count,0);
  console.log('PASS '+engine.name()+': 91 + 40 + 40 MiB on disk; zero retained JS payload chunks; cached range, decoder and end seek with origin unavailable; '+(engine===chromium?'worker restart; ':'')+'disable removes files.');
 }finally{await browser?.close();server.closeAllConnections();await new Promise(r=>server.close(r))}
})().catch(e=>{console.error(e);process.exitCode=1});

// Verify that an existing controller can be replaced while two tabs stay open.
const {chromium,webkit}=require('playwright'),http=require('node:http'),fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{
 let updated=false,browser;
 const old="self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));self.addEventListener('message',e=>e.ports[0]?.postMessage({worker_version:'old'}));";
 const wav=Buffer.alloc(16044);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(8000,24);wav.writeUInt32LE(16000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(16000,40);
 const server=http.createServer((req,res)=>{
  const p=new URL(req.url,'http://localhost').pathname;res.setHeader('Cache-Control','no-store');
  if(p==='/'){res.setHeader('Content-Type','text/html');res.end('<button onclick="document.querySelector(\'audio\').play()">Play</button><audio src="/fixture.wav" loop></audio>');return}
  if(p==='/fixture.wav'||p.startsWith('/api/v1/stream/')){res.writeHead(200,{'Content-Type':'audio/wav','Content-Length':wav.length,ETag:'"fixture"'});res.end(wav);return}
  res.setHeader('Content-Type',p.endsWith('.js')?'text/javascript':'text/plain');
  if(p==='/sw.js'){res.end(updated?fs.readFileSync('web/sw.js'):old);return}
  res.end(fs.existsSync('web'+p)?fs.readFileSync('web'+p):'fixture');
 });await new Promise(r=>server.listen(0,'127.0.0.1',r));
 try{
  const engine=process.env.BROWSER==='webkit'?webkit:chromium;browser=await engine.launch({args:engine===chromium?['--no-sandbox']:[]});const context=await browser.newContext();const a=await context.newPage(),b=await context.newPage(),origin='http://127.0.0.1:'+server.address().port;
  await a.goto(origin);await a.evaluate(async()=>{await navigator.serviceWorker.register('/sw.js',{updateViaCache:'none'});await navigator.serviceWorker.ready;if(!navigator.serviceWorker.controller)await new Promise(r=>navigator.serviceWorker.addEventListener('controllerchange',r,{once:true}));window.sentinel='same-page'});
  await b.goto(origin);await a.click('button');await a.waitForFunction(()=>!document.querySelector('audio').paused);
  updated=true;
  await a.evaluate(async()=>{
   const changed=new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('New worker stayed waiting')),15000);navigator.serviceWorker.addEventListener('controllerchange',()=>{clearTimeout(timer);resolve()},{once:true})});
   await (await navigator.serviceWorker.getRegistration()).update();await changed;
  });
  const state=await a.evaluate(async()=>{
   const send=data=>new Promise(r=>{const c=new MessageChannel();c.port1.onmessage=e=>{c.port1.close();r(e.data)};navigator.serviceWorker.controller.postMessage(data,[c.port2])});
   await send({type:'device-buffer',enabled:true,scope:'upgrade',urls:[location.origin+'/api/v1/stream/1?profile=original']});
   return {report:await send({type:'device-buffer-status'}),paused:document.querySelector('audio').paused,sentinel:window.sentinel,waiting:!!(await navigator.serviceWorker.getRegistration()).waiting};
  });
  assert.equal(state.report.worker_version,'20261108.2');assert.equal(state.paused,false);assert.equal(state.sentinel,'same-page');assert.equal(state.waiting,false);
  assert.equal(context.pages().length,2);console.log('PASS: new cache worker activated with two tabs open; no page reload or audio pause; version confirmed.');
 }finally{await browser?.close();await new Promise(r=>server.close(r))}
})().catch(e=>{console.error(e);process.exitCode=1});

// Real service worker + production plan with simulated AirPlay route signals.
// Receiver hardware behavior is intentionally not inferred from this browser test.
const {chromium,webkit}=require('playwright'),http=require('http'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
(async()=>{
 const downloads=[],web=path.resolve('web');let offline=false,browser;
 const server=http.createServer((req,res)=>{
  const p=new URL(req.url,'http://localhost').pathname;
  if(p.startsWith('/api/v1/stream/')){if(offline){res.destroy();return}downloads.push(p);res.writeHead(200,{'Content-Type':'audio/mpeg','Content-Length':'10',ETag:'"fixture"'});res.end('abcdefghij');return}
  if(p==='/'){res.setHeader('Content-Type','text/html');res.end('<!doctype html><title>Read-ahead routes</title>');return}
  const file=path.join(web,p);res.setHeader('Content-Type',p.endsWith('.js')?'text/javascript':'text/plain');res.end(fs.existsSync(file)&&fs.statSync(file).isFile()?fs.readFileSync(file):'fixture');
 });await new Promise(r=>server.listen(0,'127.0.0.1',r));
 try{
  const engine=process.env.BROWSER==='webkit'?webkit:chromium;browser=await engine.launch({args:engine===chromium?['--no-sandbox']:[]});const page=await browser.newPage();await page.goto('http://127.0.0.1:'+server.address().port);
  await page.evaluate(async()=>{
   await navigator.serviceWorker.register('/sw.js');await navigator.serviceWorker.ready;if(!navigator.serviceWorker.controller)await new Promise(r=>navigator.serviceWorker.addEventListener('controllerchange',r,{once:true}));
   Object.assign(window,{audio:{webkitCurrentPlaybackTargetIsWireless:true},me:{buffer_scope:'route-fixture'},state:{current:1,listening:{playing:true,epoch:5},upcoming:[2,3]},ownsListening:()=>true,deviceBufferMode:()=>'ahead',gaplessPlayback:{},playback:{},soundPreferences:{leveling:'off'},$:()=>({value:'mp3'}),nextForPlayback:()=>({id:2}),listeningDevice:{id:'phone'}});
   window.sample=()=>new Promise(resolve=>{const c=new MessageChannel();c.port1.onmessage=e=>{c.port1.close();resolve(e.data)};navigator.serviceWorker.controller.postMessage({type:'device-buffer-status'},[c.port2])});
  });
  async function waitSample(check){for(let i=0;i<100;i++){const s=await page.evaluate(()=>sample());if(check(s))return;await page.waitForTimeout(50)}throw Error('Buffer state timed out')}
  const app=fs.readFileSync('web/app.js','utf8');await page.addScriptTag({content:app.slice(app.indexOf('function syncDeviceBuffer('),app.indexOf("navigator.serviceWorker?.addEventListener?.('controllerchange'"))});await page.evaluate(()=>syncDeviceBuffer());
  await waitSample(s=>s?.items.length===3&&s.items.every(i=>i.state==='ready'));assert.equal(downloads.length,3);
  offline=true;
  const report=await page.evaluate(async()=>{
   audio.webkitCurrentPlaybackTargetIsWireless=false;audio.remote={state:'connected'};syncDeviceBuffer();
   const url=location.origin+'/api/v1/stream/2?profile=mp3&level=off&device_id=phone&owner_epoch=5';const r=await fetch(url,{headers:{Range:'bytes=2-5'}});
   return {status:r.status,body:await r.text(),cache:r.headers.get('X-Arrify-Device-Buffer'),snapshot:await sample()};
  });assert.equal(report.status,206);assert.equal(report.body,'cdef');assert.equal(report.cache,'complete');assert.equal(report.snapshot.downloaded_bytes,30);assert.equal(downloads.length,3);
  await page.evaluate(()=>syncDeviceBuffer(true));await waitSample(s=>s?.state==='inactive'&&s.downloaded_bytes===0);
  console.log('PASS '+engine.name()+': AirPlay route signals permit current + next two downloads; route change preserves cached ranges during origin outage; disable clears cache.');
 }finally{await browser?.close();await new Promise(r=>server.close(r))}
})().catch(e=>{console.error(e);process.exitCode=1});

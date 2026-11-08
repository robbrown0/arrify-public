// Production recovery/reconciliation + native transport, real decoder and worker.
// A per-element gesture gate models iOS restrictions; this is not AirPlay hardware.
const {chromium,webkit}=require('playwright'),http=require('http'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
(async()=>{
 const app=fs.readFileSync('web/app.js','utf8'),web=path.resolve('web');
 const extract=(start,end)=>app.slice(app.indexOf(start),app.indexOf(end));
 const functions=[extract('function prepareReloadPlayback(','async function reattachListeningPage('),extract('function listeningPlaying(','function listeningView('),extract('function progressPosition(','function duration('),extract('function showPlaybackStatus(','function progressPosition('),extract('async function consumeListening(','async function pollListening('),extract('function syncDeviceBuffer(',"navigator.serviceWorker?.addEventListener?.('controllerchange'")].join('\n');
 const toggle=app.split("case'play-pause':")[1].split("case'next':")[0];
 const wav=Buffer.alloc(44+16000*8);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(8000,24);wav.writeUInt32LE(16000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(wav.length-44,40);
 let browser;const server=http.createServer((req,res)=>{
  const p=new URL(req.url,'http://localhost').pathname;
  if(p.startsWith('/api/v1/stream/')){res.writeHead(200,{'Content-Type':'audio/wav','Content-Length':wav.length,ETag:'"reload-audio"'});res.end(wav);return}
  if(p==='/'){res.setHeader('Content-Type','text/html');res.end('<!doctype html><button id="play-pause">Play</button><div class="player"></div><p id="playback-status" hidden></p><audio></audio><script src="/playback.js"></script>');return}
  const file=path.join(web,p);res.setHeader('Content-Type',p.endsWith('.js')?'text/javascript':'text/plain');res.end(fs.existsSync(file)&&fs.statSync(file).isFile()?fs.readFileSync(file):'fixture');
 });await new Promise(r=>server.listen(0,'127.0.0.1',r));
 try{
  const engine=process.env.BROWSER==='webkit'?webkit:chromium;browser=await engine.launch({args:engine===chromium?['--no-sandbox']:[]});const page=await browser.newPage();const base='http://127.0.0.1:'+server.address().port;
  async function boot(){
   await page.evaluate(async()=>{await navigator.serviceWorker.register('/sw.js');await navigator.serviceWorker.ready;if(!navigator.serviceWorker.controller)await new Promise(r=>navigator.serviceWorker.addEventListener('controllerchange',r,{once:true}));});
   await page.evaluate(()=>{
    const allowed=new WeakSet(),original=HTMLMediaElement.prototype.play;let gesture=false;
    document.addEventListener('click',()=>{gesture=true;setTimeout(()=>{gesture=false},0)},true);
    HTMLMediaElement.prototype.play=function(){if(gesture)allowed.add(this);if(!allowed.has(this))return Promise.reject(new DOMException('Fresh gesture required','NotAllowedError'));return original.call(this)};
    const audio=document.querySelector('audio');
    Object.assign(window,{audio,$:s=>s==='#profile'?{value:'mp3'}:document.querySelector(s),listeningDevice:{id:'phone',restored:true},me:{buffer_scope:'reload-test'},soundPreferences:{leveling:'off'},ownsListening:()=>true,remoteListening:()=>false,listeningOnline:()=>true,deviceBufferMode:()=>'ahead',nextForPlayback:()=>({id:2}),gaplessPlayback:{},state:{current:1,track:{id:1},position:2,upcoming:[2,3],listening:{owner:'phone',epoch:1,intent:1,seek_intent:1,playing:sessionStorage.getItem('paused')!=='yes',action:'resume'}},listeningNeedsGesture:false,listeningSigningOut:false,listeningApplied:-1,listeningSeekApplied:-1,listeningReconciling:false,listeningReceived:0,nativeAdvance:null,retryTimer:null,pendingAdvance:null,pendingRecovery:null,queueOpen:false,listeningQueueSignature:'',listeningPushPending:false,listeningPolling:false,pauseSerial:0,applyState:async()=>{},paintListening:()=>{},refreshListeningEvents:()=>{},listeningHeartbeat:()=>{},retryCount:0,compatibleFallback:false,nativeStreamNeedsRenewal:()=>false,seekLocal:async()=>{},toast:()=>{}});
    window.nativePlayback=ArrifyPlayback.create({element:audio,stableElement:true,onActivate:a=>window.audio=a,streamQuery:()=> '&device_id=phone&owner_epoch=1'});window.playback=nativePlayback;
    window.startCurrentPlayback=(t,offset)=>playback.load(t,'mp3',offset,true);window.handlePlaybackError=e=>{window.playError=e.message};
    window.command=async({action})=>{window.lastAction=action;await new Promise(r=>setTimeout(r,80));const updated={...state,listening:{...state.listening,intent:state.listening.intent+1,playing:action!=='pause',action}};await consumeListening(updated)};
   });
   await page.addScriptTag({content:functions+`\ndocument.querySelector('#play-pause').onclick=()=>{if(!listeningPlaying())unlockLocalPlayback();return (async()=>{${toggle}})()};`});
   await page.evaluate(async()=>{prepareReloadPlayback();await consumeListening(state)});
  }
  await page.goto(base);await boot();
  assert.equal(await page.locator('#playback-status').textContent(),'Tap play to continue listening.');assert.equal(await page.locator('.player').getAttribute('aria-busy'),'false');assert.equal(await page.evaluate(()=>audio.getAttribute('src')),null);
  await page.click('#play-pause');await page.waitForFunction(()=>!audio.paused&&audio.currentTime>=2&&!playback.loading);assert.equal(await page.evaluate(()=>lastAction),'resume');assert.equal(await page.evaluate(()=>window.playError),undefined);
  await page.reload();await boot();assert.equal(await page.evaluate(()=>audio.paused&&!listeningPlaying()&&state.position===2),true);await page.click('#play-pause');await page.waitForFunction(()=>!audio.paused&&audio.currentTime>=2&&!playback.loading);assert.equal(await page.evaluate(()=>lastAction),'resume');
  await page.evaluate(()=>sessionStorage.setItem('paused','yes'));await page.reload();await boot();assert.equal(await page.evaluate(()=>audio.paused&&!listeningNeedsGesture),true);await page.click('#play-pause');await page.waitForFunction(()=>!audio.paused&&audio.currentTime>=2&&!playback.loading);assert.equal(await page.evaluate(()=>lastAction),'resume');assert.equal(await page.evaluate(()=>window.playError),undefined);
  console.log('PASS '+engine.name()+': playing reload shows persistent prompt; one tap resumes saved cursor through delayed reconciliation with read-ahead; second refresh and paused reload recover.');
 }finally{await browser?.close();await new Promise(r=>server.close(r))}
})().catch(e=>{console.error(e);process.exitCode=1});

// Real HTTP truncation followed by validated resume, then decoded through a native media element.
const {chromium,webkit}=require('playwright');
const http=require('http'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
(async()=>{
 const wav=Buffer.alloc(44+48000);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(8000,24);wav.writeUInt32LE(16000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(48000,40);
 const requests=[];let browser;
 const server=http.createServer((req,res)=>{
  const p=new URL(req.url,'http://localhost').pathname;
  if(p.startsWith('/api/v1/stream/')){
   requests.push({range:req.headers.range,validator:req.headers['if-range']});
   const headers={'Content-Type':'audio/wav',ETag:'"test-audio-v1"'};
   if(requests.length===1){res.writeHead(200,{...headers,'Content-Length':wav.length});res.write(wav.subarray(0,4044));setTimeout(()=>res.destroy(),120);return}
   if(req.headers.range==='bytes=4044-'&&req.headers['if-range']==='"test-audio-v1"'){
    res.writeHead(206,{...headers,'Content-Length':wav.length-4044,'Content-Range':`bytes 4044-${wav.length-1}/${wav.length}`});res.end(wav.subarray(4044));return;
   }
   res.writeHead(400);res.end('Unexpected retry');return;
  }
  if(p==='/'){res.setHeader('Content-Type','text/html');res.end('<button id="play">Play</button><audio></audio>');return}
  const file=path.join(path.resolve('web'),p);res.setHeader('Content-Type',p.endsWith('.js')?'text/javascript':'text/plain');res.end(fs.existsSync(file)&&fs.statSync(file).isFile()?fs.readFileSync(file):'fixture');
 });await new Promise(r=>server.listen(0,'127.0.0.1',r));
 try{
  const engine=process.env.BROWSER==='webkit'?webkit:chromium;browser=await engine.launch({args:engine===chromium?['--no-sandbox']:[]});const page=await browser.newPage();await page.goto('http://127.0.0.1:'+server.address().port);
  await page.evaluate(async()=>{
   await navigator.serviceWorker.register('/sw.js');await navigator.serviceWorker.ready;
   if(!navigator.serviceWorker.controller)await new Promise(r=>navigator.serviceWorker.addEventListener('controllerchange',r,{once:true}));
   window.url=location.origin+'/api/v1/stream/9?profile=original&level=off&device_id=test&owner_epoch=1';
    const channel=new MessageChannel();await new Promise(resolve=>{channel.port1.onmessage=resolve;navigator.serviceWorker.controller.postMessage({type:'device-buffer',enabled:true,scope:'recovery-test',urls:[url]},[channel.port2])});
   document.querySelector('#play').onclick=()=>{
    const a=document.querySelector('audio');a.src=url;a.play().catch(e=>window.playError=e.message);
   };
  });
  await (async()=>{for(let i=0;i<200;i++){if(await page.evaluate(()=>new Promise(resolve=>{const c=new MessageChannel();c.port1.onmessage=e=>{c.port1.close();resolve(e.data?.items?.length>0&&e.data.items.every(i=>i.state==='ready'))};navigator.serviceWorker.controller.postMessage({type:'device-buffer-status'},[c.port2])})))return;await page.waitForTimeout(25)}throw Error('Read-ahead did not complete')})();
  await page.click('#play');await page.waitForFunction(()=>document.querySelector('audio').ended,{},{timeout:15000});
  const state=await page.evaluate(()=>({duration:document.querySelector('audio').duration,error:window.playError,mediaError:document.querySelector('audio').error?.code}));assert.equal(state.duration,3);assert.equal(state.error,undefined);assert.equal(state.mediaError,undefined);assert.equal(requests.length,2);assert.deepEqual(requests[1],{range:'bytes=4044-',validator:'"test-audio-v1"'});console.log('PASS: '+engine.name()+' completed real audio after HTTP truncation; one validated range retry, no media reload.');
 }finally{await browser?.close();await new Promise(r=>server.close(r))}
})().catch(e=>{console.error(e);process.exitCode=1});

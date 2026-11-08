'use strict';
const {chromium,webkit}=require('playwright'),fs=require('fs'),assert=require('assert/strict');
const root=process.cwd(),svg='<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240"><rect width="240" height="240" fill="white"/><path d="M20 20h70v70H20zM150 20h70v70h-70zM20 150h70v70H20zM120 120h100v100H120z" fill="black"/></svg>';
(async()=>{const browser=await (process.env.BROWSER==='webkit'?webkit:chromium).launch({args:process.env.BROWSER==='webkit'?[]:['--no-sandbox']});try{
const context=await browser.newContext({viewport:{width:834,height:1194},hasTouch:true}),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
let blocked=false,releaseImage;
await page.route('http://arrify.test/**',async route=>{const path=new URL(route.request().url()).pathname;if(path.endsWith('.js')||path.endsWith('.css'))return route.fulfill({contentType:path.endsWith('.js')?'application/javascript':'text/css',body:fs.readFileSync(root+'/web'+path,'utf8')});if(path.endsWith('.svg')){if(blocked&&path.includes('slow'))await new Promise(resolve=>releaseImage=resolve);return route.fulfill({contentType:'image/svg+xml',body:svg})}return route.fulfill({contentType:'text/html',headers:{'Content-Security-Policy':"default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:;"},body:'<!doctype html><html><head><link rel="stylesheet" href="/style.css"><link rel="stylesheet" href="/brand.css"><link rel="stylesheet" href="/party.css"></head><body><aside id="nav">Navigation</aside><main id="app"></main><audio id="player"></audio></body></html>'})});
await page.goto('http://arrify.test/#party/'+'a'.repeat(32));
for(const f of ['guest-wifi.js','party-host.js'])await page.addScriptTag({url:'http://arrify.test/'+f});
await page.evaluate(()=>{
 window.printCalls=[];document.queryCommandSupported=()=>false;window.toasts=[];window.savedAudio=document.querySelector('#player');window.transportCalls=0;
 window.print=()=>printCalls.push({active:navigator.userActivation.isActive,poster:document.body.classList.contains('party-printing')});
 window.ArrifyApple={apply:async()=>{},isActive:()=>true,statusText:()=> 'Playing'};window.ArrifySpotify=window.ArrifyApple;
 window.fixture={id:'a'.repeat(32),name:'Apple <Test> & friends',source:'apple',items:[],open:true,ended:false,slot_limit:3,refill:2,guest_url:'/party#guest',display_url:'/party-display#'+'b'.repeat(32),qr_url:'/api/v1/parties/'+'a'.repeat(32)+'/qr.svg',wifi_settings:{include_print:true,include_display:false,mode:'custom',profile:{ssid:'Guest & Friends',password:'test-password'}}};
 ArrifyParty.configure({api:async()=>fixture,getDevice:()=>({id:'test'}),toast:m=>toasts.push(m),command:()=>transportCalls++});
 window.render=async()=>{document.querySelector('#app').innerHTML=await ArrifyParty.view(fixture.id)};
});
await page.evaluate(()=>render());await page.waitForFunction(()=>[...document.querySelectorAll('#party-print-poster img')].every(i=>i.complete&&i.naturalWidth));
assert.equal(await page.locator('[data-party-id]').count(),1);assert.equal(await page.locator('[data-party-print]').evaluate(b=>b.tagName),'BUTTON');assert.equal(await page.locator('#party-print-poster').isVisible(),false);
await page.locator('[data-party-print]').click();assert.equal(context.pages().length,1);assert.match(page.url(),/#party\//);assert.deepEqual(await page.evaluate(()=>printCalls),[{active:true,poster:true}]);assert.equal(await page.evaluate(()=>savedAudio===document.querySelector('#player')&&transportCalls===0),true);
await page.emulateMedia({media:'print'});assert.equal(await page.locator('#nav').isVisible(),false);assert.equal(await page.locator('#app').isVisible(),false);assert.equal(await page.locator('#party-print-poster').isVisible(),true);assert.equal(await page.locator('#party-print-poster h1').textContent(),'Apple <Test> & friends');assert.equal(await page.locator('#party-print-poster .wifi-panel').isVisible(),true);assert.equal(await page.locator('#party-print-poster').evaluate(e=>getComputedStyle(e).backgroundColor),'rgb(255, 255, 255)');assert.equal(await page.locator('#party-print-poster').innerText().then(t=>t.includes('test-password')),false);
if(process.env.BROWSER!=='webkit')await page.pdf({path:root+'/../poster-with-wifi.pdf',format:'Letter',margin:{top:'12mm',bottom:'12mm',left:'12mm',right:'12mm'}});
await page.evaluate(()=>dispatchEvent(new Event('afterprint')));await page.emulateMedia({media:'screen'});assert.equal(await page.locator('#app').isVisible(),true);
await page.evaluate(async()=>{fixture.wifi_settings.include_print=false;await render()});await page.waitForFunction(()=>document.querySelector('#party-print-poster img').complete);assert.equal(await page.locator('#party-print-poster .wifi-panel').count(),0);await page.locator('[data-party-print]').click();assert.equal(await page.evaluate(()=>printCalls.length),2);await page.evaluate(()=>dispatchEvent(new Event('afterprint')));
blocked=true;await page.evaluate(async()=>{fixture.qr_url='/slow.svg';await render()});await page.locator('[data-party-print]').click();assert.equal(await page.evaluate(()=>printCalls.length),2);assert.match(await page.locator('#party-print-status').innerText(),/still loading/);releaseImage();blocked=false;await page.waitForFunction(()=>document.querySelector('#party-print-poster img').naturalWidth>0);await page.locator('[data-party-print]').click();assert.equal(await page.evaluate(()=>printCalls.length),3);await page.evaluate(()=>dispatchEvent(new Event('afterprint')));
// Model WebKit's silent window.print deferral while a live connection is open.
// The supported editor print command must be invoked during the original tap.
for(const mode of ['supported','unsupported','false','throws','started-false','all-fail']){
 await page.evaluate(mode=>{
  window.commandCalls=0;window.standardCalls=0;window.commandActive=false;
  document.queryCommandSupported=()=>mode!=='unsupported';
  document.execCommand=command=>{if(command!=='print')throw Error('Unexpected command');commandCalls++;commandActive=navigator.userActivation.isActive;
   if(mode==='throws'||mode==='all-fail')throw Error('Not available');
   if(mode==='started-false'){dispatchEvent(new Event('beforeprint'));dispatchEvent(new Event('afterprint'));return false}
   return mode==='supported';
  };
  window.print=()=>{standardCalls++;if(mode==='all-fail')throw Error('Blocked')};
 },mode);
 await page.locator('[data-party-print]').click();
 const result=await page.evaluate(()=>({commandCalls,standardCalls,commandActive}));
 assert.equal(result.commandCalls,mode==='unsupported'?0:1,mode);
 assert.equal(result.standardCalls,['supported','started-false'].includes(mode)?0:1,mode);
 if(mode!=='unsupported')assert.equal(result.commandActive,true,mode);
 assert.equal(await page.locator('#party-print-status').isVisible(),true);
 assert.match(await page.locator('#party-print-status').innerText(),/Share or menu/);
 // Safari can fire afterprint before its sheet closes. Browser-menu Print must
 // still select the QR poster when used as the fallback after a silent failure.
 await page.evaluate(()=>{dispatchEvent(new Event('afterprint'));dispatchEvent(new Event('beforeprint'))});
 assert.equal(await page.evaluate(()=>document.body.classList.contains('party-printing')),true);
 await page.evaluate(()=>dispatchEvent(new Event('afterprint')));
}
await page.evaluate(async()=>{fixture.ended=true;await render()});assert.equal(await page.locator('#party-print-poster').count(),0);assert.equal(await page.locator('[data-party-print]').count(),0);
assert.deepEqual(errors,[]);await context.close();
// Existing display button still requests printing directly, without navigation.
const tv=await browser.newPage();await tv.route('http://arrify.test/**',route=>{const path=new URL(route.request().url()).pathname;if(path==='/party-display')return route.fulfill({contentType:'text/html',body:fs.readFileSync(root+'/web/party-display.html','utf8')});if(path.endsWith('.js'))return route.fulfill({contentType:'application/javascript',body:fs.readFileSync(root+'/web'+path,'utf8')});if(path.endsWith('.css'))return route.fulfill({contentType:'text/css',body:fs.readFileSync(root+'/web'+path,'utf8')});if(path.endsWith('.svg'))return route.fulfill({contentType:'image/svg+xml',body:svg});if(path.startsWith('/api/'))return route.fulfill({contentType:'application/json',body:JSON.stringify({name:'Display',items:[],total:0,open:true,qr_url:'/qr.svg',playback:{}})});return route.fulfill({body:''})});await tv.addInitScript(()=>{window.EventSource=undefined;window.calls=0;window.print=()=>window.calls++});await tv.goto('http://arrify.test/party-display#'+'b'.repeat(32));await tv.waitForFunction(()=>document.querySelector('#tv-qr').naturalWidth>0);await tv.locator('#tv-print').click();assert.equal(await tv.evaluate(()=>calls),1);await tv.close();
console.log('PASS: WebKit-compatible command, unsupported/false/throw fallback, no duplicate dialog after beforeprint, visible manual-print guidance; direct host print with active user gesture; no new tab/navigation or audio replacement; white QR poster; Wi-Fi print preference independent of display; escaped names; no plaintext password; loading guard and retry; ended-party cleanup; existing display print. Native printer dialog and physical iPad not automated.');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});

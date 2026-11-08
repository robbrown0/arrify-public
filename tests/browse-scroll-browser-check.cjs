'use strict';
const {chromium}=require('playwright'),fs=require('fs'),assert=require('assert/strict');
(async()=>{
 const browser=await chromium.launch({args:['--no-sandbox']});
 try{
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.setContent(fs.readFileSync('web/index.html','utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,'').replace(/<link\b[^>]*>/g,''));
  for(const f of ['style.css','brand.css'])await page.addStyleTag({content:fs.readFileSync('web/'+f,'utf8')});
  const source=fs.readFileSync('web/app.js','utf8'),start=source.indexOf('let browsePager=null;'),end=source.indexOf('async function renderPage()',start);
  await page.addScriptTag({content:`let me={id:1},requestEpoch=0;const esc=s=>String(s).replace(/[&<>"']/g,'');
${source.split('\n').find(l=>l.startsWith('function art('))}
${source.split('\n').find(l=>l.startsWith('function cards('))}
${source.slice(start,end)}
window.__calls=[];window.__hold=false;window.__fail=false;window.__release=null;
const rows=offset=>Array.from({length:60},(_,i)=>({id:offset+i,name:'Music '+(offset+i),artist:'Artist',tracks:12}));
const api=async path=>{__calls.push(path);if(__hold)await new Promise(r=>__release=r);if(__fail)throw Error('Offline');const offset=Number(new URL('https://test.invalid'+path).searchParams.get('offset'));return {items:rows(offset),total:180}};
window.__reset=kind=>{browsePager?.dispose();requestEpoch++;location.hash=kind;document.querySelector('#content').innerHTML=cards(rows(0),kind)+'<div id="browse-more" class="browse-pagination" role="status" aria-live="polite"><span>More music loads as you scroll.</span><button type="button" data-browse-retry hidden>Try again</button></div>';window.scrollTo(0,0);browsePager=startBrowsePagination(requestEpoch,kind)};
document.querySelector('#startup').remove();document.querySelector('#auth').hidden=true;document.querySelector('#shell').hidden=false;`});
  let layouts=0;
  for(const kind of ['albums','artists'])for(const [width,height]of [[393,852],[834,1194],[1194,834]]){
   await page.setViewportSize({width,height});await page.evaluate(kind=>{__calls=[];__hold=true;__fail=false;__reset(kind)},kind);
   await page.waitForTimeout(60);assert.equal(await page.evaluate(()=>__calls.length),0,'no eager full-library download');
   await page.evaluate(()=>scrollTo(0,document.body.scrollHeight));await page.waitForFunction(()=>__calls.length===1);
   const y=await page.evaluate(()=>scrollY);await page.evaluate(()=>{for(let i=0;i<10;i++)dispatchEvent(new Event('scroll'))});
   assert.equal(await page.evaluate(()=>__calls.length),1,'one in-flight batch');await page.evaluate(()=>{__hold=false;__release()});
   await page.waitForFunction(()=>document.querySelectorAll('#content .card').length===120);
   assert.ok(Math.abs(await page.evaluate(()=>scrollY)-y)<2,'existing scroll position stays stable');
   await page.evaluate(()=>{__fail=true;scrollTo(0,document.body.scrollHeight)});await page.locator('[data-browse-retry]').waitFor({state:'visible'});
   assert.equal(await page.evaluate(()=>__calls.length),2);await page.evaluate(()=>{for(let i=0;i<10;i++)dispatchEvent(new Event('scroll'))});await page.waitForTimeout(60);
   assert.equal(await page.evaluate(()=>__calls.length),2,'failure does not retry repeatedly');await page.evaluate(()=>__fail=false);await page.locator('[data-browse-retry]').click();
   await page.waitForFunction(()=>document.querySelectorAll('#content .card').length===180);assert.equal(await page.locator('#browse-more').count(),0);
   assert.deepEqual(await page.evaluate(()=>__calls.map(p=>new URL('https://test.invalid'+p).searchParams.get('offset'))),['60','120','120'],'offset advances only after success');layouts++;
  }
  await page.evaluate(()=>{__calls=[];__hold=true;__reset('albums');scrollTo(0,document.body.scrollHeight)});await page.waitForFunction(()=>__calls.length===1);
  await page.evaluate(()=>{__reset('artists');__hold=false;__release()});await page.waitForTimeout(120);
  assert.equal(await page.locator('#content .card').count(),60,'old response cannot append after navigation');
  assert.ok(await page.locator('#content .card').first().getAttribute('data-go').then(p=>p.startsWith('artists/')));
  await page.evaluate(()=>{browsePager.dispose();window.IntersectionObserver=undefined;__calls=[];__reset('artists');scrollTo(0,document.body.scrollHeight)});
  await page.waitForFunction(()=>document.querySelectorAll('#content .card').length===120);assert.equal(await page.evaluate(()=>__calls.length),1,'scroll fallback loads once');
  assert.deepEqual(errors,[]);console.log('PASS: '+layouts+' Albums/Artists viewport cases; automatic paging, stable scroll, one in-flight read, inline retry/same offset, end of results, stale navigation response and scroll fallback. Mocked catalog APIs.');
 }finally{await browser.close()}
})().catch(e=>{console.error(e.stack);process.exit(1)});

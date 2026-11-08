'use strict';
const fs=require('node:fs'),assert=require('node:assert/strict'),{chromium,webkit}=require('playwright');
(async()=>{
 const browser=await(process.env.BROWSER==='webkit'?webkit:chromium).launch(process.env.BROWSER==='webkit'?{}:{args:['--no-sandbox']});
 try{
 const page=await browser.newPage({viewport:{width:393,height:852}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('https://browse.invalid/**',r=>r.fulfill({body:'<main id="content"></main>',contentType:'text/html'}));await page.goto('https://browse.invalid/#browse/decade-1980');
 await page.addStyleTag({content:fs.readFileSync('web/style.css','utf8')});
 await page.addScriptTag({content:fs.readFileSync('web/browse.js','utf8')});
 const app=fs.readFileSync('web/app.js','utf8');
 await page.evaluate(()=>{
 window.$=s=>document.querySelector(s);window.esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');window.me={id:1};window.requestEpoch=1;
 window.renderPage=async()=>{const key=location.hash.split('/')[1]||'';$('#content').innerHTML=browseBackLink(key)+'<h1>Browse</h1><a id="child" href="#browse/decade-1980">1980s</a><div style="height:2200px"></div>';window.scrollTo(0,0)};
 });
 await page.addScriptTag({content:app.slice(app.indexOf('let renderAttempt=0;'),app.indexOf('// One output owns the stream;'))});
 await page.evaluate(()=>{window.addEventListener('hashchange',()=>render());return render()});
 const cases=[['decades','#browse','Back to Browse'],['genres','#browse','Back to Browse'],['new','#browse','Back to Browse'],['decade-1980','#browse/decades','Back to Decades'],['decade-1980~rock~albums','#browse/decade-1980','Back to 1980s'],['genre-rock~songs','#browse/genres','Back to Genres'],['chart-rock','#browse/hot','Back to What’s Hot'],['releases-overall','#browse','Back to Browse'],['releases-rock','#browse/releases-overall','Back to What’s New']];
 for(const [key,target,label] of cases){await page.evaluate(k=>{location.hash='browse/'+k},key);await page.waitForFunction(k=>document.querySelector('#content').dataset.route==='#browse/'+k,key);assert.equal(await page.locator('.browse-back').getAttribute('href'),target);assert.equal(await page.locator('.browse-back').textContent(),label)}
 await page.evaluate(()=>location.hash='browse/decades');await page.waitForFunction(()=>$('#content').dataset.route==='#browse/decades');await page.evaluate(()=>window.scrollTo(0,600));
 await page.evaluate(()=>document.querySelector('#child').click());await page.waitForFunction(()=>$('#content').dataset.route==='#browse/decade-1980');await page.click('.browse-back');await page.waitForFunction(()=>$('#content').dataset.route==='#browse/decades');assert.ok(Math.abs(await page.evaluate(()=>window.scrollY)-600)<2,'parent position restored');
 for(const width of [393,834,1194]){await page.setViewportSize({width,height:852});const box=await page.locator('.browse-back').boundingBox();assert.ok(box.height>=44);assert.ok(box.width<width);}
 await page.evaluate(()=>location.hash='browse');await page.waitForFunction(()=>$('#content').dataset.route==='#browse');assert.equal(await page.locator('.browse-back').count(),0);
 assert.deepEqual(errors,[]);console.log('PASS: direct category links, decade genre parent, charts/releases parent, root, real render scroll restoration, 44px targets at phone/tablet/desktop. Playback untouched.');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});

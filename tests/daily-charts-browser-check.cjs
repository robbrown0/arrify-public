'use strict';
const fs=require('node:fs'),assert=require('node:assert/strict'),{chromium,webkit}=require('playwright');
(async()=>{const browser=await(process.env.BROWSER==='webkit'?webkit:chromium).launch(process.env.BROWSER==='webkit'?{}:{args:['--no-sandbox']});try{
 const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));let documents=0;
 const html=fs.readFileSync('web/index.html','utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,'').replace(/<link\b[^>]*>/g,'');
 await page.route('https://charts.invalid/**',r=>{if(r.request().isNavigationRequest()){documents++;return r.fulfill({contentType:'text/html',body:html})}return r.fulfill({body:''})});await page.goto('https://charts.invalid/#browse/hot');
 for(const name of ['style.css','brand.css','party.css'])await page.addStyleTag({content:fs.readFileSync('web/'+name,'utf8')});
 await page.evaluate(()=>{window.$=s=>document.querySelector(s);window.esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');window.requestEpoch=1;window.context={};window.me={id:1};window.safe=fn=>Promise.resolve().then(fn);window.section=(title,body)=>'<section><h2>'+title+'</h2>'+body+'</section>';window.empty=section;window.playActions=()=>'';window.api=async path=>path==='/browse'?{decades:[],genres:[{id:'rock',name:'Rock',count:50},{id:'other',name:'Other',count:0}]}:{genre:new URL('https://charts.invalid'+path).searchParams.get('genre'),source:path.includes('genre=overall')?'Apple Music · US':'iTunes Store · US',fetched:1700000000,items:[{rank:1,title:'Test song',artist:'Test artist',request_key:'test'}]};$('#startup').remove();$('#auth').remove();$('#shell').hidden=false;});
 await page.addScriptTag({content:fs.readFileSync('web/browse.js','utf8')});
 for(const key of ['','hot','new','chart-overall','chart-rock','releases-overall','releases-rock']){
  await page.evaluate(async key=>{$('#content').innerHTML=await browseView(key,1)},key);
  const text=await page.locator('#content').innerText();assert.ok(!text.includes('weekly')&&!text.includes('This week'));
  if(key.startsWith('releases-')){assert.ok(text.includes('past 90 days'));assert.ok(text.includes('What’s New'));assert.equal(await page.locator('.browse-tabs [aria-current="page"]').textContent(),'What’s New')}
  if(key==='hot'||key==='new'||key.startsWith('chart-'))assert.equal(await page.locator('.browse-tabs [aria-current="page"]').textContent(),'What’s Hot');
  if(key==='chart-rock')assert.equal(await page.locator('.browse-subfilters a').first().getAttribute('href'),'#browse/chart-overall');
 }
 for(const width of [375,393,834,1194]){await page.setViewportSize({width,height:900});const result=await page.evaluate(()=>{const r=$('#app-refresh').getBoundingClientRect(),bell=$('#request-notifications-button').getBoundingClientRect(),search=$('.search-box').getBoundingClientRect();return {w:r.width,h:r.height,right:r.right,bell:bell.left,left:r.left,search:search.right,overflow:document.documentElement.scrollWidth>innerWidth}});assert.ok(result.w>=44&&result.h>=44);assert.ok(result.right<=result.bell+1&&result.search<=result.left+1);assert.equal(result.overflow,false)}
 assert.deepEqual(errors,[]);
 console.log('PASS: real Browse rendering, new/hot source/filter labels, legacy link, category back links, chart filter routing, header layout at375/393/834/1194. Provider API mocked.');
 }finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});

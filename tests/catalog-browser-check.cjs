// Isolated browser verification; no production accounts or playback commands.
const {chromium}=require('playwright'),fs=require('fs'),assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({args:['--no-sandbox']});try{
 const p=await browser.newPage({viewport:{width:393,height:852}});
 await p.route('**/*',r=>r.fulfill({body:'<main id="content"></main>'}));await p.goto('https://fixture.invalid');await p.setContent('<div class="main-wrap"><header class="topbar"><div class="breadcrumbs">Back</div><form class="search-box"><input id="search" type="search"></form><div class="account">Account</div></header><main id="content"></main></div>');
 await p.addStyleTag({content:fs.readFileSync('web/style.css','utf8')});
 await p.evaluate(()=>{
  window.me={id:1};window.requestEpoch=1;window.context={};window.calls=[];window.state=null;window.liked=new Set();window.shownTracks=[];window.ArrifyPlayer={likeIcon:()=>'<span>♡</span>'};
  window.esc=x=>String(x??'').replace(/[&<>"']/g,'');window.tabs=()=>'';window.table=rows=>'<table>'+rows.map(x=>'<tr><td>'+x.title+'</td></tr>').join('')+'</table>';
  window.cards=rows=>'<div class="card-grid">'+rows.map(x=>'<article>'+x.name+'</article>').join('')+'</div>';
  window.empty=(x,y)=>'<p>'+x+'</p>';window.playActions=()=>'<button>Play</button>';window.syncCollectionButton=()=>{};
  window.api=async path=>{calls.push(path);const q=new URL('https://fixture.invalid'+path).searchParams;
   const term=q.get('q');if(term==='unavailable')throw Error('Arrify is briefly unavailable. Try again in a moment.');const offset=Number(q.get('offset'));if(term==='old')await new Promise(r=>setTimeout(r,400));
   if(offset===50&&!window.retried){window.retried=true;throw Error('transient')}
   return {revision:'v1',total:term?1:51,next_offset:term||offset?null:50,items:Array.from({length:term||offset?1:50},(_,i)=>({id:i+offset,title:term||'Song '+(i+offset),name:term||'Album '+(i+offset),track_count:1,artist:'Fixture artist',album:'Fixture album',artist_id:'artist',album_id:'album',quality:'Lossless'}))};
  };
 });const app=fs.readFileSync('web/app.js','utf8');await p.addScriptTag({content:app.split('\n').filter(l=>['art','cards','tabs','table'].some(n=>l.startsWith('function '+n+'('))).join('\n')});await p.addScriptTag({path:'web/catalog-browser.js'});
 for(const view of ['songs','albums','artists']){
  await p.evaluate(view=>{window.pager?.dispose();document.querySelector('#content').innerHTML=catalogBrowserView(view);window.pager=mountCatalogBrowser(view,1)},view);
  await p.locator('#catalog-count').filter({hasText:'51'}).waitFor();assert.equal(await p.locator('#search').isVisible(),false);assert.equal(await p.locator('input[type=search]:visible').count(),1);
  await p.locator('#catalog-sort').selectOption('recent');
  await p.waitForFunction(()=>calls.at(-1).includes('sort=recent'));
  await p.locator('#catalog-query').fill('old');await p.waitForTimeout(280);await p.locator('#catalog-query').fill('new');
  await p.waitForTimeout(800);assert.equal(await p.locator(view==='songs'?'#catalog-results .track-name':'#catalog-results h3').textContent(),'new');
  assert.ok(await p.evaluate(()=>calls.some(x=>x.includes('kind='+('songs'===document.querySelector('h1').textContent.toLowerCase()?'tracks':document.querySelector('h1').textContent.toLowerCase())))));
  assert.equal(await p.locator('#catalog-sort').inputValue(),'relevance');assert.equal(await p.locator('#catalog-sort').isDisabled(),true);
  assert.equal(await p.evaluate(()=>new URL('https://fixture.invalid'+calls.at(-1)).searchParams.get('sort')),'relevance');
  if(view==='songs')assert.equal(await p.evaluate(()=>JSON.parse(context.source.id).sort),'relevance');
  await p.locator('#catalog-query').fill('');await p.waitForTimeout(350);assert.equal(await p.locator('#catalog-sort').inputValue(),'recent');assert.equal(await p.locator('#catalog-sort').isEnabled(),true);
  for(const width of [320,393,834,1440]){await p.setViewportSize({width,height:852});assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false)}
 }
 await p.evaluate(()=>{pager.dispose();document.querySelector('#content').innerHTML=catalogBrowserView('songs');window.retried=false;window.pager=mountCatalogBrowser('songs',1)});
 await p.locator('#catalog-count').filter({hasText:'51'}).waitFor();await p.locator('#catalog-more').scrollIntoViewIfNeeded();await p.locator('#catalog-more button').waitFor({state:'visible'});await p.locator('#catalog-more button').click();
 await p.waitForFunction(()=>document.querySelectorAll('#catalog-results tbody tr').length===51);
 assert.ok(await p.evaluate(()=>calls.some(x=>x.includes('offset=50')&&x.includes('revision=v1'))));
 await p.locator('#catalog-query').fill('unavailable');await p.waitForTimeout(350);assert.equal(await p.locator('#catalog-count').textContent(),'');assert.match(await p.locator('#catalog-more span').textContent(),/briefly unavailable/);
 await p.evaluate(()=>{pager.dispose();document.querySelector('#content').replaceChildren()});assert.equal(await p.locator('#search').isVisible(),true);
 console.log('PASS: scoped Songs/Albums/Artists search, sort, stale responses, revision paging retry, responsive controls');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});

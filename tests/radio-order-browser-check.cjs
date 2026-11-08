'use strict';
const fs=require('node:fs'),assert=require('node:assert/strict'),{chromium,webkit}=require('playwright');
(async()=>{
 const browser=await (process.env.BROWSER==='webkit'?webkit:chromium).launch(process.env.BROWSER==='webkit'?{}:{args:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:834,height:1194}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('https://radio.invalid/**',route=>route.fulfill({body:fs.readFileSync('web/assets/arrify-mark.jpg'),contentType:'image/jpeg'}));
  await page.setContent('<base href="https://radio.invalid">'+fs.readFileSync('web/index.html','utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,'').replace(/<link\b[^>]*>/g,''));
  for(const name of ['style.css','brand.css','party.css'])await page.addStyleTag({content:fs.readFileSync('web/'+name,'utf8')});
  await page.addScriptTag({path:process.cwd()+'/web/player.js'});
  await page.evaluate(()=>{
   window.$=selector=>document.querySelector(selector);
   window.context={radio:{kind:'track',seed:'1'},radioTracks:[1,3,2,4],total:4};
   window.shownTracks=context.radioTracks.map(id=>({id,title:'Song '+id,artist:'Artist',album:'Album',artwork:'/assets/arrify-mark.jpg'}));
   window.state={current:1,upcoming:[3,2,4],history:[],queue:shownTracks.slice(1),track:shownTracks[0],next:shownTracks[1],channel:null,station:{name:'Song Radio',kind:'track',seed:'1',mode:'mix'}};
   window.commands=[];window.primes=0;window.provider=false;
   window.command=async(data,play)=>{commands.push({data:structuredClone(data),play});};
   window.safe=fn=>{window.pending=Promise.resolve().then(fn)};
   window.nativePlayback={prime:()=>{primes++}};
   window.remoteListening=()=>false;window.listeningOnline=()=>true;window.toast=()=>{};
   window.ArrifyApple={isActive:()=>provider};window.ArrifySpotify={isActive:()=>false};
   window.ArrifyOutput={describe:()=>({apple:false,wireless:false,host:'This device',label:'This device',kind:'output'})};
   ArrifyPlayer.init({getAudio:()=>$('#audio'),getState:()=>state,getListening:()=>({}),getTransport:()=>({}),getLiked:()=>new Set(),getHome:()=>({collections:[]}),queueMarkup:t=>'<p>'+t.title+'</p>',clearQueue(){}});
   $('#startup').remove();$('#auth').remove();$('#shell').hidden=false;
  });
  const app=fs.readFileSync('web/app.js','utf8');
  const functions=['function playActions(','async function playSelection('].map(prefix=>{
   const line=app.split('\n').find(line=>line.startsWith(prefix));assert.ok(line,prefix);return line;
  }).join('\n');
  await page.addScriptTag({content:functions});
  const start=app.indexOf("document.addEventListener('click',e=>{const b=e.target.closest('button,[data-go],[data-play]')"),end=app.indexOf("\ndocument.addEventListener('keydown'",start);assert.ok(start>=0&&end>start);
  await page.addScriptTag({content:app.slice(start,end)});
  await page.evaluate(()=>{$('#content').innerHTML=playActions(4)+'<button data-play="2">Play song 2</button>';ArrifyPlayer.sync()});
  assert.equal(await page.locator('#play-all').textContent(),'▶ Play mix');
  assert.equal(await page.locator('#shuffle-all').count(),1);
  await page.click('#play-all');await page.evaluate(()=>pending);
  assert.deepEqual(await page.evaluate(()=>commands.at(-1).data),{action:'play',radio:{kind:'track',seed:'1',ids:[1,3,2,4]},track_id:1});
  await page.click('[data-play="2"]');await page.evaluate(()=>pending);
  assert.deepEqual(await page.evaluate(()=>commands.at(-1).data.radio.ids),[1,3,2,4]);assert.equal(await page.evaluate(()=>commands.at(-1).data.track_id),2);
  await page.click('#shuffle-all');await page.evaluate(()=>pending);
  assert.deepEqual(await page.evaluate(()=>commands.slice(-2).map(c=>c.data.action)),['play','shuffle']);
  assert.equal(await page.evaluate(()=>primes),3,'existing synchronous user-gesture priming preserved');
  assert.deepEqual(await page.evaluate(()=>context.radio),{kind:'track',seed:'1'},'play request must not mutate page context');
  assert.ok(!(await page.locator('#shuffle').isDisabled()));
  assert.ok(!(await page.locator('#now-playing-dialog [data-control="shuffle"]').isDisabled()));
  for(const kind of ['legacy','channel','party','mix']){
   await page.evaluate(kind=>{
    state.party_id=kind==='party'?'party':null;state.channel=kind==='channel'?'test-channel':null;
    state.station=kind==='channel'||kind==='party'?null:{name:'Radio',kind:'track',seed:'1',...(kind==='mix'?{mode:'mix'}:{})};ArrifyPlayer.sync();
   },kind);
   assert.equal(await page.locator('#shuffle').isDisabled(),kind!=='mix',kind);
   assert.equal(await page.locator('#now-playing-dialog [data-control="shuffle"]').isDisabled(),kind!=='mix',kind);
  }
  for(const width of [393,834,1194]){
   await page.setViewportSize({width,height:900});
   assert.equal(await page.locator('#content .detail-actions').evaluate(el=>el.scrollWidth>el.clientWidth+1),false);
  }
  assert.deepEqual(errors,[]);
  console.log('PASS: displayed radio IDs preserved for Play and row selection; explicit Shuffle; synchronous priming; shuffle enabled in both mix player controls, blocked for channel/legacy/party; 393/834/1194 layout. Commands mocked; physical playback not claimed.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});

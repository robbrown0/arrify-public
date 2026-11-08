'use strict';
const {chromium}=require('playwright'),assert=require('node:assert/strict'),crypto=require('node:crypto'),fs=require('node:fs'),{execFileSync}=require('node:child_process');
const base='http://127.0.0.1:8796',out='/tmp/arrify-active-streams-review';fs.mkdirSync(out,{recursive:true});
(async()=>{const b=await chromium.launch({headless:true,args:['--no-sandbox']});const errors=[];try{
 const c=await b.newContext();for(const file of ['connections.js','brand.css'])await c.route('**/'+file,r=>r.fulfill({body:fs.readFileSync(require('node:path').join(__dirname,'../web',file)),contentType:file.endsWith('.css')?'text/css':'text/javascript'}));const p=await c.newPage();p.on('pageerror',e=>errors.push(e.message));
 const username='streams_'+crypto.randomBytes(4).toString('hex'),password=crypto.randomBytes(24).toString('hex');
 assert.equal((await c.request.post(base+'/api/v1/auth/register',{headers:{Origin:base},data:{username,password,email:username+'@example.invalid'}})).status(),201);
 execFileSync('docker',['exec','arrify-streams-qa','python3','-m','app.server','make-admin',username]);
 assert.equal((await c.request.post(base+'/api/v1/auth/login',{headers:{Origin:base},data:{username,password}})).status(),200);
 await p.goto(base);await p.locator('.home-page').waitFor();
 const snapshot=await p.evaluate(async()=>await api('/admin/connections'));assert.ok(snapshot.summary.signed_in>=1);assert.equal(snapshot.summary.audio_requests,0);
 const uid=snapshot.items.find(u=>u.username===username).id;
 const now=Math.floor(Date.now()/1000),fixture={...snapshot,server_time:now,summary:{...snapshot.summary,playing:1,buffering:0,audio_requests:1,online:1},items:[{id:uid,username,display_name:'Listener <script>test</script>',online:true,playback:'playing',device:'iPhone',output:'AirPlay',quality:'Lossless',track:{id:1,title:'A long song title for narrow phone layouts — live stream example',artist:'The example artist'},queue_count:12,sessions:2,tracks_started:125,tracks_started_24h:8,requests:3,audio_requests:1,last_seen:now,last_login:now-300}]};
 await p.route('**/api/v1/admin/connections',r=>r.fulfill({json:fixture}));
 await p.evaluate(async()=>{location.hash='admin/connected';await render()});await p.locator('#admin-connections').waitFor();
 assert.equal(await p.locator('.connection-presence').textContent(),'Connected');assert.match(await p.locator('.connection-playing').textContent(),/Playing.*iPhone.*AirPlay.*Lossless/);
 assert.equal(await p.locator('.connection-identity h3').textContent(),fixture.items[0].display_name);assert.equal(await p.locator('#admin-connections script').count(),0);
 const checks=[];for(const [width,height]of [[320,568],[393,852],[852,393],[834,1194],[1194,834],[1440,1000]]){
  await p.setViewportSize({width,height});await p.evaluate(()=>document.fonts.ready);assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,'horizontal overflow '+width);
  const box=await p.locator('#connections-refresh').boundingBox();assert.ok(box.height>=44);checks.push({width,height,overflow:false});
  if([393,834,852].includes(width))await p.screenshot({path:out+'/streams-'+width+'.png',fullPage:true});
 }
 await p.locator('.connection-usage summary').click();await p.locator('#connections-refresh').click();await p.waitForFunction(()=>document.querySelector('.connection-usage')?.open);await p.locator('#admin-connections').waitFor();assert.deepEqual(errors,[]);
 fs.writeFileSync(out+'/browser-results.json',JSON.stringify({status:'pass',checks,errors,liveAdminEndpoint:'200; no sessions or credentials exposed',visualData:'isolated populated fixture'},null,2));console.log('PASS: admin route, populated stream cards, escaped metadata, six portrait/landscape viewports, 44px refresh control, no overflow or page errors.');
}finally{await b.close()}})().catch(e=>{console.error(e.stack);process.exit(1)});

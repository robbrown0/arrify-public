const {chromium}=require('playwright');
const {execFileSync}=require('node:child_process');
const crypto=require('node:crypto');
const http=require('node:http');
const assert=require('node:assert/strict');
const base='http://localhost:8792';
const username='qa_'+crypto.randomBytes(4).toString('hex'),other=username+'_other',password=crypto.randomBytes(24).toString('hex');
const errors=[];let browser;
function promote(name){execFileSync('docker',['exec','arrify-player','python3','-m','app.server','make-admin',name],{stdio:'pipe'})}
async function main(){browser=await chromium.launch({headless:true,args:['--no-sandbox']});const ctx=await browser.newContext({viewport:{width:1440,height:1020}});const page=await ctx.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('response',r=>{if(r.status()>=500)errors.push(r.status()+' '+r.url())});
 await page.goto(base);await page.locator('#auth').waitFor({state:'visible'});
 async function raw(path,data){return page.evaluate(async({path,data})=>{const r=await fetch('/api/v1'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});return {status:r.status,value:await r.json()}},{path,data})}
 assert.equal((await raw('/auth/register',{username,password,email:username+'@example.invalid'})).status,201);
 assert.equal((await raw('/auth/login',{username,password})).status,403);
 promote(username);
 await page.locator('#username').fill(username);await page.locator('#password').fill(password);await page.locator('#auth-form button').click();await page.locator('.hero').waitFor({timeout:30000});
 assert.ok(await page.locator('.station').count()>=5);
 await page.screenshot({path:require('node:path').resolve(__dirname,'../docs/desktop.png'),fullPage:true});
 let auth=await page.evaluate(()=>fetch('/api/v1/me').then(r=>r.json()));assert.equal(auth.admin,1);
 const forbidden=await raw('/favorites',{track_id:1});assert.equal(forbidden.status,403);
 for(const route of ['albums','artists','moods','channels','playlists','favorites','history','requests','admin']){await page.evaluate(route=>location.hash=route,route);await page.waitForTimeout(200);await page.locator('#content h1').first().waitFor();assert.equal(await page.locator('#toast').isVisible(),false,route+' toast');}
 const items=await page.evaluate(()=>fetch('/api/v1/collections').then(r=>r.json()));const jazz=items.items.find(x=>x.slug==='jazz-for-sleep');assert.equal(jazz.tracks,0);
 await page.evaluate(()=>location.hash='collection/jazz-for-sleep');await page.getByText('No local songs yet',{exact:true}).waitFor();assert.ok(await page.locator('#play-all').isDisabled());
 await page.evaluate(()=>location.hash='albums');await page.locator('.card').first().waitFor();await page.locator('.card').first().click();await page.locator('.track-table').waitFor();
 const track=await page.locator('[data-play]').first().getAttribute('data-play');
 await page.locator('[data-play]').first().click();await page.waitForFunction(()=>document.querySelector('#audio').currentTime>0,{},{timeout:25000});
 await page.locator('#now-like').click();await page.waitForTimeout(150);
 const favorites=await page.evaluate(()=>fetch('/api/v1/favorites').then(r=>r.json()));assert.ok(favorites.tracks.some(t=>String(t.id)===track));
 await page.locator('#new-playlist').click();await page.locator('#playlist-name').fill('A fresh rotation');await page.locator('#playlist-form button').click();await page.getByText('A blank canvas',{exact:true}).waitFor();
 await page.evaluate(()=>location.hash='channels');await page.locator('[data-tune]').first().click();await page.waitForTimeout(300);let radio=await page.evaluate(()=>fetch('/api/v1/player').then(r=>r.json()));assert.ok(radio.next);const next=radio.next.id;let second=await page.evaluate(()=>fetch('/api/v1/player').then(r=>r.json()));assert.equal(second.next.id,next);await page.locator('#next').click();await page.waitForTimeout(200);second=await page.evaluate(()=>fetch('/api/v1/player').then(r=>r.json()));assert.equal(second.current,next);
 await page.locator('#queue-button').click();await page.locator('#queue-panel').waitFor({state:'visible'});assert.ok(await page.locator('.queue-track').count()>=2);await page.locator('#close-queue').click();
 const token=(await raw('/auth/token',{username,password})).value.access_token;assert.ok(token);
 const tech=await ctx.request.get(base+'/api/v1/technical/'+track,{headers:{Authorization:'Bearer '+token}});assert.equal(tech.status(),200);assert.ok((await tech.json()).duration>0);
 const res=await ctx.request.get(base+'/api/v1/stream/'+track,{headers:{Authorization:'Bearer '+token,Range:'bytes=0-31'}});assert.equal(res.status(),206);assert.equal((await res.body()).length,32);
 for(const profile of ['aac','mp3'])await new Promise((resolve,reject)=>{const r=http.get(base+'/api/v1/stream/'+track+'?profile='+profile,{headers:{Authorization:'Bearer '+token}},s=>{if(s.statusCode!==200)return reject(Error('Transcode status '+s.statusCode));s.once('data',b=>{assert.ok(b.length>0);r.destroy();resolve()})});r.on('error',e=>{if(e.code!=='ECONNRESET')reject(e)});r.setTimeout(25000,()=>{r.destroy();reject(Error('Transcode timeout'))})});
 assert.equal((await raw('/auth/register',{username:other,password,email:other+'@example.invalid'})).status,201);promote(other);
 const t2=(await raw('/auth/token',{username:other,password})).value.access_token;
 const secondFavorites=await ctx.request.get(base+'/api/v1/favorites',{headers:{Authorization:'Bearer '+t2}});assert.equal((await secondFavorites.json()).tracks.length,0);
 await page.setViewportSize({width:390,height:844});await page.evaluate(()=>location.hash='home');await page.locator('.hero').waitFor();await page.waitForTimeout(400);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false);await page.screenshot({path:require('node:path').resolve(__dirname,'../docs/mobile.png'),fullPage:true});await page.locator('#mobile-queue').click();await page.locator('#queue-profile').waitFor({state:'visible'});
 await page.evaluate(()=>location.hash='search');await page.locator('#close-queue').click();await page.locator('#search').fill('artist:Radiohead AND recording:Creep');await page.waitForTimeout(900);await page.locator('#external-search').click();await page.locator('.request-row').first().waitFor({timeout:65000});assert.ok(await page.locator('[data-request]').count()>0); // Search only: never acquire music during QA.
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({result:'passed',checks:['approval','desktop navigation','jazz unavailable','browser audio playback','favorites','personal playlist','radio stable next','queue','CSRF rejection','native bearer auth','account isolation','byte ranges','AAC transcode','MP3 transcode','responsive mobile','metadata search'],screenshots:['docs/desktop.png','docs/mobile.png']}));
}
main().catch(e=>{console.error(e.message);process.exitCode=1}).finally(async()=>{if(browser)await browser.close();execFileSync('docker',['exec','arrify-player','python3','-c',`from app.accounts import Accounts; import sys; a=Accounts('/data'); names=sys.argv[1:]; ids=[r[0] for n in names for r in a.db.execute('SELECT id FROM users WHERE username=?',(n,))]; [(a.db.execute('DELETE FROM '+table+' WHERE '+column+'=?',(i,))) for i in ids for table,column in [('sessions','user_id'),('favorites','user_id'),('personal_playlists','user_id'),('player','user_id'),('history','user_id'),('music_requests','user_id')]]; [a.db.execute('DELETE FROM users WHERE id=?',(i,)) for i in ids]`,username,other],{stdio:'pipe'})});

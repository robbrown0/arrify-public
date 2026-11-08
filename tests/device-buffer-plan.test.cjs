const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const app=fs.readFileSync('web/app.js','utf8');
const sync=app.slice(app.indexOf('function syncDeviceBuffer('),app.indexOf("navigator.serviceWorker?.addEventListener?.('controllerchange'"));
function setup({wireless=false,connected=false,saveData=false,mode='ahead',owns=true,profile='mp3',level='off',provider=false,gapless=false}={}){
 const posts=[];const audio={webkitCurrentPlaybackTargetIsWireless:wireless,remote:{state:connected?'connected':'disconnected'}};
 const ctx={audio,navigator:{connection:{saveData},serviceWorker:{controller:{postMessage:x=>posts.push(x)}}},me:{buffer_scope:'session'},state:{current:1,listening:{playing:true,epoch:5},upcoming:[2,3,4]},ownsListening:()=>owns,deviceBufferMode:()=>mode,window:{ArrifyApple:{isActive:()=>provider}},gaplessPlayback:{},playback:{},soundPreferences:{leveling:level},$:()=>({value:profile}),nextForPlayback:()=>({id:2}),location:{origin:'https://music.example'},listeningDevice:{id:'phone'}};
 if(gapless)ctx.playback=ctx.gaplessPlayback;
 vm.createContext(ctx);vm.runInContext(sync,ctx);return {ctx,posts,audio,sync:()=>vm.runInContext('syncDeviceBuffer()',ctx)};
}
test('read-ahead plans the current song and next two with either AirPlay signal or neither',()=>{
 for(const options of [{},{wireless:true},{connected:true},{wireless:true,connected:true}]){
  const f=setup(options);f.sync();const p=f.posts[0];assert.equal(p.enabled,true);assert.deepEqual(Array.from(p.urls,u=>Number(new URL(u).pathname.split('/').pop())),[1,2,3]);assert.equal(p.scope,'session');assert.ok(p.urls.every(u=>u.startsWith('https://music.example/api/v1/stream/')&&u.includes('owner_epoch=5')));
 }
});
test('route changes preserve the plan and never alter the playing media element',()=>{
 const f=setup();f.audio.src='https://music.example/playing';f.sync();f.audio.webkitCurrentPlaybackTargetIsWireless=true;f.sync();f.audio.remote.state='connected';f.sync();assert.equal(JSON.stringify(f.posts[0]),JSON.stringify(f.posts[2]));assert.equal(f.audio.src,'https://music.example/playing');
});
test('data limits, ownership, unsupported transport and logout still disable read-ahead',()=>{
 for(const options of [{saveData:true},{mode:'off'},{owns:false},{provider:true},{gapless:true},{profile:'flac'},{profile:'original',level:'track'}]){const f=setup({wireless:true,...options});f.sync();assert.equal(f.posts[0].enabled,false);assert.equal(f.posts[0].urls.length,0)}
 const f=setup({wireless:true});vm.runInContext('syncDeviceBuffer(true)',f.ctx);assert.equal(f.posts[0].enabled,false);
});
test('the active profile and leveling are retained in prefetched URLs',()=>{
 const f=setup({wireless:true,profile:'aac',level:'album'});f.sync();assert.ok(f.posts[0].urls.every(u=>new URL(u).searchParams.get('profile')==='aac'&&new URL(u).searchParams.get('level')==='album'));
});

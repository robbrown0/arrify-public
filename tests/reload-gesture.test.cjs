const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const app=fs.readFileSync('web/app.js','utf8');
const recovery=app.slice(app.indexOf('function prepareReloadPlayback('),app.indexOf('async function reattachListeningPage('));
const playing=app.slice(app.indexOf('function listeningPlaying('),app.indexOf('function listeningView('));
const progress=app.slice(app.indexOf('function progressPosition('),app.indexOf('function duration('));
function setup({restored=true,owner=true,stable=true,remote=false,online=true,provider=false}={}){
 const calls=[],audio={src:'',paused:true,dataset:{},currentTime:0};
 const native={priming:false,loading:false,intent:false,snapshot:()=>({stableElement:stable}),stop(){calls.push('stop');audio.src='';this.loading=false;this.intent=false},prime(){calls.push('prime');this.priming=true;audio.src='blob:silent';audio.paused=false}};
 const ctx={window:{ArrifyApple:{isActive:()=>provider}},listeningDevice:{restored},ownsListening:()=>owner,remoteListening:()=>remote,listeningOnline:()=>online,state:{track:{id:7},current:7,position:83,listening:{playing:true}},audio,nativePlayback:native,playback:native,listeningNeedsGesture:false,showPlaybackStatus:(message,busy)=>calls.push({message,busy})};
 vm.createContext(ctx);vm.runInContext(recovery+playing+progress,ctx);return {ctx,calls,run:s=>vm.runInContext(s,ctx)};
}
test('stable owner reload waits for a tap without claiming playback or losing saved position',()=>{
 const f=setup();f.run('prepareReloadPlayback()');assert.equal(f.ctx.listeningNeedsGesture,true);assert.equal(f.run('listeningPlaying()'),false);assert.deepEqual(f.calls,[{message:'Tap play to continue listening.',busy:false}]);assert.equal(f.run('progressPosition()'),83);
});
test('a tap primes synchronously and still means Resume, not Pause; silent prime cannot reset position',()=>{
 const f=setup();f.run('prepareReloadPlayback();unlockLocalPlayback()');assert.equal(f.ctx.listeningNeedsGesture,false);assert.ok(f.calls.includes('prime'));assert.equal(f.run('listeningPlaying()'),false);assert.equal(f.run('progressPosition()'),83);assert.equal(f.ctx.state.position,83);
});
test('blocked loaded audio can be replaced on the same element with position preserved',()=>{
 const f=setup();f.ctx.audio.src='https://music/song';f.ctx.audio.dataset.trackId='7';f.ctx.audio.currentTime=91;f.ctx.nativePlayback.loading=true;f.ctx.listeningNeedsGesture=true;f.run('unlockLocalPlayback()');assert.equal(f.ctx.state.position,91);assert.equal(f.ctx.nativePlayback.loading,false);assert.equal(f.run('progressPosition()'),91);assert.equal(f.run('listeningPlaying()'),false);
});
test('paused reload, desktop autoplay, providers and other-device playback do not enter local recovery',()=>{
 for(const args of [{restored:false},{owner:false},{stable:false},{provider:true}]){const f=setup(args);f.run('prepareReloadPlayback()');assert.equal(f.ctx.listeningNeedsGesture,false)}
 const f=setup();f.ctx.state.listening.playing=false;f.run('prepareReloadPlayback()');assert.equal(f.ctx.listeningNeedsGesture,false);
 const remote=setup({remote:true,owner:false});remote.run('unlockLocalPlayback()');assert.equal(remote.calls.length,0);
});
test('reconciliation does not start or retry audio while waiting for a gesture',async()=>{
 const f=setup();Object.assign(f.ctx,{listeningSigningOut:false,listeningReceived:0,listeningApplied:-1,listeningSeekApplied:-1,listeningReconciling:false,nativeAdvance:null,retryTimer:null,pendingAdvance:null,pendingRecovery:null,navigator:{},queueOpen:false,location:{hash:'#home'},listeningQueueSignature:'',listeningPushPending:false,listeningPolling:false,clearTimeout,syncDeviceBuffer:()=>{},applyState:async()=>{},paintListening:()=>{},refreshListeningEvents:()=>{},listeningHeartbeat:()=>{},startCurrentPlayback:()=>{throw Error('must not autoplay')},handlePlaybackError:()=>{throw Error('must not retry')}});
 f.ctx.state.listening={owner:'phone',epoch:2,intent:3,seek_intent:1,playing:true,action:'resume'};f.run('prepareReloadPlayback()');vm.runInContext(app.slice(app.indexOf('async function consumeListening('),app.indexOf('async function pollListening(')),f.ctx);await f.run('consumeListening(state)');assert.equal(f.ctx.listeningNeedsGesture,true);assert.equal(f.ctx.listeningApplied,3);assert.equal(f.run('progressPosition()'),83);
 f.ctx.ownsListening=()=>false;await f.run('consumeListening({...state,listening:{...state.listening,owner:"tablet"}})');assert.equal(f.ctx.listeningNeedsGesture,false);assert.equal(f.ctx.listeningApplied,-1);assert.equal(f.calls.filter(x=>typeof x==='object').at(-1).message,'');
});

'use strict';
const fs=require('node:fs'),vm=require('node:vm'),test=require('node:test'),assert=require('node:assert/strict');
const source=fs.readFileSync('web/app.js','utf8');
const code=source.slice(source.indexOf('function airplayFinishedWithoutEnded('),source.indexOf('\nbindAudio(audio);'));
function setup(overrides={}){
 const element={dataset:{trackId:'29613'},duration:190.383552208,currentTime:190.284,paused:true,ended:false,error:null,seeking:false,webkitCurrentPlaybackTargetIsWireless:true,addEventListener(){},...overrides};
 const playback={intent:true,loading:false,priming:false,interrupt(){this.intent=false},pause(){this.intent=false;element.paused=true},observePlaying(){this.intent=true}};
 const button={setAttribute(){},textContent:''},events=[];
 const c={audio:element,playback,nativePlayback:playback,state:{current:29613},ending:false,nativeAdvance:null,pendingAdvance:null,listeningControl:0,scrubbing:false,retryTimer:null,pendingRecovery:null,listeningReconciling:false,pauseSerial:0,ownsListening:()=>true,clock:{snapshot:()=>({pending:null})},window:{ArrifyApple:{isActive:()=>false},ArrifySpotify:{isActive:()=>false}},navigator:{},recordPlayback:e=>events.push(e),uploadPlayback(){},saveProgress(){c.saved++},saved:0,advanceAutomatically(){c.advanced++;c.ending=true},advanced:0,$:()=>button,setTimeout,clearTimeout};
 vm.createContext(c);vm.runInContext(code,c);c.bindAudio(element);return {c,element,playback,events};
}
test('recorded AirPlay endpoint pause advances once without ended',()=>{
 const {c,element,events}=setup();element.onpause();assert.equal(c.advanced,1);assert.equal(c.playback.intent,true);assert.equal(c.saved,0);assert.deepEqual(events,['pause','airplay-end']);element.ended=true;element.onended();assert.equal(c.advanced,1);
});
test('normal ended still advances once',()=>{const {c,element}=setup({ended:true});element.onpause();element.onended();element.onended();assert.equal(c.advanced,1)});
test('ordinary pauses and deliberate user pause do not advance',()=>{
 for(const overrides of [{currentTime:100},{currentTime:190.0},{webkitCurrentPlaybackTargetIsWireless:false}]){const {c,element}=setup(overrides);element.onpause();assert.equal(c.advanced,0);assert.equal(c.playback.intent,false)}
 const {c,element,playback}=setup();playback.pause();element.onpause();assert.equal(c.advanced,0);
});
test('ownership, transport, seeking, errors and in-flight actions are guarded',()=>{
 const variants=[c=>c.ownsListening=()=>false,c=>c.playback.loading=true,c=>c.listeningReconciling=true,c=>c.listeningControl=1,c=>c.scrubbing=true,c=>c.retryTimer=1,c=>c.pendingRecovery={},c=>c.pendingAdvance={},c=>c.nativeAdvance={},c=>c.clock.snapshot=()=>({pending:190}),c=>c.playback={...c.playback},c=>c.state.current=7,c=>c.window.ArrifyApple.isActive=()=>true,c=>c.window.ArrifySpotify.isActive=()=>true];
 for(const change of variants){const {c,element}=setup();change(c);element.onpause();assert.equal(c.advanced,0)}
 for(const overrides of [{seeking:true},{error:{code:2}},{duration:NaN},{duration:Infinity},{duration:0},{currentTime:191}]){const {c,element}=setup(overrides);element.onpause();assert.equal(c.advanced,0)}
});
test('standard remote route also qualifies at the endpoint',()=>{const {c,element}=setup({webkitCurrentPlaybackTargetIsWireless:false,remote:{state:'connected'}});element.onpause();assert.equal(c.advanced,1)});
test('lock-screen pause cancels intent before asynchronous command',()=>{
 const {c,element}=setup();const handlers={};c.navigator.mediaSession={setActionHandler:(k,v)=>handlers[k]=v};c.command=()=>new Promise(()=>{});c.safe=fn=>fn();c.seekTo=()=>{};
 vm.runInContext(source.split('\n').find(l=>l.startsWith('function bindLocalMediaSession()')),c);c.bindLocalMediaSession();handlers.pause();assert.equal(c.playback.intent,false);element.onpause();assert.equal(c.advanced,0);
});
test('endpoint fallback uses existing synchronous handoff and guarded next request',async()=>{
 const {c,element}=setup();let release;const gate=new Promise(r=>release=r),commands=[];
 Object.assign(c,{state:{current:29613,track:{id:29613},next:{id:35874},listening:{epoch:2,revision:5,playing:true}},listeningSigningOut:false,crypto:{randomUUID:()=> 'endpoint-transition'},advanceRetryTimer:null,retryCount:0,compatibleFallback:false,pollListening:async()=>{},showPlaybackStatus(){},handlePlaybackError(){},startCurrentPlayback:async track=>{element.dataset.trackId=String(track.id);element.currentTime=0;element.ended=false;c.playback.loading=true},command:async body=>{commands.push(body);await gate;c.state.current=35874;c.state.track={id:35874};c.state.next={id:27966};c.playback.loading=false}});
 vm.runInContext(source.slice(source.indexOf('function nextForPlayback()'),source.indexOf('\nlet requestNoticeState=')),c);
 element.onpause();assert.equal(element.dataset.trackId,'35874');assert.equal(commands.length,1);assert.equal(commands[0].automatic,true);assert.equal(commands[0].expected_current,29613);assert.equal(commands[0].expected_next,35874);assert.equal(commands[0].owner_epoch,2);
 element.onpause();element.onended();assert.equal(commands.length,1);assert.equal(c.playback.intent,true);
 release();await new Promise(r=>setImmediate(r));assert.equal(c.state.current,35874);assert.equal(c.ending,false);assert.equal(c.nativeAdvance,null);
});

test('repeat selects the correct prepared source without changing next-song guards',()=>{
 const c={state:{track:{id:1},next:{id:2},repeat:'one'}};vm.createContext(c);vm.runInContext(source.split('\n').find(l=>l.startsWith('function nextForPlayback()')),c);assert.equal(c.nextForPlayback().id,1);c.state.repeat='all';assert.equal(c.nextForPlayback().id,2);c.state.next=null;assert.equal(c.nextForPlayback().id,1);c.state.party_id='party';assert.equal(c.nextForPlayback(),null);delete c.state.party_id;c.state.repeat='off';assert.equal(c.nextForPlayback(),null);
});

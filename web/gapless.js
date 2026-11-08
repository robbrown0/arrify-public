/* Opt-in local-output transport. Two decoded songs maximum; no persisted audio. */
'use strict';
window.ArrifyGapless={
 supported(){return Boolean(window.AudioContext)&&!navigator.connection?.saveData&&!(/iPhone|iPad|iPod/.test(navigator.userAgent||'')||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1)||typeof HTMLMediaElement.prototype.webkitShowPlaybackTargetPicker==='function')},
 schedule(context,buffer,destination,when,offset=0){const node=context.createBufferSource();node.buffer=buffer;node.connect(destination);node.start(Math.round(when*context.sampleRate+1e-7)/context.sampleRate,offset);return node},
 create(config){
  const context=config.context||new AudioContext({sampleRate:44100}),gain=context.createGain();gain.connect(context.destination);
  let current=null,next=null,wanted=null,epoch=0,playing=false,loading=false,pending=false,preparing=null,prepareToken=0,prepareAbort=null,volume=.8,muted=false,aborts=new Set();
  const fake=new EventTarget();fake.dataset={};fake.preload='auto';fake.readyState=0;fake.error=null;fake.seeking=false;fake.playbackRate=1;fake.sinkId='';fake.setAttribute=()=>{};fake.removeAttribute=()=>{};fake.load=()=>{};
  const fire=name=>{fake.dispatchEvent(new Event(name));fake['on'+name]?.(new Event(name))};
  const time=()=>!current?0:Math.min(current.buffer.duration,current.offset+(playing?Math.max(0,context.currentTime-current.start):0));
  Object.defineProperties(fake,{currentTime:{get:time,set:value=>{if(!current)return;const position=Math.max(0,Math.min(current.buffer.duration,Number(value)));if(!Number.isFinite(position))return;halt();current.offset=position;fake.seeking=false;if(playing)start();fire('seeked');fire('timeupdate')}},duration:{get:()=>current?.buffer.duration||NaN},paused:{get:()=>!playing},ended:{get:()=>Boolean(pending)},currentSrc:{get:()=>current?'gapless:'+current.track.id:''},src:{get:()=>fake.currentSrc},volume:{get:()=>volume,set:value=>{volume=Math.max(0,Math.min(1,Number(value)));gain.gain.value=muted?0:volume;fire('volumechange')}},muted:{get:()=>muted,set:value=>{muted=Boolean(value);gain.gain.value=muted?0:volume;fire('volumechange')}}});gain.gain.value=volume;
  if(context.setSinkId)fake.setSinkId=async id=>{await context.setSinkId(id);fake.sinkId=id};
  function cancelFetch(){for(const c of aborts)c.abort();aborts.clear()}
  function dispose(item){if(item?.node){item.node.onended=null;try{item.node.stop()}catch{}item.node.disconnect();item.node=null}}
  function halt(){dispose(current);dispose(next)}
  function start(){
   if(!current)return;pending=false;current.start=Math.ceil((context.currentTime+.025)*context.sampleRate)/context.sampleRate;
   current.node=window.ArrifyGapless.schedule(context,current.buffer,gain,current.start,current.offset);
   current.end=current.start+current.buffer.duration-current.offset;
   const item=current;current.node.onended=()=>{if(current!==item||!playing)return;pending=true;fire('ended')};
   scheduleNext();
  }
  function scheduleNext(){
   if(!current?.node||!next||!playing||pending)return;
   dispose(next);if(current.end<=context.currentTime+.015)return;
   next.start=current.end;next.offset=0;next.end=next.start+next.buffer.duration;
   next.node=window.ArrifyGapless.schedule(context,next.buffer,gain,next.start);
   const item=next;next.node.onended=()=>{if(current!==item||!playing)return;pending=true;fire('ended')};
  }
  async function decode(track,profile,run,onController){
   const controller=new AbortController();aborts.add(controller);onController?.(controller);let timer=setTimeout(()=>controller.abort(),90000);
   try{
    const response=await fetch('/api/v1/stream/'+track.id+'?profile=pcm&level='+encodeURIComponent(config.getLevel?.()||'off')+(config.streamQuery?.()||''),{signal:controller.signal,cache:'no-store'});
    if(!response.ok)throw Error('This song needs standard playback.');
    const reader=response.body.getReader(),chunks=[];let bytes=0;
    while(true){const result=await reader.read();if(result.done)break;bytes+=result.value.byteLength;if(bytes>48*1024*1024){controller.abort();throw Error('This song exceeds the gapless preview memory limit.')}chunks.push(result.value)}
    const joined=new Uint8Array(bytes);let at=0;for(const c of chunks){joined.set(c,at);at+=c.length}chunks.length=0;
    if(run!==epoch)throw Object.assign(Error('Replaced'),{name:'AbortError'});
    const buffer=await context.decodeAudioData(joined.buffer);
    if(buffer.length*buffer.numberOfChannels*4>96*1024*1024)throw Error('This song exceeds the gapless preview memory limit.');
    return {track,profile,buffer,offset:0,node:null,levelStatus:response.headers.get('X-Arrify-Leveling')||'off',gainDb:response.headers.get('X-Arrify-Gain-Db')||'0'};
   }finally{clearTimeout(timer);aborts.delete(controller)}
  }
  async function load(track,profile,offset=0,autoplay=true){
   const resume=context.resume(); // Keep browser activation in the user's gesture.
   if(next?.track.id===track.id&&next.profile===profile&&next.node&&pending&&!offset){
    dispose(current);current=next;next=null;pending=false;fake.dataset={trackId:String(track.id),profile:'original',streamOffset:'0'};fake.dataset.levelStatus=current.levelStatus;fake.dataset.gainDb=current.gainDb;fake.readyState=4;fire('loadedmetadata');fire('playing');if(wanted&&wanted.track.id!==track.id)prepare(wanted.track,wanted.profile);return;
   }
   const run=++epoch;++prepareToken;preparing=null;prepareAbort=null;cancelFetch();halt();current=null;next=null;pending=false;loading=true;playing=autoplay;fake.error=null;fake.readyState=0;fake.dataset={trackId:String(track.id),profile:'original',streamOffset:'0'};
   try{await resume;const item=await decode(track,profile,run);if(run!==epoch)throw Object.assign(Error('Replaced'),{name:'AbortError'});current=item;current.offset=Math.min(offset,item.buffer.duration);fake.dataset.levelStatus=item.levelStatus;fake.dataset.gainDb=item.gainDb;fake.readyState=4;loading=false;fire('loadedmetadata');if(playing){start();fire('play');fire('playing')}else suspendIdle();if(wanted)prepare(wanted.track,wanted.profile)}catch(error){if(run===epoch)loading=false;throw error}
  }
  async function prepare(track,profile){
   wanted=track?{track,profile}:null;
   if(preparing===track?.id)return;
   prepareAbort?.abort();prepareAbort=null;const token=++prepareToken;preparing=null;
   if(!track){dispose(next);next=null;return}
   if(next?.track.id===track.id&&next.profile===profile)return;
   dispose(next);next=null;if(loading||!current||!playing||pending)return;
   const run=epoch,desired=track.id;preparing=desired;
   try{const item=await decode(track,profile,run,c=>prepareAbort=c);if(run!==epoch||token!==prepareToken||wanted?.track.id!==desired||!playing)return;next=item;scheduleNext()}catch(error){if(error.name!=='AbortError')config.onNotice?.('Next song will use a standard transition; gapless buffering was unavailable.')}finally{if(token===prepareToken){preparing=null;prepareAbort=null}}
  }
  function suspendIdle(){if(!playing)context.suspend?.().catch(()=>{})}
  function pause(){prepareAbort?.abort();prepareAbort=null;preparing=null;++prepareToken;if(current)current.offset=time();playing=false;halt();pending=false;suspendIdle();fire('pause')}
  async function resume(){await context.resume();if(playing)return;playing=true;if(current)start();fire('play');fire('playing');if(wanted&&!next)prepare(wanted.track,wanted.profile)}
  function stop(){++epoch;++prepareToken;preparing=null;prepareAbort=null;cancelFetch();playing=false;loading=false;halt();current=null;next=null;wanted=null;pending=false;fake.readyState=0;fake.dataset={};suspendIdle();fire('emptied')}
  fake.play=resume;fake.pause=pause;
  setInterval(()=>{if(playing&&current)fire('timeupdate')},200);
  return {load,prepare,pause,resume,stop,cancelPendingStart:()=>{if(loading)stop()},interrupt:()=>{playing=false;halt();suspendIdle()},get active(){return fake},get loading(){return loading},get intent(){return playing},snapshot:()=>({gapless:true,loading,prepared:next?.track.id||null,scheduled:!!next?.node,decodedBytes:(current?.buffer.length||0)*8+(next?.buffer.length||0)*8})};
 }
};

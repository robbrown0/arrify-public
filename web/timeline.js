/* One source of truth for media position, duration, and recovery checkpoints. */
'use strict';
window.ArrifyTimeline={
 duration(audio,technical=0){
  const offset=Number(audio.dataset.streamOffset)||0,native=Number(audio.duration);
  const original=(audio.dataset.profile||'original')==='original';
  const total=original?(Number.isFinite(native)&&native>0?native:technical):(technical>0?technical:Number.isFinite(native)&&native>0?offset+native:0);
  const position=Math.max(0,(Number.isFinite(audio.currentTime)?audio.currentTime:0)+offset);
  // An invalid estimate must not pin a still-playing song at 100%.
  return Number.isFinite(total)&&total>0&&(audio.ended||position<=total+.5)?total:0;
 },
 create({onReset=()=>{}}={}){
  let track=null,checkpoint=0,last=0,pending=null,reported=false;
  return{
   begin(id,position=0){track=String(id);checkpoint=Math.max(0,position);last=checkpoint;pending=null;reported=false},
   seek(position){checkpoint=Math.max(0,position);last=checkpoint;pending=checkpoint;reported=false},
   settled(position){if(pending!==null&&Math.abs(position-pending)<1){checkpoint=Math.max(0,position);last=checkpoint;pending=null}},
   observe(audio,loading=false){
    if(String(audio.dataset.trackId)!==track||loading||audio.error||pending!==null)return;
    const position=Math.max(0,(Number.isFinite(audio.currentTime)?audio.currentTime:0)+(Number(audio.dataset.streamOffset)||0));
    if(position<last-2){if(!reported){reported=true;onReset({position,checkpoint})}return}
    last=position;checkpoint=Math.max(checkpoint,position);
   },
   recovery(audio){
    if(String(audio.dataset.trackId)!==track)return 0;
    if(pending!==null)return pending;
    const position=(Number.isFinite(audio.currentTime)?audio.currentTime:0)+(Number(audio.dataset.streamOffset)||0);
    return Math.max(checkpoint,position,0);
   },
   snapshot:()=>({track,checkpoint,pending})
  };
 }
};

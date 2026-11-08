/* Read-only device telemetry. Existing player heartbeats drive sampling. */
'use strict';
window.ArrifyBufferStatus=(()=>{
 let latest=null,sampled=0,busy=false;
 const mb=n=>(Math.max(0,Number(n)||0)/1048576).toFixed(1)+' MB';
 const failureLabel=s=>({'unsupported-response':'Audio response cannot be cached','representation-changed':'Audio changed during download','storage-incomplete':'Incomplete device file','buffer-limit':'Cache limit reached','download-failed':'Download did not complete'}[s]||'');
 const label=s=>({ready:'Ready',downloading:'Downloading',retrying:'Waiting to retry',partial:'Partial',waiting:'Preparing',queued:'Queued',oversized:'Exceeds per-song cache limit',budget:'Cache budget reached','storage-full':'Device storage unavailable'}[s]||'Unknown');
 function markup(report){
  if(!report)return '<p class="muted">Buffer details haven’t been reported yet.</p>';
  const rows=report.items||[];
  return '<div class="buffer-overview"><strong>'+mb(report.downloaded_bytes)+'<small> / '+mb(report.limit_bytes)+'</small></strong><span>Downloaded audio</span></div><div class="buffer-tracks">'+rows.map((x,i)=>'<div><span>'+(i===0?'Current song':'Next '+i)+'</span><span>'+label(x.state)+(x.failure_reason?' · '+failureLabel(x.failure_reason):'')+(x.total_bytes&&x.state!=='oversized'?' · '+Math.min(100,Math.floor(x.loaded_bytes/x.total_bytes*100))+'%':'')+'</span><progress max="'+(x.total_bytes||1)+'" value="'+(x.loaded_bytes||0)+'" aria-label="'+(i===0?'Current song':'Next '+i)+' downloaded"></progress></div>').join('')+'</div><p class="buffer-caption">'+(report.state==='inactive'?'No active read-ahead. ':report.storage==='memory'?'Limited memory cache; larger device storage is unavailable. ':'')+'Player buffer: '+Math.round(report.player_seconds||0)+' seconds ahead. Downloaded audio and the player’s playable buffer are separate.</p>';
 }
 function report(audio,current){
  if(!latest||Date.now()-sampled>15000||latest.items?.length&&latest.items[0].id!==current)return null;
  let seconds=0;try{for(let i=0;i<audio.buffered.length;i++)if(audio.buffered.start(i)<=audio.currentTime&&audio.buffered.end(i)>=audio.currentTime){seconds=audio.buffered.end(i)-audio.currentTime;break}}catch{}
  return {...latest,player_seconds:Math.max(0,Math.min(86400,seconds)),sample_age_seconds:Math.floor((Date.now()-sampled)/1000)};
 }
 function paint(audio,current){const node=document.querySelector('#sound-buffer-details');if(node&&document.querySelector('#sound-dialog')?.open)node.innerHTML=markup(report(audio,current));}
 function refresh(audio,current){
  paint(audio,current);if(busy)return;
  const worker=navigator.serviceWorker?.controller;if(!worker){latest=null;paint(audio,current);return}
  busy=true;const channel=new MessageChannel();
  const finish=()=>{clearTimeout(timer);channel.port1.close();channel.port2.close();busy=false};
  const timer=setTimeout(()=>{finish();paint(audio,current)},1200);
  channel.port1.onmessage=e=>{latest=e.data;sampled=Date.now();finish();paint(audio,current)};
  try{worker.postMessage({type:'device-buffer-status'},[channel.port2])}catch{finish()}
 }
 return {refresh,report,markup,mb};
})();

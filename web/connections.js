/* Read-only admin activity; existing heartbeats supply presence information. */
'use strict';
function connectionsMarkup(d){
 const labels={playing:'Playing',paused:'Paused',buffering:'Buffering',interrupted:'Interrupted','needs-gesture':'Tap to resume',offline:'Offline'};
 const age=at=>{const seconds=Math.max(0,Math.floor(d.server_time-at));return seconds<20?'just now':seconds<60?seconds+'s ago':seconds<3600?Math.floor(seconds/60)+'m ago':Math.floor(seconds/3600)+'h ago'};
 const renderUser=u=>{
  const id=Number(u.id),name=u.display_name||u.username,b=u.buffer,ready=b?.items?.slice(1).filter(x=>x.state==='ready').length||0,retrying=b?.items?.some(x=>x.state==='retrying');
  const cache=b?(b.state==='inactive'?'Read-ahead inactive':window.ArrifyBufferStatus.mb(b.downloaded_bytes)+' downloaded · '+(retrying?'Retrying':ready+' of '+Math.max(0,b.items.length-1)+' next ready')):'Buffer not reported';
  return '<article class="connection-card" data-connection-user="'+id+'"><details class="connection-usage"><summary id="connection-summary-'+id+'" aria-label="Playback details for '+esc(name)+'"><div class="connection-identity"><img src="/api/v1/avatar/'+id+'" alt="" loading="lazy"><div><h3>'+esc(name)+'</h3><p>'+esc(u.device||u.username)+'</p></div></div><div class="connection-now"><p class="connection-track"><strong>'+esc(u.track?.title||'No song loaded')+'</strong><span>'+esc(u.track?.artist||'')+'</span></p><p class="connection-cache">'+esc(cache)+'</p></div><div class="connection-state"><span class="connection-presence '+(u.playback==='playing'?'online':'')+'"><i aria-hidden="true"></i>'+esc(labels[u.playback]||'Offline')+'</span><small>'+(b&&u.buffer_at?'Reported '+age(u.buffer_at):'Seen '+age(u.player_seen||u.last_seen))+'</small></div><span class="admin-chevron" aria-hidden="true">⌄</span></summary><div class="connection-detail-panel"><div class="connection-buffer-detail"><h4>Device buffer</h4>'+window.ArrifyBufferStatus.markup(b)+(b&&u.buffer_at?'<p class="buffer-caption">Last reported '+esc(adminDate(u.buffer_at))+(d.server_time-u.buffer_at>=20?' · Saved observation; device may be asleep or disconnected.':'')+'</p>':'')+'</div><dl class="connection-stats">'+[['Audio requests',u.audio_requests],['Saved sessions',u.sessions],['Tracks · 24h',u.tracks_started_24h],['Tracks · total',u.tracks_started],['Requests',u.requests],['Queued',u.queue_count]].map(([name,value])=>'<div><dt>'+esc(name)+'</dt><dd>'+Number(value||0).toLocaleString()+'</dd></div>').join('')+'</dl><p class="muted">'+esc([u.quality,u.output].filter(Boolean).join(' · '))+'</p><p class="muted">Last seen '+esc(adminDate(u.last_seen))+' · Signed in '+esc(adminDate(u.last_login))+'</p></div></details></article>';
 };
 const active=d.items.filter(u=>u.playback==='playing'||u.playback==='buffering'||u.audio_requests>0),idle=d.items.filter(u=>!active.includes(u));
 const metrics=[['Playing',d.summary.playing],['Buffering',d.summary.buffering],['Transfers',d.summary.audio_requests],['Connected',d.summary.online]];
 return '<section id="admin-connections"><div class="section-head"><div><span class="eyebrow">Listening activity</span><h2>Active streams</h2></div><button class="chip" id="connections-refresh">Refresh</button></div><div class="connections-summary stream-metrics">'+metrics.map(([name,value])=>'<div><strong>'+Number(value).toLocaleString()+'</strong><span>'+esc(name)+'</span></div>').join('')+'</div><p class="connections-updated muted" role="status">Updated '+esc(new Date(d.server_time*1000).toLocaleTimeString())+'</p><div class="connections-list">'+(active.length?active.map(renderUser).join(''):empty('Nothing playing right now.','Listeners appear here when playback starts.'))+(idle.length?'<details class="connections-idle"><summary id="connections-idle-summary">Other signed-in accounts <span class="admin-count">'+idle.length+'</span></summary>'+idle.map(renderUser).join('')+'</details>':'')+'</div><details class="connection-help admin-info"><summary id="connections-help-summary">About this view</summary><p>One playback owner is shown per account. Buffer observations come from that device’s existing heartbeats; other saved sessions are not separate live cache reports. Downloaded audio includes partial and complete files for the current plan, not the server cache. Player buffer means playable seconds ahead. Sleeping devices retain their last report. Transfers include preparation and prefetch work. This view refreshes every 10 seconds while visible.</p></details></section>';
}
let connectionsRefreshing=false;
async function refreshConnections(){
 if(!me?.admin||document.hidden||location.hash!=='#admin/connected'||connectionsRefreshing)return;
 connectionsRefreshing=true;
 try{
  const d=await api('/admin/connections');
  if(location.hash!=='#admin/connected'||!me?.admin)return;
  const section=document.querySelector('#admin-connections');if(!section)return;
  // Keep disclosures, keyboard focus and scroll stable during live updates.
  const focused=section.contains(document.activeElement)?document.activeElement.id:null;
  const expanded=[...section.querySelectorAll('details[open]>summary[id]')].map(x=>x.id);
  const scroll=document.querySelector('#content'),top=scroll?.scrollTop,pageTop=window.scrollY;
  section.outerHTML=connectionsMarkup(d);
  for(const id of expanded)document.getElementById(id)?.parentElement.setAttribute('open','');
  if(focused)document.getElementById(focused)?.focus({preventScroll:true});
  if(scroll)scroll.scrollTop=top;window.scrollTo(0,pageTop);
 }finally{connectionsRefreshing=false}
}
document.addEventListener('click',e=>{if(e.target.closest('#connections-refresh'))safe(refreshConnections)});
setInterval(()=>{if(location.hash==='#admin/connected'&&!document.hidden)safe(refreshConnections)},10000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&location.hash==='#admin/connected')safe(refreshConnections)});

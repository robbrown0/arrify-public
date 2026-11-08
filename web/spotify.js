/* Spotify transport shares Arrify's player presentation and authoritative party queue. */
'use strict';
window.ArrifySpotify=(()=>{
 let cfg=null,player=null,loading=null,recovering=null,sdkDevice='',sdkOwner=null,party=null,selected=null,viewSelected=false,active=false,busy=false,applying=false,advancing=false,reporting=false,polling=false,sequence=0,generation=0,appliedKey='',blockedKey='',confirmed=false,lastError='',snapshot=null,observedAt=0,observer=null;
 // Keep bounded, account-scoped provider diagnostics; never retain SDK messages,
 // tokens, device IDs, track metadata or URLs. No playback work depends on uploads.
 let diagnosticOwner=null,diagnosticEvents=[],diagnosticLastUpload=-Infinity,diagnosticTimer=null,diagnosticBusy=false,diagnosticState='',diagnosticLastState=0,diagnosticUploads=[],diagnosticDue=Infinity;
 function diagnosticReason(error){
  const message=String(error?.message||error?.name||'').slice(0,2048).toLowerCase();
  if(/license|fairplay|drm|key.?system|encrypted/.test(message))return'protection';
  if(/autoplay|notallowed|user.?gesture/.test(message))return'autoplay';
  if(/decode|decoding/.test(message))return'decode';
  if(/not.?supported|unsupported/.test(message))return'unsupported';
  if(/network|fetch|connection/.test(message))return'network';
  if(/timeout|timed out/.test(message))return'timeout';
  if(/device.*(unavailable|disconnect|not found)/.test(message))return'device-unavailable';
  if(/authoriz|token|authentication/.test(message))return'authorization';
  if(/abort|interrupt/.test(message))return'interrupted';
  if(/playback|could not play/.test(message))return'playback';
  return'unknown';
 }
 function diagnostic(event,error){
  try{
   const uid=user()?.id;if(!uid)return;
   if(diagnosticOwner!==uid){clearTimeout(diagnosticTimer);diagnosticTimer=null;diagnosticOwner=uid;diagnosticEvents=[];diagnosticLastUpload=-Infinity;diagnosticState='';diagnosticLastState=0;diagnosticUploads=[];diagnosticDue=Infinity}
   const item={event,wall_ms:Date.now(),hidden:document.hidden,audio_session_state:navigator.audioSession?.state||''};
   if(snapshot){item.paused=!!snapshot.paused;item.position=Math.max(0,Number(snapshot.position)||0)/1000;item.duration=Math.max(0,Number(snapshot.duration)||0)/1000}
   if(error){item.reason=diagnosticReason(error);if(Number.isInteger(error.status)&&error.status>=100&&error.status<=599)item.http_status=error.status}
   diagnosticEvents.push(item);if(diagnosticEvents.length>40)diagnosticEvents.shift();
   const now=Date.now();diagnosticUploads=diagnosticUploads.filter(t=>now-t<900000);
   if(diagnosticUploads.length>=7)return;
   const due=Math.max(now,diagnosticLastUpload+(event==='spotify-state'?180000:5000));
   if(due<diagnosticDue){clearTimeout(diagnosticTimer);diagnosticDue=due;diagnosticTimer=setTimeout(flushDiagnostics,due-now)}
  }catch{}
 }
 async function flushDiagnostics(){
  diagnosticTimer=null;diagnosticDue=Infinity;const uid=diagnosticOwner;
  if(!uid||user()?.id!==uid||!diagnosticEvents.length||diagnosticBusy)return;
  diagnosticUploads=diagnosticUploads.filter(t=>Date.now()-t<900000);if(diagnosticUploads.length>=7)return;
  diagnosticBusy=true;diagnosticLastUpload=Date.now();diagnosticUploads.push(diagnosticLastUpload);
  try{await cfg.api('/player/diagnostics',{source:'spotify',events:diagnosticEvents.slice(-40)})}catch{}finally{diagnosticBusy=false}
 }
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const user=()=>cfg?.getUser?.(),device=()=>cfg.getDevice(),config=()=>cfg.api('/spotify');
 const displayed=()=>selected?(active&&party?.id===selected.id?party:selected):(active&&!viewSelected?party:null);
 const owns=(p=party)=>!!(active&&p?.spotify_playback?.party_id===p.id&&p.spotify_playback.device_id===device().id);
 const current=(p=party)=>p?.items.find(t=>t.entry_id===p.spotify_playback?.entry_id);
 const displayTrack=()=>{const p=displayed();return current(p)||p?.items.find(t=>t.status==='playing')||p?.items.find(t=>t.status==='queued')};
 const local=()=>!!(displayed()&&party?.id===displayed().id&&owns());
 const notice=message=>{lastError=message;const status=document.querySelector('#spotify-party-status');if(status)status.textContent=message;else if(message)cfg?.toast(message)};
 const artwork=t=>{const url=String(t?.artwork||'');return /^https:\/\/[^/?#@]+\.scdn\.co\//i.test(url)?url:''};
 const fmt=n=>{n=Math.max(0,Math.floor(Number(n)||0));return Math.floor(n/60)+':'+String(n%60).padStart(2,'0')};
 function position(){return Math.max(0,(snapshot?.position||0)/1000+(snapshot&&!snapshot.paused?(performance.now()-observedAt)/1000:0))}
 const playing=()=>!!(local()&&snapshot&&!snapshot.paused&&snapshot.track_window?.current_track?.id===current()?.id);
 async function connect({preserveDraft=false}={}){if(!preserveDraft)try{sessionStorage.removeItem('arrify:spotify:party-draft')}catch{}const result=await cfg.api('/spotify',{action:'connect'});const url=new URL(result.url);if(url.origin!=='https://accounts.spotify.com'||url.pathname!=='/authorize')throw Error('Spotify returned an invalid sign-in address.');if(new URL(url.searchParams.get('redirect_uri')).origin!==location.origin)throw Error('Open Arrify at its configured HTTPS address to connect Spotify.');location.assign(url.href)}
 async function renderProfile(){
  if(!cfg||!user())return;const uid=user().id,settings=await config();if(user()?.id!==uid)return;
  const root=document.querySelector('#music-services-list')||document.querySelector('#profile-dialog .profile-bottom')?.parentElement;if(!root)return;let node=document.querySelector('#spotify-profile');if(!node){node=document.createElement('section');node.id='spotify-profile';node.className='profile-section';root.append(node)}
  node.innerHTML=window.ArrifyPlayer.musicServiceHeading('spotify','Spotify',settings.linked)+'<p class="profile-help">'+(!settings.available?'Setup is pending. Ask an administrator to finish connecting Spotify.':settings.linked?'Connected'+(settings.display_name?' as '+esc(settings.display_name):'')+'. Spotify Premium is required for playback.':'Connect through Spotify to host a Spotify PartyQueue. Premium is required for playback.')+'</p><div class="party-buttons"><button type="button" class="chip" data-spotify-connect '+(!settings.available||settings.linked?'disabled':'')+'>'+(settings.linked?'Spotify connected':'Connect Spotify')+'</button>'+(settings.linked?'<button type="button" class="text-button" data-spotify-disconnect>Disconnect</button>':'')+'</div><p data-spotify-profile-status class="profile-help" role="status"></p>';
 }
 async function load(){
  if(player&&sdkOwner!==user()?.id){player.disconnect();player=null;sdkDevice='';snapshot=null;active=false}
  if(player&&sdkDevice)return player;if(loading)return loading;const uid=user()?.id;
  loading=(async()=>{
   const settings=await config();if(!settings.available)throw Error('Spotify setup is not complete.');if(!settings.linked)throw Error('Connect Spotify in Music services first.');if(settings.premium===false)throw Error('Spotify playback requires Premium.');
   if(!window.Spotify?.Player)await new Promise((resolve,reject)=>{const old=window.onSpotifyWebPlaybackSDKReady;let timer;const script=document.createElement('script');const fail=()=>{clearTimeout(timer);script.remove();reject(Error('Spotify playback could not load. Check your connection and try again.'))};window.onSpotifyWebPlaybackSDKReady=()=>{clearTimeout(timer);try{old?.()}finally{resolve()}};script.src='https://sdk.scdn.co/spotify-player.js';script.async=true;script.onerror=fail;timer=setTimeout(fail,20000);document.head.append(script)});
   if(user()?.id!==uid)throw Error('Your Arrify account changed. Connect again.');sdkOwner=uid;
   if(player){const old=player;player=null;old.disconnect()}
   player=new window.Spotify.Player({name:'Arrify PartyQueue',volume:1,getOAuthToken:callback=>{if(user()?.id!==uid){callback('');return}cfg.api('/spotify/token').then(d=>{if(user()?.id===uid)callback(d.access_token);else callback('')}).catch(()=>{callback('');notice('Reconnect Spotify in Music services.')})}});
   const instance=player,currentInstance=()=>player===instance&&sdkOwner===uid&&user()?.id===uid;
   player.addListener('player_state_changed',state=>{if(currentInstance())onState(state).catch(()=>{})});player.addListener('not_ready',()=>{if(!currentInstance())return;diagnostic('spotify-not-ready');return recoverDevice().catch(()=>{})});
   for(const event of ['initialization_error','authentication_error','account_error','playback_error','autoplay_failed'])player.addListener(event,error=>{if(!currentInstance())return;diagnostic('spotify-'+event.replaceAll('_','-'),error);notice(event==='account_error'?'Spotify playback requires Premium and access to this developer app.':event==='authentication_error'?'Reconnect Spotify in Music services.':event==='autoplay_failed'?'Tap Play again to start Spotify on this device.':'Spotify could not play this song. Retry or use Next.');report('error',event==='account_error'?'subscription':'playback').catch(()=>{})});
   await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Spotify playback device did not become ready. Try again.')),20000);player.addListener('ready',({device_id})=>{if(!currentInstance()){instance.disconnect();clearTimeout(timer);reject(Error('Your Arrify account changed.'));return}sdkDevice=device_id;diagnostic('spotify-ready');clearTimeout(timer);resolve()});player.connect().then(ok=>{if(!ok){clearTimeout(timer);reject(Error('Spotify playback could not connect.'))}}).catch(error=>{clearTimeout(timer);reject(error)})});return player;
  })().catch(error=>{try{player?.disconnect()}catch{}player=null;sdkDevice='';throw error}).finally(()=>loading=null);return loading;
 }
 async function recoverDevice(){
  if(recovering)return recovering;const uid=user()?.id;
  recovering=(async()=>{await stop(true);if(user()?.id!==uid)return;const old=player;player=null;sdkDevice='';sdkOwner=null;old?.disconnect();notice('Reconnecting Spotify on this device…');paint();await load();if(user()?.id===uid){notice('Spotify reconnected. Tap Play to continue this party.');paint()}})().catch(error=>{if(user()?.id===uid){notice(error.message);paint()}}).finally(()=>recovering=null);return recovering;
 }
 async function playback(action,extra={}){const d=party.spotify_playback;try{return await cfg.api('/spotify/player',{party_id:party.id,device_id:device().id,epoch:d.epoch,revision:d.revision,entry_id:d.entry_id,action,...extra})}catch(error){diagnostic('spotify-api-error',error);if(error.message==='Spotify playback device disconnected. Tap Play to reconnect.'){await recoverDevice();throw Error(lastError||'Tap Play to reconnect Spotify on this device.')}throw error}}

 async function report(observed=snapshot&&!snapshot.paused&&snapshot.track_window?.current_track?.id===current()?.id?'playing':snapshot?.paused?'paused':'buffering',error=''){
  if(!owns()||reporting||applying||!party||!user())return;reporting=true;const p=party,d=p.spotify_playback,uid=user().id;
  try{const value=await cfg.api('/parties/'+p.id+'/spotify-playback',{action:'heartbeat',device_id:device().id,epoch:d.epoch,revision:d.revision,entry_id:d.entry_id,sequence:++sequence,observed,error,position:Math.min(86400,position()),duration:Math.min(86400,(snapshot?.duration||0)/1000)});if(user()?.id===uid&&party?.id===p.id&&party.spotify_playback?.epoch===d.epoch&&(value.spotify_playback?.revision||0)>=(party.spotify_playback?.revision||0)){party=value._partial?{...party,...value}:value;paint()}}
  catch(error){if(error.status===409)await refresh();else if([401,403,404,410].includes(error.status)){await stop(false);notice('Spotify authorization ended. Reconnect in Music services.')}else throw error}finally{reporting=false}
 }
 async function onState(value){
  if(!value||!owns()||applying||advancing)return;const diagState=String(value.paused)+':'+String(value.track_window?.current_track?.id===current()?.id);if(diagState!==diagnosticState||Date.now()-diagnosticLastState>=30000){diagnosticState=diagState;diagnosticLastState=Date.now();const previous=snapshot;snapshot=value;diagnostic('spotify-state');snapshot=previous}const t=current(),id=value.track_window?.current_track?.id,oldPosition=position(),oldDuration=(snapshot?.duration||0)/1000;
  const completed=confirmed&&!party.spotify_playback.user_paused&&oldDuration>0&&oldPosition>=oldDuration-.75&&(value.paused||id!==t?.id)&&(id!==t?.id||value.position===0||value.position>=value.duration-750);
  if(completed){if(id!==t?.id)await player.pause().catch(()=>{});await next(true);return}
  if(t&&id!==t.id){if(!value.paused)await player.pause().catch(()=>{});appliedKey='';blockedKey=party.id+':'+party.spotify_playback.epoch+':'+party.spotify_playback.entry_id;notice('Spotify changed songs outside this party. Tap Play to resume the party queue.');await report('paused');return}
  snapshot=value;observedAt=performance.now();if(!value.paused&&id===t?.id)confirmed=true;await report(value.paused?'paused':'playing').catch(()=>{});paint();
 }
 async function apply(p){
  if(active&&party?.id===p.id&&!p.ended){const old=party.spotify_playback,next=p.spotify_playback;if(old&&next&&(next.epoch<old.epoch||next.epoch===old.epoch&&next.revision<old.revision))return}
  if(selected?.id===p.id)selected=p.ended?null:p;
  if(p.source!=='spotify'||p.ended){if(active&&party?.id===p.id)await stop(false);paint();return}
  if(active&&party?.id!==p.id){paint();return}party=p;const d=p.spotify_playback;
  if(!d||d.party_id!==p.id||d.device_id!==device().id){if(active)await stop(false);else paint();return}if(!active){paint();return}
  const t=current(),key=p.id+':'+d.epoch+':'+d.entry_id;if(!t){confirmed=false;appliedKey='';blockedKey='';snapshot=null;await player?.pause().catch(()=>{});paint();return}if(applying||key===blockedKey){paint();return}
  if(key!==appliedKey){applying=true;const ticket=++generation;sequence=0;confirmed=false;snapshot=null;
   try{if(d.desired_playing){await playback('play',{position:d.position||0});if(ticket!==generation||!active)return;appliedKey=key;lastError='';snapshot=await player.getCurrentState();observedAt=performance.now();if(snapshot&&!snapshot.paused&&snapshot.track_window?.current_track?.id===t.id)confirmed=true}else await player.pause()}
   catch(error){if(ticket===generation){appliedKey='';blockedKey=key;notice(error.message||'Spotify could not start. Tap Play to retry.')}}finally{applying=false;if(ticket===generation&&active){const latest=party.spotify_playback;if(party.id+':'+latest.epoch+':'+latest.entry_id!==key)await apply(party);else if(appliedKey)await report().catch(()=>{});paint()}}
  }else if(!d.desired_playing&&snapshot&&!snapshot.paused){await player.pause();snapshot=await player.getCurrentState();observedAt=performance.now();paint()}
 }
 async function startParty(p,takeover=false){
  if(p.source!=='spotify')throw Error('Choose a Spotify party.');player?.activateElement?.();await load();await player.activateElement?.();
  const value=await cfg.api('/parties/'+p.id+'/spotify-playback',{action:'start',device_id:device().id,device_name:device().name,spotify_device_id:sdkDevice,takeover});await window.ArrifyApple.stop(false);party=value;active=true;cfg.pauseLocalPlayback();appliedKey='';blockedKey='';lastError='';await apply(value);return party;
 }
 async function toggle(p=displayed()){
  player?.activateElement?.();if(busy||advancing||!p)return;busy=true;paint();try{if(!owns()||p.id!==party?.id)return await startParty(p,!!p.spotify_playback?.online);const d=party.spotify_playback,wantsPlay=!snapshot||snapshot.paused||d.user_paused;party=await cfg.api('/parties/'+p.id+'/spotify-playback',{action:wantsPlay?'resume':'pause',device_id:device().id,epoch:d.epoch});if(wantsPlay){blockedKey='';if(!appliedKey)await apply(party);else await playback('play',{position:position()})}else await player.pause();snapshot=await player.getCurrentState();observedAt=performance.now();await report(snapshot?.paused?'paused':'playing');return party}finally{busy=false;paint()}
 }
 async function next(automatic=false){return transition('next',automatic)}
 async function previous(){return transition('previous',false)}
 async function transition(action,automatic){if(!owns()||advancing||applying||!party.spotify_playback.entry_id)return;advancing=true;const p=party,d=p.spotify_playback;try{party=await cfg.api('/parties/'+p.id+'/spotify-playback',{action,automatic,device_id:device().id,epoch:d.epoch,revision:d.revision,entry_id:d.entry_id});lastError='';await apply(party);return party}finally{advancing=false;paint()}}
 async function seek(value){if(!owns()||busy||applying||advancing||!Number.isFinite(value))return;await playback('seek',{position:Math.max(0,Math.min((snapshot?.duration||0)/1000,value))});snapshot=await player.getCurrentState();observedAt=performance.now();await report();paintClock()}
 async function stop(notify=true){if(active&&notify&&owns()){const p=party,d=p.spotify_playback;try{party=await cfg.api('/parties/'+p.id+'/spotify-playback',{action:'pause',device_id:device().id,epoch:d.epoch})}catch{}}active=false;generation++;confirmed=false;appliedKey='';blockedKey='';try{await player?.pause()}catch{}snapshot=null;cfg?.restoreLocalSession?.();paint()}
 async function signOut(){clearTimeout(diagnosticTimer);diagnosticTimer=null;diagnosticOwner=null;diagnosticEvents=[];try{sessionStorage.removeItem('arrify:spotify:party-draft')}catch{}selected=null;await stop(true);player?.disconnect();player=null;sdkDevice='';sdkOwner=null;paint()}
 async function disconnect(){await signOut();await cfg.api('/spotify',{action:'disconnect'});await renderProfile()}
 async function refresh(){if(!active||!party||polling||!user())return;polling=true;try{const p=await cfg.api('/parties/'+party.id);await apply(p)}catch(error){if([401,403,404,410].includes(error.status))await stop(false)}finally{polling=false}}
 function setPartyView(p){const previous=selected?.id;viewSelected=true;selected=p?.source==='spotify'&&!p.ended?p:null;if(selected&&selected.id!==previous&&cfg?.getUser?.()){lastError='';load().then(paint).catch(error=>{notice(error.message);paint()})}paint()}
 function statusText(p){if(lastError&&(!active||p?.id===party?.id))return lastError;if(owns(p))return lastError||(!current(p)?'Waiting for requests.':playing()?'Playing on this device. Choose AirPlay speakers in Control Center.':'Paused or waiting for playback. Tap Play to continue.');if(loading)return 'Preparing Spotify on this device…';if(player&&sdkDevice)return 'Ready to play. Tap Play party. Choose AirPlay speakers in Control Center.';return p?.spotify_playback?.online?'Playback is on '+(p.spotify_playback.device_name||'another device')+'. Choose Play here to move it.':'Connect Spotify in Music services, then choose Play party.'}
 function paintClock(){const t=displayTrack(),own=local(),duration=t?(own?(snapshot?.duration||t.duration*1000||0)/1000:Number(t.duration)||0):0,pos=own?Math.min(duration,position()):0;for(const range of (document.hidden?[]:document.querySelectorAll('[data-spotify-seek]'))){const bar=range.closest('[data-spotify-clock]')||range.closest('#spotify-mini');range.max=duration||1;if(document.activeElement!==range)range.value=pos;range.disabled=!own||!duration||applying||advancing;bar.querySelector('[data-spotify-position]').textContent=fmt(pos);bar.querySelector('[data-spotify-duration]').textContent=fmt(duration)}const progress=document.hidden?null:document.querySelector('#spotify-mini .apple-mini-progress');progress?.style.setProperty('--progress',(duration?pos/duration*100:0)+'%');if(own&&navigator.mediaSession?.setPositionState&&duration)try{navigator.mediaSession.setPositionState({duration,playbackRate:1,position:pos})}catch{}}
 function bindMedia(){if(!owns()||!navigator.mediaSession)return;const t=current();if(!t)navigator.mediaSession.metadata=null;if(t&&typeof MediaMetadata!=='undefined')navigator.mediaSession.metadata=new MediaMetadata({title:t.title,artist:t.artist,album:t.album,artwork:[{src:artwork(t)||location.origin+'/assets/icon-512.png'}]});for(const [action,fn]of Object.entries({play:()=>{if(!snapshot||snapshot.paused)return toggle(party)},pause:()=>{if(snapshot&&!snapshot.paused)return toggle(party)},previoustrack:previous,nexttrack:()=>next(false),seekbackward:null,seekforward:null,seekto:d=>seek(d.seekTime)}))try{navigator.mediaSession.setActionHandler(action,fn?d=>Promise.resolve(fn(d||{})).catch(()=>notice('Try that control again in Arrify.')):null)}catch{}navigator.mediaSession.playbackState=snapshot&&!snapshot.paused?'playing':'paused'}
 function paint(){
  const p=displayed(),mini=document.querySelector('#spotify-mini');document.body.classList.toggle('apple-party-playing',!!p||!!window.ArrifyApple?.hasView?.());if(!p){observer?.disconnect();observer=null;mini?.remove();if(!window.ArrifyApple?.hasView?.())document.documentElement.style.removeProperty('--apple-dock-clearance');document.querySelector('#spotify-full-player')?.close();return}
  const t=displayTrack(),own=local(),bar=mini||document.createElement('div'),key=JSON.stringify([p.id,t,own,playing(),busy,applying,advancing,!!loading]);bar.id='spotify-mini';bar.className='apple-mini';if(bar._key!==key){bar._key=key;bar.innerHTML=window.ArrifyPlayer.externalMiniHTML('spotify','Spotify',{track:t,art:artwork(t),local:own,playing:playing(),busy:busy||applying||advancing||!!loading});const content=bar.querySelector('.apple-mini-track'),wrapper=document.createElement('div');wrapper.className='apple-mini-track spotify-mini-track';wrapper.innerHTML=content.innerHTML;content.replaceWith(wrapper);const expand=document.createElement('button');expand.type='button';expand.className='service-mini-expand';expand.setAttribute('data-spotify-expand','');expand.setAttribute('aria-label','Open Spotify player');expand.setAttribute('aria-haspopup','dialog');wrapper.prepend(expand);const source=wrapper.querySelector('small');if(t){const link=document.createElement('a');link.href='https://open.spotify.com/track/'+t.id;link.target='_blank';link.rel='noopener noreferrer';link.className='spotify-attribution';link.textContent='Spotify';source.replaceWith(link)}}if(!mini){document.body.append(bar);if(typeof ResizeObserver==='function'){observer=new ResizeObserver(()=>{document.documentElement.style.setProperty('--apple-dock-clearance',Math.ceil(bar.getBoundingClientRect().height+(parseFloat(getComputedStyle(bar).bottom)||0)+(parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--visual-bottom-gap'))||0)+20)+'px')});observer.observe(bar)}}
  document.documentElement.style.setProperty('--apple-dock-clearance',Math.ceil(bar.getBoundingClientRect().height+(parseFloat(getComputedStyle(bar).bottom)||0)+(parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--visual-bottom-gap'))||0)+20)+'px');const d=document.querySelector('#spotify-full-player');window.ArrifyPlayer.renderExternalDialog('spotify','Spotify',{view:p,track:t,artwork,local:own,playing:playing(),busy:busy||applying||advancing,status:lastError});if(d?.open){const badge=d.querySelector('.np-header .eyebrow');if(badge)badge.textContent='NOW PLAYING · SPOTIFY';let source=d.querySelector('.spotify-source-link');if(t&&!source){source=document.createElement('a');source.className='spotify-source-link';source.textContent='Open in Spotify';source.target='_blank';source.rel='noopener noreferrer';d.querySelector('.np-status-bar').append(source)}if(source){source.hidden=!t;if(t)source.href='https://open.spotify.com/track/'+t.id}window.ArrifyPlayer.fitLayout(d)}bindMedia();paintClock();const status=document.querySelector('#spotify-party-status');if(status)status.textContent=statusText(p);const hostPlay=document.querySelector('[data-party-id="'+p.id+'"] [data-party-play]');if(hostPlay)hostPlay.disabled=!p.items.length||!!loading||busy||applying;
 }
 function tab(queue){const d=document.querySelector('#spotify-full-player');window.ArrifyPlayer.setQueueSheet(d,queue);d.querySelector('[data-spotify-panel=listen]').hidden=false;d.querySelector('[data-spotify-panel=queue]').hidden=!queue;d.querySelector('.np-view-nav').hidden=!queue;if(queue)d.querySelector('[data-spotify-back]').focus();paint()}
 function openOutput(){diagnostic('spotify-output-request');cfg.toast('Choose AirPlay speakers in Control Center’s Now Playing panel.');const n=document.querySelector('#spotify-full-player [data-spotify-full-status]');if(n){n.textContent='Choose AirPlay speakers in Control Center’s Now Playing panel.';n.hidden=false}}
 document.addEventListener('click',event=>{const b=event.target.closest('button');if(!b||b.disabled||!cfg)return;if(b.hasAttribute('data-spotify-connect')){connect().catch(error=>notice(error.message));return}if(b.hasAttribute('data-spotify-disconnect')){b.disabled=true;disconnect().catch(error=>notice(error.message)).finally(()=>{if(b.isConnected)b.disabled=false});return}if(b.hasAttribute('data-spotify-expand')){const d=window.ArrifyPlayer.createExternalDialog('spotify','Spotify');d.showModal();document.body.classList.add('apple-player-expanded');tab(false);return}if(b.hasAttribute('data-spotify-collapse'))return document.querySelector('#spotify-full-player')?.close();if(b.hasAttribute('data-spotify-back'))return tab(false);if(b.hasAttribute('data-spotify-queue'))return tab(true);if(b.hasAttribute('data-spotify-party-link')){document.querySelector('#spotify-full-player')?.close();location.hash='party/'+displayed().id;return}if(b.hasAttribute('data-spotify-output'))return openOutput();if(b.hasAttribute('data-spotify-toggle'))toggle(displayed()).catch(error=>notice(error.message));if(b.hasAttribute('data-spotify-next'))next(false).catch(error=>notice(error.message));if(b.hasAttribute('data-spotify-previous'))previous().catch(error=>notice(error.message))});
 document.addEventListener('change',event=>{if(event.target.matches('[data-spotify-seek]'))seek(Number(event.target.value)).catch(error=>notice(error.message))});
 let heartbeatRunning=false;
 async function heartbeat(){
  if(!active)return;
  // A slow party HTTP refresh must not stall SDK end-of-track observation.
  refresh().catch(()=>{});
  if(heartbeatRunning)return;heartbeatRunning=true;let timer;
  try{
   const state=await Promise.race([Promise.resolve(player?.getCurrentState()),new Promise(resolve=>{timer=setTimeout(()=>resolve(null),4000)})]);
   if(state)await onState(state);else await report();
  }catch{}finally{clearTimeout(timer);heartbeatRunning=false}
 }
 setInterval(paintClock,500);setInterval(heartbeat,5000);
 for(const event of ['pageshow','online'])window.addEventListener(event,()=>refresh().catch(()=>{}));const prepareVisible=()=>{if(selected&&user()&&!sdkDevice&&!loading)load().then(paint).catch(error=>notice(error.message))};window.addEventListener('online',prepareVisible);window.addEventListener('pageshow',prepareVisible);document.addEventListener('visibilitychange',()=>{if(!document.hidden){prepareVisible();refresh().catch(()=>{})}});
 document.addEventListener('visibilitychange',()=>{if(active)diagnostic('spotify-visibility')});
 try{navigator.audioSession?.addEventListener?.('statechange',()=>{if(active)diagnostic('spotify-audio-session')})}catch{}
 return {configure(options){cfg=options},config,connect,renderProfile,disconnect,setPartyView,startParty,toggle,next,previous,seek,apply,refresh,stop,signOut,statusText,openOutput,hasView:()=>!!displayed(),isActive:p=>active&&(!p||party?.id===p.id),importPlaylists:(q,offset,catalog)=>cfg.api('/spotify/playlists?'+new URLSearchParams({q,offset,catalog:catalog?'1':'0'})),importPlaylistTracks:item=>cfg.api('/spotify/playlists/'+encodeURIComponent(item.id))};
})();

/* Optional MusicKit transport. Apple passwords and user tokens never enter Arrify storage. */
'use strict';
window.ArrifyApple=(()=>{
 let cfg=null,music=null,sdkAccount=null,loadingSDK=null,party=null,active=false,applying=false,controlling=false,advancing=false,polling=false,reporting=false,sequence=0,generation=0,confirmed=false,appliedKey='',lastError='';
 let blockedPlaybackKey='',observedDuration=0,diagnosticOwner=null,diagnosticEvents=[],diagnosticLast=0;
 function requireFullPlayback(){
  const mode=window.MusicKit?.PlaybackMode?.FULL_PLAYBACK_ONLY;
  if(mode===undefined)throw Error('Full Apple Music playback is unavailable. Refresh Arrify or open it in Safari.');
  music.playbackMode=mode;
  if(music.playbackMode!==mode)throw Error('Apple Music could not enable full-song playback. Try Safari.');
 }
 function shortPlayback(){
  const actual=Number(music?.currentPlaybackDuration)||observedDuration,catalog=Number(current()?.duration)||0;
  return actual>0&&actual<=90&&catalog>actual+15&&actual<catalog*.75;
 }
 function diagnostic(event){
  try{
  const uid=user()?.id;if(!uid)return;
  if(diagnosticOwner!==uid){diagnosticOwner=uid;diagnosticEvents=[];diagnosticLast=0}
  diagnosticEvents.push({event,wall_ms:Date.now(),duration:Number(music?.currentPlaybackDuration)||observedDuration,metadata_duration:Number(current()?.duration)||0,authorized:!!music?.isAuthorized,preview_only:!!music?.previewOnly,installed:!!navigator.standalone||!!window.matchMedia?.('(display-mode: standalone)').matches||!!window.matchMedia?.('(display-mode: fullscreen)').matches,hidden:!!document.hidden});
  diagnosticEvents=diagnosticEvents.slice(-20);
  if(Date.now()-diagnosticLast<5000&&event!=='apple-preview-blocked')return;
  diagnosticLast=Date.now();cfg.api('/player/diagnostics',{source:'apple',events:diagnosticEvents.slice()}).catch(()=>{});
  }catch{}
 }
 async function blockShortPlayback(){
  confirmed=false;appliedKey='';blockedPlaybackKey=party.id+':'+party.apple_playback.epoch+':'+party.apple_playback.entry_id;diagnostic('apple-preview-blocked');
  await music.pause().catch(()=>{});
  notice('Apple Music returned a short preview instead of the full song. Your queue is preserved. Try Play again; if this repeats in the Home Screen app, open Arrify in Safari.');
  await report('error','playback').catch(()=>{});paint();
 }
 const ownerKey='arrify:apple-music:owner';
 let selectedParty=null,viewSelected=false,dockObserver=null;
 function syncDockSpace(){
  const viewport=window.visualViewport,root=document.documentElement;if(!root)return;
  const gap=viewport&&Math.abs(viewport.scale-1)<.01?Math.max(0,root.clientHeight-viewport.height-viewport.offsetTop):0;
  const updates=[['--visual-bottom-gap',Math.round(gap)+'px']];
  for(const [selector,variable] of [['.player','--native-dock-clearance'],['#apple-mini','--apple-dock-clearance'],['#spotify-mini','--apple-dock-clearance']]){
   const bar=document.querySelector(selector);if(!bar?.isConnected)continue;
   const clearance=Math.ceil(bar.getBoundingClientRect().height+Math.max(0,parseFloat(getComputedStyle(bar).bottom)||0)+gap+20);
   updates.push([variable,clearance+'px']);
  }
  for(const [key,value] of updates)if(root.style?.getPropertyValue(key)!==value)root.style?.setProperty(key,value);
 }
 let dockFrame=0;function scheduleDockSpace(){if(dockFrame)return;dockFrame=requestAnimationFrame(()=>{dockFrame=0;syncDockSpace()})}
 const nativeDock=document.querySelector('.player');if(nativeDock&&typeof ResizeObserver==='function')new ResizeObserver(scheduleDockSpace).observe(nativeDock);
 window.addEventListener('resize',scheduleDockSpace);window.visualViewport?.addEventListener('resize',scheduleDockSpace);window.visualViewport?.addEventListener('scroll',scheduleDockSpace);window.addEventListener('pageshow',scheduleDockSpace);

 const displayedParty=()=>selectedParty?(active&&party?.id===selectedParty.id?party:selectedParty):(active&&!viewSelected?party:null);
 const displayOwns=()=>!!(active&&displayedParty()?.id===party?.id&&owns());
 const displayTrack=()=>{const p=displayedParty();return p?.items.find(t=>t.entry_id===p.apple_playback?.entry_id)||p?.items.find(t=>t.status==='playing')||p?.items.find(t=>t.status==='queued')};
 function setPartyView(p){viewSelected=true;selectedParty=p?.source==='apple'&&!p.ended?p:null;paint()}
 let appleMedia=null;const outputAttached=new WeakSet();
 function attachAppleMedia(event){
  const candidate=event?.mediaElement||event?.detail?.mediaElement||event?.detail||event?.target||event;
  if(!candidate||!['AUDIO','VIDEO'].includes(candidate.tagName)||candidate.id==='audio')return;
  appleMedia=candidate;candidate.setAttribute('x-webkit-airplay','allow');candidate.disableRemotePlayback=false;
  if(outputAttached.has(candidate))return;outputAttached.add(candidate);for(const name of ['webkitcurrentplaybacktargetiswirelesschanged','webkitplaybacktargetavailabilitychanged'])candidate.addEventListener(name,paint);
 }
 function outputMedia(){
  // MusicKit v3 creates these elements; never fall back to Arrify’s separate audio.
  const element=document.querySelector('#apple-music-player')||document.querySelector('#apple-music-video-player');
  if(element)attachAppleMedia(element);return element||appleMedia;
 }
 const user=()=>cfg?.getUser?.(),device=()=>cfg.getDevice();
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const notice=text=>{lastError=text;const n=document.querySelector('#apple-party-status');if(n)n.textContent=text;else if(text)cfg?.toast(text)};
 const storedOwner=()=>{try{return localStorage.getItem(ownerKey)}catch{return null}};
 const setOwner=value=>{try{if(value==null)localStorage.removeItem(ownerKey);else localStorage.setItem(ownerKey,String(value))}catch{}};
 async function config(){return cfg.api('/apple-music')}
 async function load(){
  if(!user()?.id)throw Error('Sign in to Arrify to use Apple Music.');
  if(music){if(storedOwner()!==String(user()?.id)){await stop(false);await music.unauthorize();setOwner(null)}sdkAccount=user()?.id;if(!active)cfg.restoreLocalSession?.();return music}if(loadingSDK)return loadingSDK;const account=user()?.id;
  loadingSDK=(async()=>{
   const settings=await cfg.api('/apple-music/token');if(settings.origin!==location.origin)throw Error('Open Arrify at its configured HTTPS address to connect Apple Music.');
   if(!window.MusicKit)await new Promise((resolve,reject)=>{let timer;const finish=()=>{clearTimeout(timer);document.removeEventListener('musickitloaded',finish);resolve()};const script=document.createElement('script');script.src='https://js-cdn.music.apple.com/musickit/v3/musickit.js';script.async=true;script.onerror=()=>{clearTimeout(timer);document.removeEventListener('musickitloaded',finish);script.remove();reject(Error('Apple Music could not load. Check your connection and try again.'))};document.addEventListener('musickitloaded',finish,{once:true});timer=setTimeout(()=>{document.removeEventListener('musickitloaded',finish);script.remove();reject(Error('Apple Music is taking too long to load. Try again.'))},20000);document.head.append(script)});
   await window.MusicKit.configure({developerToken:settings.developerToken,app:{name:'Arrify',build:'2.0.0-dev'}});music=window.MusicKit.getInstance();
   if(storedOwner()!==String(account))await music.unauthorize();
   if(user()?.id!==account){await music.unauthorize();music=null;sdkAccount=null;throw Error('Your Arrify sign-in changed. Reopen the profile.');}
   sdkAccount=account;music.addEventListener('mediaElementCreated',attachAppleMedia);music.addEventListener('playbackStateDidChange',onState);music.addEventListener('playbackTargetAvailableDidChange',paint);music.addEventListener('playbackTargetIsWirelessDidChange',paint);music.addEventListener('mediaPlaybackError',()=>{if(active&&!applying){notice('Apple Music could not play this song. Reconnect, retry, or skip it explicitly.');report('error','playback').catch(()=>{})}});
   if(!active)cfg.restoreLocalSession?.();return music;
  })();try{return await loadingSDK}finally{loadingSDK=null}
 }
 async function connect(){
  if(!user()?.id)throw Error('Sign in to Arrify to use Apple Music.');
  const account=user()?.id,m=music&&sdkAccount===account?music:await load();const token=await m.authorize();
  if(user()?.id!==account){await m.unauthorize();throw Error('Your Arrify sign-in changed. Please try again.');}
  try{const result=await cfg.api('/apple-music',{action:'connect',music_user_token:token||m.musicUserToken});setOwner(account);cfg.restoreLocalSession?.();await renderProfile();return result}catch(error){await m.unauthorize();setOwner(null);throw error}
 }
 async function playlistRead(path,params={}){
  const m=await load();if(!m.isAuthorized||storedOwner()!==String(user()?.id))throw Error('Connect Apple Music on this device in Profile to import playlists.');
  if(!m.api||typeof m.api.music!=='function')throw Error('Apple Music playlist access is unavailable. Refresh and reconnect.');
  const response=await m.api.music(path,params);return response?.data?.data?response.data:response?.data?.results?response.data:response?.data&&Array.isArray(response.data)?{data:response.data}:response;
 }
 async function importPlaylists(query='',offset=0,catalog=false){
  if(!catalog){const d=await playlistRead('/v1/me/library/playlists',{limit:100,offset});return {items:(d.data||[]).filter(p=>p.type==='library-playlists').map(p=>({id:p.id,kind:'library',name:p.attributes?.name||'Untitled playlist',owner:'Your Apple Music library',artwork:p.attributes?.artwork?.url||'',count:null})),more:!!d.next}}
  const sf=(await config()).storefront;if(!/^[a-z]{2}$/.test(sf))throw Error('Reconnect Apple Music to choose your storefront.');
  if(/^https?:/i.test(query)){
   let link;try{link=new URL(query)}catch{throw Error('Paste an Apple Music playlist link.')}
   const id=link.pathname.split('/').pop();if(link.protocol!=='https:'||link.hostname!=='music.apple.com'||link.username||link.password||!/^pl\.[A-Za-z0-9._-]{1,190}$/.test(id))throw Error('Paste a music.apple.com playlist link.');
   const d=await playlistRead('/v1/catalog/'+sf+'/playlists/'+encodeURIComponent(id));return {items:(d.data||[]).map(p=>({id:p.id,kind:'catalog',name:p.attributes?.name||'Apple Music playlist',owner:p.attributes?.curatorName||'Apple Music',artwork:p.attributes?.artwork?.url||'',count:null})),more:false};
  }
  const d=await playlistRead('/v1/catalog/'+sf+'/search',{term:query,types:'playlists',limit:25});return {items:(d.results?.playlists?.data||[]).map(p=>({id:p.id,kind:'catalog',name:p.attributes?.name||'Apple Music playlist',owner:p.attributes?.curatorName||'Apple Music',artwork:p.attributes?.artwork?.url||'',count:null})),more:false};
 }
 async function importPlaylistTracks(item){
  if(!/^[A-Za-z0-9._-]{1,200}$/.test(String(item.id))||!['library','catalog'].includes(item.kind))throw Error('Choose an Apple Music playlist.');
  const sf=item.kind==='catalog'?(await config()).storefront:'';if(item.kind==='catalog'&&!/^[a-z]{2}$/.test(sf))throw Error('Reconnect Apple Music.');
  const path=item.kind==='library'?'/v1/me/library/playlists/'+encodeURIComponent(item.id)+'/tracks':'/v1/catalog/'+sf+'/playlists/'+encodeURIComponent(item.id)+'/tracks';
  let tracks=[],offset=0,more=false;
  do{const d=await playlistRead(path,{limit:100,offset,include:'catalog'}),rows=d.data||[];more=!!d.next;if(!rows.length)break;
   for(const row of rows){if(tracks.length>=500)break;const a=row.attributes||{},id=row.type==='songs'?String(row.id):String(a.playParams?.catalogId||row.relationships?.catalog?.data?.find(t=>t.type==='songs')?.id||'');tracks.push({id,title:a.name||'Unavailable song',artist:a.artistName||'',available:['songs','library-songs'].includes(row.type)&&/^[0-9]{1,20}$/.test(id)})}offset+=rows.length;
  }while(more&&tracks.length<500);
  return {name:item.name,tracks,total:tracks.length,truncated:more};
 }
 async function disconnect(){await stop(true).catch(()=>{});let result;try{result=await cfg.api('/apple-music',{action:'disconnect'})}finally{try{if(music)await music.unauthorize()}catch{}setOwner(null);await renderProfile().catch(()=>{})}return result}
 async function renderProfile(){
  if(!cfg||!user())return;const settings=await config();let node=document.querySelector('#apple-music-profile');
  if(!node){const bottom=document.querySelector('#music-services-list')||document.querySelector('#profile-dialog .profile-bottom');if(!bottom)return;bottom.insertAdjacentHTML(bottom.id==='music-services-list'?'afterbegin':'beforebegin','<section id="apple-music-profile" class="profile-section"></section>');node=document.querySelector('#apple-music-profile')}
  node.hidden=!settings.enabled;if(node.hidden)return;if(settings.available&&(settings.linked||music)){node.innerHTML=window.ArrifyPlayer.musicServiceHeading('apple','Apple Music')+'<p class="profile-help" role="status">Preparing Apple Music sign-in…</p>';try{await load()}catch{node.innerHTML=window.ArrifyPlayer.musicServiceHeading('apple','Apple Music')+'<p class="profile-help" role="status">Apple Music sign-in could not load. Reopen Profile to retry.</p>';return}if(!user())return}const connected=!!music?.isAuthorized&&storedOwner()===String(user().id);
  node.innerHTML=window.ArrifyPlayer.musicServiceHeading('apple','Apple Music',connected)+'<p class="profile-help">'+(!settings.available?'Setup is pending. Arrify library parties are available.':connected?'Connected on this device. Apple handles your sign-in.':settings.linked?'Your account is linked. Connect this device to play Apple Music parties.':'Connect through Apple to host an Apple Music PartyQueue.')+'</p><div class="party-buttons"><button type="button" class="chip" id="apple-connect" '+(!settings.available||connected?'disabled':'')+'>'+(connected?'Apple Music connected':'Connect Apple Music')+'</button>'+(settings.linked?'<button type="button" class="text-button" id="apple-disconnect">Disconnect</button>':'')+'</div><p id="apple-profile-status" class="profile-help" role="status"></p>';
 }
 function owns(p=party){const d=p?.apple_playback;return !!(d&&p&&d.party_id===p.id&&d.device_id===device().id)}
 function current(p=party){return p?.items.find(t=>t.entry_id===p.apple_playback?.entry_id)}
 function observed(){const P=window.MusicKit.PlaybackStates,s=music.playbackState;return s===P.playing?'playing':s===P.paused||s===P.stopped?'paused':'buffering'}
 async function report(state=observed(),error=''){
  if(Number(music?.currentPlaybackDuration)>0)observedDuration=Number(music.currentPlaybackDuration);
  if(!active||!party||!owns()||reporting||!user()||applying&&['playing','paused'].includes(state))return;reporting=true;const p=party,d=p.apple_playback;
  try{const value=await cfg.api('/parties/'+p.id+'/apple-playback',{action:'heartbeat',device_id:device().id,epoch:d.epoch,revision:d.revision,entry_id:d.entry_id,sequence:++sequence,observed:state,error,position:Math.max(0,Math.min(86400,Number(music?.currentPlaybackTime)||0)),duration:Math.max(0,Math.min(86400,Number(music?.currentPlaybackDuration)||0))});if(party?.id===p.id&&party.apple_playback?.epoch===d.epoch&&(value.apple_playback?.revision||0)>=(party.apple_playback?.revision||0)){party=value._partial?{...party,...value}:value;paint()}}
  catch(error){if(error.status===409){await refresh()}else if([401,403,404,410].includes(error.status)){await stop(false);notice('Apple Music authorization ended. Reconnect in Profile to continue.')}else throw error}
  finally{reporting=false}
 }
 async function onState(event){
  if(!active||applying||!party||!owns())return;const P=window.MusicKit.PlaybackStates,state=event?.state??music.playbackState;
  const eventId=event?.nowPlayingItem?.id;if(eventId!=null&&String(eventId)!==String(current()?.id))return;
  if(Number(music?.currentPlaybackDuration)>0)observedDuration=Number(music.currentPlaybackDuration);
  if(state===P.playing)confirmed=true;
  if(state===P.completed||P.ended!==undefined&&state===P.ended){if(confirmed&&!advancing&&!party.apple_playback.user_paused&&shortPlayback()){await blockShortPlayback();return}if(confirmed&&!advancing&&!party.apple_playback.user_paused)await next(true).catch(()=>notice('Could not advance the party. Retry or use Next.'));return}
  const duration=Number(music.currentPlaybackDuration)||0,position=Number(music.currentPlaybackTime)||0;
  if(advancing||confirmed&&duration>0&&position>=duration-.75&&(state===P.paused||state===P.stopped))return;
  await report().catch(()=>{});paint();
 }
 async function apply(p){
  if(selectedParty?.id===p.id)selectedParty=p.ended?null:p;
  if(p.source!=='apple'){if(active&&p.id===party?.id){await stop(false);notice('Apple Music is unavailable after the server update. Refresh Arrify.')}return}if(party&&active&&p.id!==party.id){paint();return}
  party=p;const d=p.apple_playback;
  if(!d||d.party_id!==p.id||d.device_id!==device().id){if(active)await stop(false);else paint();return}
  if(!active){paint();return}
  const key=p.id+':'+d.epoch+':'+d.entry_id,t=current(p);
  if(!d.entry_id||!t){confirmed=false;appliedKey='';if(music.playbackState===window.MusicKit.PlaybackStates.playing)await music.stop();paint();return}
  if(applying||key===blockedPlaybackKey)return;
  if(key!==appliedKey){applying=true;const ticket=++generation;confirmed=false;sequence=0;
   try{observedDuration=0;requireFullPlayback();await music.setQueue({songs:[String(t.id)]});if(ticket!==generation||!active)return;appliedKey=key;
    if(d.desired_playing){await music.play();if(d.position>0)await music.seekToTime(d.position)}else await music.pause();
   }catch(error){if(ticket===generation){blockedPlaybackKey=key;appliedKey='';diagnostic('apple-playback-error');lastError=music?.isAuthorized?'Apple Music could not start full-song playback. Try Play again; if this repeats in the Home Screen app, use Safari. Your queue is preserved.':'Reconnect Apple Music in your profile.';notice(lastError);await report(music?.isAuthorized?'needs-gesture':'error',music?.isAuthorized?'needs-gesture':'authorization').catch(()=>{})}}
   finally{applying=false;if(ticket===generation&&active){const latest=party?.apple_playback,latestKey=party?.id+':'+latest?.epoch+':'+latest?.entry_id;if(latestKey!==key){await apply(party);return}if(!lastError&&observed()==='playing')confirmed=true;if(!lastError){await report().catch(()=>{});diagnostic('apple-playing')}paint()}}
  }else if(!d.desired_playing&&music.playbackState===window.MusicKit.PlaybackStates.playing)await music.pause();
 }
 async function startParty(p,takeover=false){
  if(p.source!=='apple')throw Error('This party uses the Arrify library.');const m=await load();if(!m.isAuthorized||storedOwner()!==String(user()?.id))throw Error('Connect Apple Music on this device in your profile first.');
  const value=await cfg.api('/parties/'+p.id+'/apple-playback',{action:'start',device_id:device().id,device_name:device().name,takeover});
  await window.ArrifySpotify?.stop?.(false);party=value;active=true;cfg.pauseLocalPlayback();appliedKey='';blockedPlaybackKey='';lastError='';await apply(value);return value;
 }
 async function toggle(p){if(controlling||advancing)return party;controlling=true;paint();try{return await toggleNow(p)}finally{controlling=false;paint()}}
 async function toggleNow(p){
  if(!active||p?.id!==party?.id||!owns(p))return startParty(p,!!p.apple_playback?.online);
  const d=party.apple_playback,play=music.playbackState!==window.MusicKit.PlaybackStates.playing;
  party=await cfg.api('/parties/'+party.id+'/apple-playback',{action:play?'resume':'pause',device_id:device().id,epoch:d.epoch});
  if(!party.apple_playback?.entry_id){await apply(party);return party}
  if(play){blockedPlaybackKey='';lastError='';if(!appliedKey)await apply(party);else{try{await music.play();if(observed()==='playing')confirmed=true;await report()}catch{notice('Tap Resume to start Apple Music on this device.');await report('needs-gesture','needs-gesture')}}}else{await music.pause();await report('paused')}
  paint();return party;
 }
 async function next(automatic=false){
  if(!active||!owns()||advancing||controlling||!party?.apple_playback?.entry_id)return;advancing=true;const p=party,d=p.apple_playback;
  try{const value=await cfg.api('/parties/'+p.id+'/apple-playback',{action:'next',device_id:device().id,epoch:d.epoch,revision:d.revision,entry_id:d.entry_id,automatic});if((value.apple_playback?.revision||0)<(party?.apple_playback?.revision||0)){await refresh();return party}party=value;lastError='';await apply(party);return party}finally{advancing=false;paint()}
 }
 async function previous(){
  if(!active||!owns()||advancing||controlling||!party?.apple_playback?.entry_id)return;advancing=true;const p=party,d=p.apple_playback;
  try{party=await cfg.api('/parties/'+p.id+'/apple-playback',{action:'previous',device_id:device().id,epoch:d.epoch,entry_id:d.entry_id});lastError='';await apply(party);return party}finally{advancing=false;paint()}
 }
 async function refresh(){
  if(!active||!party||polling||!user())return;polling=true;try{const p=await cfg.api('/parties/'+party.id);if(active&&party?.id===p.id)await apply(p)}catch(error){if([401,403,404].includes(error.status))await stop(false)}finally{polling=false}
 }
 async function stop(notify=true){
  if(active&&notify&&party&&owns()){const p=party,d=p.apple_playback;try{await cfg.api('/parties/'+p.id+'/apple-playback',{action:'pause',device_id:device().id,epoch:d.epoch})}catch{}}
  active=false;generation++;confirmed=false;appliedKey='';try{if(music)await music.stop()}finally{cfg?.restoreLocalSession?.();paint()}
 }
 // Arrify sign-out stops playback, but disconnecting Apple is a separate action.
 async function signOut(){selectedParty=null;lastError='';try{await stop(true)}catch{}sdkAccount=null}
 function statusText(p){if(active&&p?.id===party?.id&&owns(p))return lastError||(!current(p)?'Waiting for requests.':music?.playbackState===window.MusicKit.PlaybackStates.playing?'Playing on this device. Use Control Center to choose AirPlay speakers.':'Paused or waiting for playback. Tap Resume to continue.');return p?.apple_playback?.online?'Playback is on '+(p.apple_playback.device_name||'another device')+'. Choose Play here to move it.':storedOwner()===String(user()?.id)?'Choose Play party to start on this device. Use Control Center for AirPlay.':'Connect Apple Music in Profile, then choose Play party. Use Control Center for AirPlay.'}
 const stamp=value=>{value=Math.max(0,Math.floor(Number(value)||0));return Math.floor(value/60)+':'+String(value%60).padStart(2,'0')};
 function artwork(t){if(!t)return '';const value=String(t?.artwork||(active&&current()?.id===t.id?music?.nowPlayingItem?.artwork?.url:'')||'').replace('{w}','512').replace('{h}','512').replace('{f}','jpg');return /^https:\/\/[^/?#@]+\.mzstatic\.com\//i.test(value)?value:''}
 function openOutput(){
  if(!active||!music)return;
  const media=outputMedia();
  // Safari requires the native call synchronously inside the user's tap.
  if(typeof media?.webkitShowPlaybackTargetPicker==='function')try{media.webkitShowPlaybackTargetPicker();return}catch(error){if(error.name==='AbortError')return}
  if(typeof music.showPlaybackTargetPicker==='function'&&music.playbackTargetAvailable!==false)try{music.showPlaybackTargetPicker();return}catch(error){if(error.name==='AbortError')return}
  const message='Choose AirPlay in Control Center’s Now Playing panel to select your speakers.';
  const status=document.querySelector('#apple-full-player[open] [data-apple-full-status]');if(status)status.textContent=message;cfg.toast(message);
 }

 async function seek(position){if(!active||!owns()||applying||advancing||controlling||!current())return;const duration=Number(music.currentPlaybackDuration)||Number(current().duration)||0;if(!Number.isFinite(position)||duration<=0)return;await music.seekToTime(Math.max(0,Math.min(duration,position)));await report();paintClock()}
 async function pauseExplicit(){
  if(!active||!owns())return;const p=party,d=p.apple_playback;
  const value=await cfg.api('/parties/'+p.id+'/apple-playback',{action:'pause',device_id:device().id,epoch:d.epoch});
  if(!active||party?.id!==p.id)return;party=value;await music.pause();await report('paused');paint();
 }
 function bindMedia(){
  if(!active||!navigator.mediaSession)return;const session=navigator.mediaSession,t=current();
  if(t&&typeof MediaMetadata!=='undefined')try{session.metadata=new MediaMetadata({title:t.title,artist:t.artist,album:t.album,artwork:[{src:artwork(t)||location.origin+'/assets/icon-512.png',sizes:'512x512',type:artwork(t)?'image/jpeg':'image/png'}]})}catch{}
  for(const [action,fn] of Object.entries({play:()=>toggle(party),pause:pauseExplicit,previoustrack:()=>previous(),nexttrack:()=>next(false),seekbackward:null,seekforward:null,seekto:d=>seek(d.seekTime)}))try{session.setActionHandler(action,fn?d=>Promise.resolve(fn(d||{})).catch(()=>notice('Apple Music could not apply that control. Try again in Arrify.')):null)}catch{}
  try{session.playbackState=music.playbackState===window.MusicKit.PlaybackStates.playing?'playing':'paused'}catch{}
 }
 function paintClock(){
  const t=displayTrack(),local=displayOwns()&&!!t,position=local?Math.max(0,Number(music?.currentPlaybackTime)||0):0,duration=t?Math.max(0,local?Number(music?.currentPlaybackDuration)||Number(t.duration)||0:Number(t.duration)||0):0;
  for(const range of (document.hidden?[]:document.querySelectorAll('[data-apple-seek]'))){const bar=range.closest('[data-apple-clock]')||range.closest('#apple-mini');range.max=duration||1;if(document.activeElement!==range)range.value=Math.min(position,duration||0);range.disabled=!local||!duration||applying||advancing;bar.querySelector('[data-apple-position]').textContent=stamp(position);bar.querySelector('[data-apple-duration]').textContent=stamp(duration)}
  const progress=document.hidden?null:document.querySelector('#apple-mini .apple-mini-progress');if(progress)progress.style.setProperty('--progress',(duration?Math.min(100,position/duration*100):0)+'%');
  if(navigator.mediaSession?.setPositionState&&local&&duration>0&&position<=duration)try{navigator.mediaSession.setPositionState({duration,playbackRate:1,position})}catch{}
 }
 function paint(){
  const mini=document.querySelector('#apple-mini'),view=displayedParty(),local=displayOwns();document.body.classList.toggle('apple-party-playing',!!view||!!window.ArrifySpotify?.hasView?.());
  if(!view){dockObserver?.disconnect();dockObserver=null;if(!window.ArrifySpotify?.hasView?.())document.documentElement?.style?.removeProperty('--apple-dock-clearance');if(mini)mini.remove();document.querySelector('#apple-full-player')?.close();return}const t=displayTrack(),playing=!!(local&&t&&music?.playbackState===window.MusicKit?.PlaybackStates.playing);
  const bar=mini||document.createElement('div');bar.id='apple-mini';bar.className='apple-mini';const busy=controlling||advancing||applying,wireless=!!(outputMedia()?.webkitCurrentPlaybackTargetIsWireless||music?.playbackTargetIsWireless||music?.playbackController?.playbackTargetIsWireless),key=JSON.stringify([view.id,t?.id,t?.title,t?.artist,artwork(t),playing,busy,wireless,local]);
  if(bar._appleKey!==key){bar._appleKey=key;bar.innerHTML=window.ArrifyPlayer.externalMiniHTML('apple','Apple Music',{track:t,art:artwork(t),playing,busy,local,wireless})}if(!mini){document.body.append(bar);if(typeof ResizeObserver==='function'){dockObserver?.disconnect();dockObserver=new ResizeObserver(syncDockSpace);dockObserver.observe(bar)}}syncDockSpace();bindMedia();paintFull();paintClock();
  const hint=document.querySelector('#apple-party-status');if(hint)hint.textContent=statusText(view);
 }
 function appleTab(queue){
  const d=document.querySelector('#apple-full-player');if(!d)return;
  window.ArrifyPlayer.setQueueSheet(d,queue);d.querySelector('[data-apple-panel=listen]').hidden=false;d.querySelector('[data-apple-panel=queue]').hidden=!queue;d.querySelector('.np-view-nav').hidden=!queue;
  if(queue&&d.open)d.querySelector('[data-apple-back]').focus({preventScroll:true});
  window.ArrifyPlayer?.fitLayout(d);
 }
 function fullPlayer(){return window.ArrifyPlayer.createExternalDialog('apple','Apple Music')}

 function paintFull(){window.ArrifyPlayer.renderExternalDialog('apple','Apple Music',{view:displayedParty(),track:displayTrack(),artwork,local:displayOwns(),playing:!!(displayOwns()&&displayTrack()&&music?.playbackState===window.MusicKit?.PlaybackStates.playing),busy:controlling||advancing||applying,wireless:!!outputMedia()?.webkitCurrentPlaybackTargetIsWireless,status:lastError})}

 document.addEventListener('click',event=>{const b=event.target.closest('button');if(!b||!cfg)return;
  if(b.hasAttribute('data-apple-expand')){const dialog=fullPlayer();dialog.showModal();appleTab(false);document.body.classList.add('apple-player-expanded');paintFull();paintClock();return}if(b.hasAttribute('data-apple-back')){appleTab(false);document.querySelector('#apple-full-player [data-apple-queue]').focus({preventScroll:true});return}if(b.hasAttribute('data-apple-queue')){appleTab(true);return}if(b.hasAttribute('data-apple-party-link')){document.querySelector('#apple-full-player')?.close();location.hash='party/'+displayedParty().id;return}if(b.hasAttribute('data-apple-collapse')){document.querySelector('#apple-full-player')?.close();return}
  const run=async()=>{if(b.hasAttribute('data-apple-output'))return openOutput();if(b.id==='apple-connect'){await connect();return}if(b.id==='apple-disconnect'){await disconnect();return}if(b.hasAttribute('data-apple-toggle'))return toggle(displayedParty());if(b.hasAttribute('data-apple-next'))return next(false);if(b.hasAttribute('data-apple-previous'))return previous()};
  if(b.id==='apple-connect'||b.id==='apple-disconnect'||['data-apple-output','data-apple-toggle','data-apple-next','data-apple-previous'].some(x=>b.hasAttribute(x))){b.disabled=true;run().catch(error=>{const n=document.querySelector('#apple-profile-status');if(n)n.textContent=error.message;else notice(error.message)}).finally(()=>b.disabled=false)}
 });
 document.addEventListener('click',event=>{if(event.target.closest('[data-apple-party-link]'))document.querySelector('#apple-full-player')?.close()});
 document.addEventListener('change',event=>{if(event.target.matches?.('[data-apple-seek]'))seek(Number(event.target.value)).catch(()=>notice('Could not seek. Try again.'))});
 setInterval(paintClock,500);
 setInterval(()=>{if(active){if(!document.hidden&&!document.querySelector('[data-party-id="'+party?.id+'"]'))refresh().catch(()=>{});const P=window.MusicKit.PlaybackStates;if(confirmed&&!applying&&!advancing&&(music.playbackState===P.completed||P.ended!==undefined&&music.playbackState===P.ended))onState().catch(()=>{});else report().catch(()=>{})}},5000);
 const wake=()=>{if(active){refresh().then(()=>report()).catch(()=>{})}};document.addEventListener('visibilitychange',()=>{if(!document.hidden)wake()});window.addEventListener('pageshow',wake);window.addEventListener('online',wake);
 return {hasView:()=>!!displayedParty(),configure(options){cfg=options},setPartyView,config,connect,importPlaylists,importPlaylistTracks,disconnect,renderProfile,prepare:load,startParty,toggle,next,previous,openOutput,apply,refresh,stop,signOut,statusText,isActive:p=>active&&(!p||p.id===party?.id)};
})();

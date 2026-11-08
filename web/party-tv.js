/* Independent read-only client. No host login, player or MusicKit credentials. */
'use strict';
(()=>{
 const token=location.hash.slice(1),base='/api/v1/party-display/'+encodeURIComponent(token),$=id=>document.getElementById(id);
 let state=null,etag=null,events=null,pending=false,again=false,lastContact=0,clock=0,terminal=false,art='',qr='',queue='',printed=false,wakeSentinel=null,wakePending=null;
 const text=(id,value)=>{$(id).textContent=value??''};
 const time=value=>{value=Math.max(0,Math.floor(Number(value)||0));return Math.floor(value/60)+':'+String(value%60).padStart(2,'0')};
 function hideQR(){if($('tv-wifi'))$('tv-wifi').hidden=true;const img=$('tv-qr');img.hidden=true;img.removeAttribute('src');qr=''}
 function disconnected(){if(terminal)return;text('tv-connection','Connection lost · Reconnecting…');document.body.classList.add('tv-disconnected')}
 function finish(status){terminal=true;$('tv-source').hidden=true;events?.close();releaseWake();hideQR();$('tv-stage').hidden=true;$('tv-inactive').hidden=false;$('tv-inactive').replaceChildren();const title=document.createElement('h2'),hint=document.createElement('p');title.textContent=status==='deleted'?'Party no longer available':status==='ended'?'Party Ended':'Invalid invitation';hint.textContent='Ask the host to start a new party and share its new invitation.';$('tv-inactive').append(title,hint);text('tv-connection','Inactive');$('tv-print').disabled=true}
 function paint(p){
  if(p.ended||['ended','deleted'].includes(p.state)){finish(p.state||'ended');return}
  state=p;window.ArrifyWiFi?.paint($('tv-wifi'),p.wifi,true);clock=performance.now();lastContact=clock;$('tv-stage').hidden=false;$('tv-inactive').hidden=true;document.body.classList.remove('tv-disconnected');text('tv-connection','Live');text('tv-party-name',p.name);document.title=p.name+' · PartyQueue TV';
  const now=p.items.find(t=>t.status==='playing'),url=now?.artwork_url||now?.artwork||'/assets/icon-512.png';if(art!==url){$('tv-art').src=url;art=url}
  const source=['arrify','apple','spotify'].includes(p.source)?p.source:null;
  $('tv-source').hidden=!source;
  for(const badge of document.querySelectorAll('[data-tv-source]')){
   badge.hidden=badge.dataset.tvSource!==source;
   if(badge.dataset.tvSource==='spotify')badge.href=source==='spotify'&&/^[A-Za-z0-9]{22}$/.test(String(now?.id||''))?'https://open.spotify.com/track/'+now.id:'https://open.spotify.com/';
   if(badge.dataset.tvSource==='apple')badge.href=source==='apple'&&/^[0-9]{1,20}$/.test(String(now?.id||''))?'https://music.apple.com/song/'+now.id:'https://music.apple.com/';
  }
  text('tv-title',now?.title||'The party is warming up.');text('tv-artist',now?.artist||'New requests appear automatically.');text('tv-album',now?.album||'');text('tv-playback-label',!now?'WAITING FOR MUSIC':!p.playback?.online?'PLAYER OFFLINE':p.playing?'NOW PLAYING':'PAUSED');
  text('tv-requester',now?.requested_by||'');$('tv-requester').hidden=!now?.requested_by;
  text('tv-requests-status',p.open?'Requests are open':'Requests paused · You can still join when reopened');
  if(qr!==p.qr_url){qr=p.qr_url;$('tv-qr').src=qr}$('tv-qr').hidden=false;
  const upcoming=p.items.filter(t=>t.status==='queued'),signature=JSON.stringify(upcoming);text('tv-queue-count',Math.max(0,p.total-(now?1:0))+' songs');
  if(queue!==signature){queue=signature;$('tv-queue').replaceChildren();for(const [i,t] of upcoming.slice(0,5).entries()){const li=document.createElement('li'),number=document.createElement('span'),copy=document.createElement('div'),title=document.createElement('strong'),artist=document.createElement('small');number.textContent=i+1;title.textContent=t.title;artist.textContent=t.artist+(t.requested_by?' · '+t.requested_by:'');copy.append(title,artist);li.append(number,copy);$('tv-queue').append(li)}if(!upcoming.length){const li=document.createElement('li');li.textContent='More requests will appear here.';$('tv-queue').append(li)}}
  tick();
  if(new URLSearchParams(location.search).has('print')&&!printed){printed=true;Promise.all([$('tv-qr').decode(),...Array.from(p.wifi?.include_print?$('tv-wifi')?.querySelectorAll('img')||[]:[],img=>img.decode())]).then(()=>window.print()).catch(()=>{})}
 }
 function tick(){if(document.hidden)return;if(!state||terminal)return;const d=state.playback||{},duration=Number(d.duration)||0,age=Math.max(0,((d.server_time||0)-(d.observed_at||d.server_time||0))+(performance.now()-clock)/1000),position=(Number(d.position)||0)+(d.playing?Math.min(age,20):0);$('tv-progress').max=duration||1;$('tv-progress').value=duration?Math.min(position,duration):0;text('tv-position',time(position));text('tv-duration',time(duration));if(performance.now()-lastContact>12000)disconnected()}
 async function refresh(){if(terminal)return;if(pending){again=true;return}pending=true;try{const response=await fetch(base,{cache:'no-store',headers:etag?{'If-None-Match':etag}:{},signal:AbortSignal.timeout(10000)});if(response.status===304){lastContact=performance.now();if(state&&!terminal){document.body.classList.remove('tv-disconnected');text('tv-connection','Live');if(qr!==state.qr_url){qr=state.qr_url;$('tv-qr').src=qr}$('tv-qr').hidden=false;if($('tv-wifi'))$('tv-wifi').hidden=!state.wifi}return}const p=await response.json();if(response.status===410){finish(p.state);return}if(!response.ok){if(response.status===400||response.status===404)finish('invalid');else disconnected();return}if(terminal)return;etag=response.headers.get('ETag');paint(p)}catch{disconnected()}finally{pending=false;if(again){again=false;refresh()}}}
 function connect(){if(terminal||events||!window.EventSource)return;events=new EventSource(base+'/events');events.addEventListener('session',()=>{lastContact=performance.now();refresh()});events.addEventListener('ping',()=>{lastContact=performance.now();if(document.body.classList.contains('tv-disconnected'))refresh()});events.addEventListener('terminal',event=>{try{finish(JSON.parse(event.data).state)}catch{finish('ended')}});events.onerror=()=>{if(performance.now()-lastContact>12000)disconnected();refresh()}}
 function help(message){text('tv-display-help',message);$('tv-display-help').hidden=!message}
 function focusView(message='Focus view is on. This browser keeps its toolbars; rotate to landscape for more room.'){
  const enabled=document.body.classList.toggle('tv-focus');text('tv-fullscreen',enabled?'Exit focus view':'Focus view');help(enabled?message:'');syncPresentation();
 }
 let fullscreenTimer=null,fullscreenPending=false;
 function fullscreenRequest(){const root=document.documentElement;return root.requestFullscreen||root.webkitRequestFullscreen||root.webkitRequestFullScreen}
 function fullscreenActive(){return !!(document.fullscreenElement||document.webkitFullscreenElement||document.webkitCurrentFullScreenElement)}
 function fullscreenSettled(){clearTimeout(fullscreenTimer);fullscreenTimer=null;fullscreenPending=false;$('tv-fullscreen').disabled=false}
 function fullscreenFallback(){fullscreenSettled();if(!document.body.classList.contains('tv-focus'))focusView('Fullscreen is unavailable in this browser. Focus view keeps the QR visible, but cannot hide browser or device controls.');keepAwake()}
 function fullscreen(){
  if(fullscreenPending)return;
  const root=document.documentElement,request=fullscreenRequest(),exit=document.exitFullscreen||document.webkitExitFullscreen||document.webkitCancelFullScreen;
  if(fullscreenActive()){try{const result=exit?.call(document);result?.catch?.(()=>help('Could not leave fullscreen. Use your browser’s exit control.'))}catch{help('Could not leave fullscreen. Use your browser’s exit control.')}return}
  if(document.body.classList.contains('tv-focus')){focusView();syncFullscreen();return}
  if(!request){focusView();keepAwake();return}
  fullscreenPending=true;$('tv-fullscreen').disabled=true;
  // Try the API inside the tap. Older WebKit can omit the enabled flag and return
  // no promise; its fullscreenchange event remains authoritative.
  try{const result=request===root.requestFullscreen?request.call(root,{navigationUI:'hide'}):request.call(root);fullscreenTimer=setTimeout(()=>{if(fullscreenActive())syncFullscreen();else fullscreenFallback()},1500);result?.then?.(()=>{if(fullscreenActive())syncFullscreen()}).catch?.(()=>{if(fullscreenActive())syncFullscreen();else fullscreenFallback()});keepAwake()}catch{fullscreenFallback()}
 }
 let presentationTimer=null,presentationKeyboard=false;
 function showPresentationControls(event){
  if(!document.body.classList.contains('tv-presentation'))return;
  if(event?.type==='keydown'){if(event.key==='Escape')return;presentationKeyboard=true}else if(event)presentationKeyboard=false;
  clearTimeout(presentationTimer);document.body.classList.add('tv-controls-visible');
  if(!presentationKeyboard)presentationTimer=setTimeout(()=>document.body.classList.remove('tv-controls-visible'),3000);
 }
 function syncPresentation(){const enabled=fullscreenActive()||document.body.classList.contains('tv-focus'),wasEnabled=document.body.classList.contains('tv-presentation');document.body.classList.toggle('tv-presentation',enabled);if(enabled&&!wasEnabled){presentationKeyboard=false;showPresentationControls()}else if(!enabled){clearTimeout(presentationTimer);presentationKeyboard=false;document.body.classList.remove('tv-controls-visible')}}
 function syncFullscreen(){const active=fullscreenActive();if(active){fullscreenSettled();document.body.classList.remove('tv-focus');help('')}text('tv-fullscreen',active?'Exit fullscreen':document.body.classList.contains('tv-focus')?'Exit focus view':fullscreenRequest()?'Fullscreen':'Focus view');syncPresentation()}
 function wakeStatus(message,held=false){text('tv-awake-status',message);text('tv-awake',held?'Screen awake ✓':'Keep screen awake');$('tv-awake').disabled=held||terminal}
 async function releaseWake(){const lock=wakeSentinel;wakeSentinel=null;if(lock&&!lock.released)await lock.release().catch(()=>{})}
 function keepAwake(){
  if(terminal||document.hidden)return Promise.resolve();if(wakeSentinel&&!wakeSentinel.released){wakeStatus('Screen stays awake while this display is open.',true);return Promise.resolve()}if(wakePending)return wakePending;
  if(!navigator.wakeLock?.request){wakeStatus('This browser cannot prevent sleep. On the display device, set Settings → Display & Brightness → Auto-Lock → Never for the party.');return Promise.resolve()}
  wakeStatus('Keeping the screen awake…');wakePending=(async()=>{try{const lock=await navigator.wakeLock.request('screen');if(terminal||document.hidden){await lock.release();return}wakeSentinel=lock;lock.addEventListener('release',()=>{if(wakeSentinel!==lock)return;wakeSentinel=null;if(!terminal)wakeStatus(document.hidden?'Screen protection pauses while this page is hidden.':'Screen protection ended. Tap Keep screen awake to retry.')});wakeStatus('Screen stays awake while this display is open.',true)}catch{wakeStatus('Screen sleep is not blocked. Keep this page visible, turn off Low Power Mode if enabled, then tap Keep screen awake.')}finally{wakePending=null}})();return wakePending;
 }
 $('tv-fullscreen').onclick=fullscreen;$('tv-awake').onclick=keepAwake;$('tv-print').onclick=()=>{if(!terminal&&lastContact&&performance.now()-lastContact<12000)window.print()};$('tv-qr').onerror=()=>{qr='';disconnected()};
 for(const event of ['pointerdown','pointermove','keydown'])document.addEventListener(event,showPresentationControls);
 document.addEventListener('fullscreenchange',syncFullscreen);document.addEventListener('webkitfullscreenchange',syncFullscreen);syncFullscreen();
 const wake=()=>{refresh();connect();keepAwake()};window.addEventListener('offline',disconnected);window.addEventListener('online',wake);window.addEventListener('pageshow',wake);window.addEventListener('focus',()=>keepAwake());window.addEventListener('pagehide',()=>releaseWake());document.addEventListener('visibilitychange',()=>{if(!document.hidden)wake();else{releaseWake();wakeStatus('Screen protection pauses while this page is hidden.')}});
 if(!/^[A-Za-z0-9_-]{32}$/.test(token)){finish('invalid');return}wake();setInterval(tick,250);setInterval(refresh,10000);
})();

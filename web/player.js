/* Presentation only: both player sizes control the same persistent audio element. */
'use strict';
window.ArrifyPlayer=(()=>{
  const $=s=>document.querySelector(s);
  const paths={
    play:'<path d="m9 5 11 7-11 7z" fill="currentColor" stroke="none"/>',
    pause:'<path d="M8 5v14M16 5v14" stroke-width="4"/>',
    next:'<path d="m5 5 10 7-10 7z" fill="currentColor" stroke="none"/><path d="M19 5v14"/>',
    previous:'<path d="m19 5-10 7 10 7z" fill="currentColor" stroke="none"/><path d="M5 5v14"/>',
    shuffle:'<path d="M3 6h3c5 0 7 12 12 12h3M3 18h3c2.5 0 4-3 5-6m2-3c1.5-2 3-3 5-3h3m-3-3 3 3-3 3m0 6 3 3-3 3"/>',
    repeat:'<path d="m17 2 4 4-4 4M3 11V8a2 2 0 0 1 2-2h16M7 22l-4-4 4-4m14-1v3a2 2 0 0 1-2 2H3"/>',
    queue:'<path d="M3 5h18M3 11h12M3 17h9m5-3 5 3-5 3z"/>',
    expand:'<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>',
    close:'<path d="m5 9 7 7 7-7"/>',
    output:'<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4"/>',
    phone:'<rect x="6" y="2" width="12" height="20" rx="2.5"/><path d="M10 18h4"/>',
    tablet:'<rect x="3" y="2" width="18" height="20" rx="2.5"/><path d="M11 18h2"/>',
    volume:'<path d="M4 9h4l5-4v14l-5-4H4zM17 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/>',
    like:'<circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/>'
  };
  const icon=name=>'<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'+(paths[name]||paths.play)+'</svg>';
  const dockIcon=name=>'<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">'+({play:'<path d="M7 3v18l15-9z"/>',pause:'<path d="M7 4h4v16H7zm6 0h4v16h-4z"/>',previous:'<path d="M20 4v16L7 12zM3 4h3v16H3z"/>',next:'<path d="M4 4v16l13-8zM18 4h3v16h-3z"/>'}[name]||'')+'</svg>';
  const likeIcon=selected=>'<svg class="like-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/>'+(selected?'<path d="m8 12 2.5 2.5L16 9"/>':'<path d="M12 8v8M8 12h8"/>')+'</svg>';
  function paintLike(button,selected){if(!button)return;const key=selected?'liked':'unliked';if(button.dataset.likeState!==key||!button.querySelector('.like-icon')){button.innerHTML=likeIcon(selected);button.dataset.likeState=key}toggleClass(button,'is-liked',selected);setAttr(button,'aria-pressed',String(selected));setAttr(button,'aria-label',selected?'Unlike current song':'Like current song');setUI(button,'title',selected?'Remove from Liked songs':'Add to Liked songs')}
  let options,dialog,lastQueue='',lastTrack=null,previousFocus,previewing=false;
  function tab(queue){
    setQueueSheet(dialog,queue);$('#np-listen').hidden=false;$('#np-queue').hidden=!queue;
    $('.np-view-nav').hidden=!queue;
    if(queue&&dialog?.open)$('#np-back-playing').focus({preventScroll:true});
  }
  function open(queue=false){
    if(!options)return;
    $('#close-queue').click();
    tab(queue);sync();previousFocus=document.activeElement;
    if(!dialog.open)dialog.showModal();
    document.body.classList.add('player-expanded');
    for(const id of ['now-open'])$('#'+id).setAttribute('aria-expanded','true');
  }
  function close(){dialog.close()}
  let lastLayoutTrack;
  const renderedHTML=new WeakMap();
  function setUI(node,key,value){
    if(key==='innerHTML'){const old=renderedHTML.get(node);if(old?.value===value&&old.first===node.firstChild&&old.last===node.lastChild)return;node.innerHTML=value;renderedHTML.set(node,{value,first:node.firstChild,last:node.lastChild});return}
    if(node[key]!==value)node[key]=value;
  }
  function setAttr(node,key,value){if(node.getAttribute(key)!==String(value))node.setAttribute(key,String(value))}
  function toggleClass(node,key,value){if(node.classList.contains(key)!==!!value)node.classList.toggle(key,!!value)}
  function sync(){
    if(!options||document.hidden)return;
    const state=options.getState(),t=state?.track,audio=options.getAudio(),isParty=!!(state?.party_name||state?.party_id);
    toggleClass(dialog,'party-player',isParty);setUI($('#shuffle'),'hidden',isParty);setUI($('#shuffle'),'disabled',isParty||!!(state?.channel||(state?.station&&state.station.mode!=='mix')));
    setUI($('#np-add-playlist'),'hidden',isParty);
    if(previewing&&!state?.party_preview)options.restoreTimeline?.();previewing=!!state?.party_preview;
    const listening=options.getListening?.()||{},remote=Boolean(listening.remote),session=listening.session;
    const playing=!state?.party_preview&&(remote?listening.playing:Boolean(t&&(!audio.paused||options.isStarting?.())));
    setUI($('#now-art'),'hidden',!t);setUI($('#now-like'),'hidden',!t||isParty);if(t&&$('#now-art').getAttribute('src')!==t.artwork)$('#now-art').src=t.artwork;setUI($('#now-title'),'textContent',t?.title||'');setUI($('#now-artist'),'textContent',t?.artist||'');setUI($('#play-pause'),'disabled',!t);
    setUI($('#next'),'disabled',!!state?.party_preview||!state?.upcoming?.length);setUI($('#previous'),'disabled',!!state?.party_preview||!state?.history?.length);
    setUI($('#np-clear-queue'),'hidden',isParty);setUI($('#np-edit-queue'),'hidden',isParty);
    const canRepeat=!isParty&&!state?.channel&&(!state?.station||state.station.mode==='mix'),repeat=state?.repeat||'off';
    setUI($('#np-repeat'),'hidden',!canRepeat);setUI($('#np-repeat'),'disabled',!t);setUI($('#np-repeat'),'innerHTML',icon('repeat')+(repeat==='one'?'<span class="repeat-one">1</span>':''));setAttr($('#np-repeat'),'aria-label','Repeat '+repeat+'. Change repeat mode');setAttr($('#np-repeat'),'aria-pressed',String(repeat!=='off'));
    if(canRepeat&&repeat!=='off'&&t)setUI($('#next'),'disabled',false);
    if(state?.party_preview){setUI($('#seek'),'disabled',true);$('#seek').value=0;setUI($('#elapsed'),'textContent','0:00');setUI($('#duration'),'textContent','0:00');$('#dock-progress').style.setProperty('--progress','0%')}
    setUI($('#play-pause'),'innerHTML',dockIcon(playing?'pause':'play'));
    setAttr($('#play-pause'),'aria-label',playing?'Pause':'Play');
    dialog.querySelectorAll('[data-control]').forEach(b=>{
      const control=b.dataset.control;
      const name=control==='play-pause'?(playing?'pause':'play'):control==='now-like'?'like':control==='output-button'?'output':control;
      const content=icon(name)+(control==='shuffle'&&b.closest('.queue-mode-controls')?'<span>Shuffle</span>':'');
      if(control==='output-button'){const route=remote?{wireless:Boolean(session.online),host:session.device_name,label:session.output==='AirPlay'?'AirPlay':session.device_name,kind:session.output==='AirPlay'?'airplay':/iPad/.test(session.device_name)?'tablet':/iPhone|Android/.test(session.device_name)?'phone':'output'}:window.ArrifyOutput.describe(audio);setUI(b,'innerHTML',(route.kind==='airplay'?window.ArrifyOutput.symbol(true):icon(route.kind))+'<span></span>');setUI(b.querySelector('span'),'textContent',route.label);toggleClass(b,'output-connected',route.wireless);setAttr(b,'aria-label','Playback device: '+route.label+'. Open device options.');setUI(b,'title','Playback hosted by '+route.host+'. Open device options.')}
      else if(control==='now-like')paintLike(b,Boolean(t&&options.getLiked().has(t.id)));
      else setUI(b,'innerHTML',content);
      if(control==='play-pause')setAttr(b,'aria-label',playing?'Pause':'Play');
      setUI(b,'hidden',isParty&&['shuffle','now-like'].includes(control));
      setUI(b,'disabled',isParty&&['shuffle','now-like'].includes(control)|| (control==='now-like'||control==='play-pause'?!t:Boolean($('#'+control)?.disabled)));
    });
    setUI($('#np-add-playlist'),'disabled',!t||isParty);
    const modes=dialog.querySelector('.queue-mode-controls');if(modes){modes.hidden=isParty||editingQueue;const r=modes.querySelector('.queue-repeat');setUI(r,'disabled',!canRepeat||!t);setAttr(r,'aria-pressed',String(repeat!=='off'));setUI(r,'innerHTML',icon('repeat')+'<span>Repeat '+repeat+'</span>');const sh=modes.querySelector('[data-control="shuffle"]');setUI(sh,'innerHTML',icon('shuffle')+'<span>Shuffle</span>');}
    setUI($('#np-title'),'textContent',t?.title||(state?.party_name?'':'The needle’s up.'));
    setUI($('#np-artist'),'textContent',t?.artist||(state?.party_name?'':'Pick a record.'));
    setAttr($('#np-artist'),'href',t?.artist_id?'#artists/'+t.artist_id:'#artists');
    setUI($('#np-album'),'textContent',t?.album||'');
    setAttr($('#np-album'),'href',t?.album_id?'#albums/'+t.album_id:'#albums');
    paintLike($('#now-like'),Boolean(t&&options.getLiked().has(t.id)));
    toggleClass($('#np-like'),'is-liked',Boolean(t&&options.getLiked().has(t.id)));
    setAttr($('#np-like'),'aria-pressed',String(Boolean(t&&options.getLiked().has(t.id))));
    setAttr($('#np-like'),'aria-label',t&&options.getLiked().has(t.id)?'Unlike current song':'Like current song');
    if(t?.id!==lastTrack){
      lastTrack=t?.id;$('.np-metadata').scrollTop=0;setUI($('#np-cover'),'hidden',!t);setUI($('#np-backdrop'),'hidden',!t);setUI($('#np-placeholder'),'hidden',Boolean(t));
      if(t){$('#np-cover').src=t.artwork;$('#np-backdrop').src=t.artwork;$('#np-cover').alt=t.album+' album artwork'}
      else{$('#np-cover').removeAttribute('src');$('#np-backdrop').removeAttribute('src')}
    }
    const channel=state?.channel&&options.getHome()?.collections.find(p=>p.slug===state.channel);
    setUI($('#np-heading'),'textContent',state?.party_name||state?.station?.name||(channel?channel.name:t?.album||'Your music'));

    setUI($('#np-next-song'),'textContent',state?.next?state.next.title+' · '+state.next.artist:'The queue is yours to fill.');
    const profile=remote?(session.profile||'original'):(audio.dataset.profile||$('#profile').value),source=(remote?session.quality||t?.quality:t?.quality)||'Quality unavailable';
    const quality=!remote&&options.getTransport?.().gapless?'PCM · 44.1 kHz':profile==='aac'?'AAC · 192 kbps':profile==='mp3'?'MP3 · 192 kbps':profile==='flac'?'FLAC stream':source==='Lossless'?'Lossless':source;
    const streaming=!state?.party_preview&&(remote?Boolean(t&&session.online&&session.playing&&session.observed==='playing'&&session.ack===session.intent):Boolean(t&&!audio.paused&&!audio.ended&&!audio.error&&audio.readyState>=2&&audio.dataset.trackId===String(t.id)));
    setUI($('#np-playback-settings'),'hidden',!streaming);
    setUI($('#quality'),'hidden',!streaming);
    setUI($('#np-quality-label'),'textContent',streaming?quality:'');
    toggleClass($('#np-playback-settings'),'is-lossless',streaming&&quality==='Lossless');
    toggleClass($('#quality'),'is-lossless',streaming&&quality==='Lossless');
    setUI($('#np-quality-detail'),'textContent',(t?'Source: '+source+'. ':'')+(profile==='aac'||profile==='mp3'?'Playing '+quality+'. ':'')+'AirPlay output quality is managed by iOS.');
    const route=remote?{apple:false,wireless:true}:window.ArrifyOutput.describe(audio);setUI($('.np-volume'),'hidden',route.apple||route.wireless);setUI($('#np-volume'),'disabled',route.apple||route.wireless);setUI($('#np-volume-help'),'hidden',!route.apple&&!route.wireless);setUI($('#np-volume-help'),'textContent',route.apple?'Use the output picker or Control Center for volume when supported by your speaker. Otherwise, use the speaker’s app or remote.':route.wireless?'Adjust volume on the connected speaker or in its app.':'Use your device’s volume controls.');
    $('#np-profile').value=remote?profile:$('#profile').value;setUI($('#np-profile'),'disabled',remote);
    if(!options.isScrubbing?.()&&document.activeElement!==$('#np-seek'))$('#np-seek').value=$('#seek').value;
    setUI($('#np-seek'),'disabled',!t||$('#seek').disabled);
    setUI($('#np-elapsed'),'textContent',$('#elapsed').textContent);setUI($('#np-duration'),'textContent',$('#duration').textContent);
    $('#np-volume').value=audio.volume;
    if(t?.id!==lastLayoutTrack){lastLayoutTrack=t?.id;fitArtwork()}
    const signature=JSON.stringify([state?.current,state?.upcoming?.slice(0,250),state?.channel,state?.station,state?.repeat,state?.party_id,state?.queue]);
    if(signature!==lastQueue){
      lastQueue=signature;
      selectedQueue.clear();renderSheetQueue(state);
    }
  }

  let editingQueue=false,selectedQueue=new Set(),queueBusy=false,draggingQueue=false;
  const escapeText=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function queueAction(data){
    if(queueBusy)return;queueBusy=true;$('#np-queue').setAttribute('aria-busy','true');
    try{await options.queueCommand(data);selectedQueue.clear();lastQueue='';sync()}
    catch(error){options.notify?.(error.message||'Could not update the queue.');lastQueue='';sync()}
    finally{queueBusy=false;$('#np-queue').removeAttribute('aria-busy')}
  }
  function editQueue(order,expected){return queueAction({action:'queue_edit',order,expected_upcoming:expected})}
  function renderSheetQueue(state){
    if(draggingQueue)return;
    const t=state?.track,party=!!(state?.party_name||state?.party_id);if(party)editingQueue=false;
    $('#np-edit-queue').textContent=editingQueue?'Done':'Edit';$('#np-queue .np-queue-heading h2').textContent=editingQueue?'Edit Queue':'Queue';
    $('#np-queue').classList.toggle('queue-edit-mode',editingQueue);const modes=dialog.querySelector('.queue-mode-controls');if(modes)modes.hidden=party||editingQueue;
    const rows=(state?.queue||[]).map((track,i)=>'<div class="sheet-track" data-queue-index="'+i+'">'+(editingQueue?'<input type="checkbox" data-queue-select="'+i+'" aria-label="Select '+escapeText(track.title)+'" '+(selectedQueue.has(i)?'checked':'')+'>':'')+'<img src="'+escapeText(track.artwork)+'" alt=""><div class="sheet-track-copy"><strong>'+escapeText(track.title)+'</strong><small>'+escapeText(track.artist)+'</small></div>'+(!party?'<button class="queue-drag" data-queue-drag="'+i+'" aria-label="Reorder '+escapeText(track.title)+'. Use arrow keys to move." title="Drag to reorder; arrow keys also work">≡</button>':'')+'</div>').join('');
    $('#np-queue-items').innerHTML=(t?'<div class="sheet-track sheet-current"><img src="'+escapeText(t.artwork)+'" alt=""><div class="sheet-track-copy"><strong>'+escapeText(t.title)+'</strong><small>'+escapeText(t.artist)+'</small></div><button data-control="play-pause" aria-label="Play or pause current song">'+icon(options.getAudio().paused?'play':'pause')+'</button></div>':'')+'<p class="queue-label">COMING UP</p>'+rows+(!rows?'<p class="muted">Your queue is clear.</p>':'');
    let actions=$('#np-queue-edit-actions');if(!actions){actions=document.createElement('div');actions.id='np-queue-edit-actions';actions.className='queue-edit-actions';$('#np-queue').append(actions)}
    actions.hidden=!editingQueue;actions.innerHTML='<button class="chip" data-queue-move-selection '+(!selectedQueue.size?'disabled':'')+'>Move up</button><button class="chip" data-queue-move-down '+(!selectedQueue.size?'disabled':'')+'>Move down</button><button class="chip" data-queue-remove-selection '+(!selectedQueue.size?'disabled':'')+'>Remove'+(selectedQueue.size?' ('+selectedQueue.size+')':'')+'</button>';
  }
  function setQueueSheet(target,open){
    target.classList.toggle('queue-sheet-open',open);
    if(!open){target.dispatchEvent(new Event('queue-sheet-close'));target.querySelector('.np-queue').classList.remove('sheet-expanded')}
    const context=target.querySelector('.queue-context strong');if(context)context.textContent=target.querySelector('.np-header h2').textContent;
    const listen=target.querySelector('.np-listen');listen.hidden=false;listen.inert=open;
    target.querySelector('.np-header').inert=open;target.querySelector('.np-status-bar').inert=open;
  }
  function setupQueueSheet(target){
    const sheet=target.querySelector('.np-queue'),nav=target.querySelector('.np-view-nav');
    if(!sheet.querySelector('.queue-context')){const context=document.createElement('p');context.className='queue-context';context.append('Playing ',document.createElement('strong'));sheet.querySelector('.np-queue-heading').after(context)}
    sheet.prepend(nav);nav.querySelector('button').innerHTML='<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m6 6 12 12M18 6 6 18"/></svg>';nav.querySelector('button').setAttribute('aria-label','Close queue');
    const grip=document.createElement('button');grip.className='queue-sheet-grip';grip.setAttribute('aria-label','Close queue. Drag down to dismiss.');grip.innerHTML='<span></span>';sheet.prepend(grip);
    const dismiss=()=>{if(target===dialog)tab(false);else{const provider=target.id.startsWith('apple')?'apple':'spotify';target.querySelector('[data-'+provider+'-back]').click()}};
    let gesture=null,suppressGripClick=false;
    grip.onclick=()=>{if(suppressGripClick){suppressGripClick=false;return}dismiss()};
    grip.onpointerdown=e=>{if(e.button!==0)return;gesture={y:e.clientY,dy:0,height:sheet.getBoundingClientRect().height};sheet.classList.add('sheet-dragging');grip.setPointerCapture(e.pointerId)};
    grip.onpointermove=e=>{if(!gesture)return;gesture.dy=e.clientY-gesture.y;if(Math.abs(gesture.dy)>5)suppressGripClick=true;if(gesture.dy>=0){sheet.style.setProperty('--sheet-drag',gesture.dy+'px')}else{sheet.style.height=Math.min(target.clientHeight*.9,gesture.height-gesture.dy)+'px'}};
    const endGesture=cancel=>{if(!gesture)return;const dy=gesture.dy;gesture=null;sheet.classList.remove('sheet-dragging');sheet.style.removeProperty('--sheet-drag');sheet.style.removeProperty('height');if(cancel)return;if(dy < -50)sheet.classList.add('sheet-expanded');else if(dy>80){if(sheet.classList.contains('sheet-expanded'))sheet.classList.remove('sheet-expanded');else dismiss()}};
    grip.onpointerup=()=>endGesture(false);grip.onpointercancel=()=>endGesture(true);grip.onlostpointercapture=()=>endGesture(true);
    target.addEventListener('cancel',event=>{if(!sheet.hidden){event.preventDefault();dismiss()}});
    target.addEventListener('close',()=>setQueueSheet(target,false));
    target.addEventListener('queue-sheet-close',()=>endGesture(true));
    if(target!==dialog)return;
    const modes=document.createElement('div');modes.className='queue-mode-controls';modes.innerHTML='<button data-control="shuffle">'+icon('shuffle')+'<span>Shuffle</span></button><button class="queue-repeat">'+icon('repeat')+'<span>Repeat off</span></button>';sheet.append(modes);
    modes.querySelector('.queue-repeat').onclick=()=>$('#np-repeat').click();
    sheet.addEventListener('change',e=>{if(e.target.dataset.queueSelect===undefined)return;const i=Number(e.target.dataset.queueSelect);if(e.target.checked)selectedQueue.add(i);else selectedQueue.delete(i);const actions=$('#np-queue-edit-actions');for(const b of actions.querySelectorAll('button'))b.disabled=!selectedQueue.size;actions.querySelector('[data-queue-remove-selection]').textContent='Remove'+(selectedQueue.size?' ('+selectedQueue.size+')':'')});
    sheet.addEventListener('click',e=>{const remove=e.target.closest('[data-queue-remove-selection]'),move=e.target.closest('[data-queue-move-selection]');const down=e.target.closest('[data-queue-move-down]');if(!remove&&!move&&!down)return;const expected=[...options.getState().upcoming],order=expected.map((_,i)=>i);if(remove)return editQueue(order.filter(i=>!selectedQueue.has(i)),expected);if(down){for(let i=order.length-2;i>=0;i--)if(selectedQueue.has(order[i])&&!selectedQueue.has(order[i+1]))[order[i+1],order[i]]=[order[i],order[i+1]]}else{for(let i=1;i<order.length;i++)if(selectedQueue.has(order[i])&&!selectedQueue.has(order[i-1]))[order[i-1],order[i]]=[order[i],order[i-1]]}editQueue(order,expected)});
    sheet.addEventListener('keydown',e=>{const handle=e.target.closest('[data-queue-drag]');if(!handle||!['ArrowUp','ArrowDown'].includes(e.key))return;e.preventDefault();const expected=[...options.getState().upcoming],from=Number(handle.dataset.queueDrag),to=from+(e.key==='ArrowUp'?-1:1);if(to<0||to>=expected.length)return;const order=expected.map((_,i)=>i);order.splice(to,0,order.splice(from,1)[0]);editQueue(order,expected).then(()=>sheet.querySelector('[data-queue-drag="'+to+'"]')?.focus())});
    let drag=null,dragFrame=0;
    const frame=now=>{
      if(!drag)return;const d=drag,list=$('#np-queue-items'),rect=list.getBoundingClientRect(),dt=Math.min(32,now-(d.time||now));d.time=now;
      const edge=48,speed=d.y<rect.top+edge?-Math.min(1,(rect.top+edge-d.y)/edge):d.y>rect.bottom-edge?Math.min(1,(d.y-rect.bottom+edge)/edge):0;
      if(speed)list.scrollTop+=speed*dt*.55;
      const delta=d.y-d.startY+list.scrollTop-d.scroll;
      const center=d.centers[d.from]+delta;
      d.to=d.centers.reduce((best,c,i)=>Math.abs(c-center)<Math.abs(d.centers[best]-center)?i:best,0);
      d.rows.forEach((row,i)=>{const shift=i===d.from?delta:d.to>d.from&&i>d.from&&i<=d.to?-d.height:d.to<d.from&&i>=d.to&&i<d.from?d.height:0;row.style.transform='translateY('+shift+'px)'});
      dragFrame=requestAnimationFrame(frame);
    };
    sheet.addEventListener('pointerdown',e=>{const handle=e.target.closest('[data-queue-drag]');if(!handle||queueBusy||drag||e.button!==0)return;const row=handle.closest('.sheet-track'),rows=[...sheet.querySelectorAll('[data-queue-index]')],list=$('#np-queue-items');drag={handle,row,rows,centers:rows.map(r=>r.offsetTop+r.offsetHeight/2),height:row.offsetHeight,from:Number(handle.dataset.queueDrag),to:Number(handle.dataset.queueDrag),expected:[...options.getState().upcoming],startY:e.clientY,y:e.clientY,scroll:list.scrollTop};draggingQueue=true;handle.setPointerCapture(e.pointerId);row.classList.add('is-dragging');sheet.classList.add('sorting-queue');dragFrame=requestAnimationFrame(frame);});
    sheet.addEventListener('pointermove',e=>{if(drag)drag.y=e.clientY});
    const finish=cancel=>{if(!drag)return;const d=drag;drag=null;cancelAnimationFrame(dragFrame);draggingQueue=false;d.rows.forEach(row=>row.style.removeProperty('transform'));d.row.classList.remove('is-dragging');sheet.classList.remove('sorting-queue');if(!cancel&&d.from!==d.to){const order=d.expected.map((_,i)=>i);order.splice(d.to,0,order.splice(d.from,1)[0]);const ordered=[...d.rows];ordered.splice(d.to,0,ordered.splice(d.from,1)[0]);ordered.forEach(row=>$('#np-queue-items').append(row));editQueue(order,d.expected)}else{lastQueue='';sync()}};
    sheet.addEventListener('pointerup',e=>{if(drag){drag.y=e.clientY;cancelAnimationFrame(dragFrame);frame(performance.now())}finish(false)});sheet.addEventListener('pointercancel',()=>finish(true));sheet.addEventListener('lostpointercapture',()=>finish(true));
    target.addEventListener('queue-sheet-close',()=>{endGesture(true);finish(true)});window.addEventListener('blur',()=>{endGesture(true);finish(true)});document.addEventListener('visibilitychange',()=>{if(document.hidden){endGesture(true);finish(true)}});

  }
  function init(config){
    options=config;dialog=$('#now-playing-dialog');
    for(const [id,name]of Object.entries({previous:'previous',next:'next',shuffle:'shuffle','queue-button':'queue','np-close':'close','np-show-queue':'queue'}))$('#'+id).innerHTML=id==='previous'||id==='next'?dockIcon(name):icon(name);
    $('#now-open').addEventListener('click',()=>open());
    $('#np-close').addEventListener('click',close);
    $('#np-playback-settings').addEventListener('click',()=>$('#playback-options-dialog').showModal());
    document.addEventListener('arrify-output-change',sync);
    const resize=new ResizeObserver(fitArtwork);for(const element of [$('.np-shell'),$('.np-details'),$('.np-listen')])resize.observe(element);
    window.visualViewport?.addEventListener('resize',fitArtwork);window.addEventListener('resize',fitArtwork);
    $('#np-back-playing').addEventListener('click',()=>{tab(false);$('#np-show-queue').focus({preventScroll:true})});
    $('#np-show-queue').addEventListener('click',()=>tab(true));$('#np-up-next').addEventListener('click',()=>tab(true));
    setupQueueSheet(dialog);
    $('#np-repeat').addEventListener('click',()=>queueAction({action:'repeat',mode:({off:'all',all:'one',one:'off'})[options.getState()?.repeat||'off']}));
    $('#np-edit-queue').addEventListener('click',()=>{editingQueue=!editingQueue;selectedQueue.clear();renderSheetQueue(options.getState())});
    $('#np-clear-queue').addEventListener('click',()=>options.clearQueue());
    for(const id of ['np-artist','np-album'])$('#'+id).addEventListener('click',close);
    $('#np-seek').addEventListener('change',()=>{$('#seek').value=$('#np-seek').value;$('#seek').dispatchEvent(new Event('change'))});
    $('#np-volume').addEventListener('input',()=>{$('#volume').value=$('#np-volume').value;$('#volume').dispatchEvent(new Event('input'))});
    $('#np-profile').addEventListener('change',()=>{$('#profile').value=$('#np-profile').value;$('#profile').dispatchEvent(new Event('change'));sync()});
    attachAudio(config.getAudio());
    dialog.addEventListener('close',()=>{document.body.classList.remove('player-expanded');for(const id of ['now-open'])$('#'+id).setAttribute('aria-expanded','false');if(previousFocus?.isConnected)previousFocus.focus()});
    sync();
  }
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)sync()});
  function attachAudio(element){for(const event of ['play','playing','pause','ended','error','timeupdate','loadedmetadata','volumechange','emptied','webkitcurrentplaybacktargetiswirelesschanged'])element.addEventListener(event,sync);for(const event of ['connect','disconnect'])element.remote?.addEventListener?.(event,sync)}
  let layoutFrame=0;
  function fitArtwork(){if(layoutFrame)return;layoutFrame=requestAnimationFrame(()=>{
    layoutFrame=0;if(!dialog?.open)return;
    fitLayout(dialog);
  })}

  function fitLayout(target){if(!target?.open)return;
    const $=selector=>target.querySelector(selector);
    const panel=$('.np-listen'),compact=matchMedia('(max-height:500px) and (min-width:480px)').matches;
    if(compact!==target._compactLayout){target._compactLayout=compact;for(const element of [$('.np-shell'),panel,$('.np-details'),$('.np-metadata'),$('.np-song-heading')])element.scrollTop=0}
    if(panel.hidden)return;if(compact){panel.style.removeProperty('--np-art-size');return}
    const css=getComputedStyle(panel),padding=parseFloat(css.paddingTop)+parseFloat(css.paddingBottom),portrait=innerWidth<=900;
    panel.classList.toggle('np-tight',portrait&&panel.clientHeight<560);
    const outer=selector=>{const element=typeof selector==='string'?$(selector):selector,style=getComputedStyle(element);return style.display==='none'?0:element.offsetHeight+parseFloat(style.marginTop)+parseFloat(style.marginBottom)};
    const reserved=portrait?Math.min(128,Math.max(84,[...$('.np-metadata').children].reduce((sum,element)=>sum+outer(element),0)))+outer('.np-timeline')+outer('.np-transport')+outer('.np-up-next')+parseFloat(css.gap):0;
    const available=panel.clientHeight-padding-reserved,size=Math.max(64,Math.min(portrait?520:530,panel.clientWidth,available)),value=Math.floor(size)+'px';
    if(panel.style.getPropertyValue('--np-art-size')!==value)panel.style.setProperty('--np-art-size',value)
  }

 function createExternalDialog(provider,label){
  if(!['apple','spotify'].includes(provider))throw Error('Unknown music service.');
  let dialog=document.querySelector('#'+provider+'-full-player');if(dialog)return dialog;
  dialog=document.createElement('dialog');dialog.id=provider+'-full-player';dialog.className='now-playing-dialog '+provider+'-full-player';dialog.setAttribute('aria-label',label+' player');
  dialog.innerHTML=document.querySelector('#now-playing-dialog').innerHTML;
  dialog.querySelectorAll('.queue-sheet-grip,.queue-edit-actions,.queue-mode-controls').forEach(n=>n.remove());
  for(const node of dialog.querySelectorAll('[id]'))node.id=provider+'-'+node.id;
  for(const node of dialog.querySelectorAll('[aria-controls],[aria-labelledby]'))for(const attr of ['aria-controls','aria-labelledby'])if(node.hasAttribute(attr))node.setAttribute(attr,node.getAttribute(attr).split(' ').map(id=>provider+'-'+id).join(' '));
  const slot=(id,attr)=>{const node=dialog.querySelector('#'+provider+'-'+id);node.setAttribute(attr,'');return node};
  slot('np-close','data-'+provider+'-collapse').innerHTML=window.ArrifyPlayer.icon('close');
  for(const [id,attr]of Object.entries({'np-heading':'data-'+provider+'-party-name','np-title':'data-'+provider+'-title','np-artist':'data-'+provider+'-artist','np-album':'data-'+provider+'-album','np-cover':'data-'+provider+'-cover','np-notice':'data-'+provider+'-full-status','np-seek':'data-'+provider+'-seek','np-elapsed':'data-'+provider+'-position','np-duration':'data-'+provider+'-duration','np-next-song':'data-'+provider+'-up-next','np-queue-items':'data-'+provider+'-queue-items'}))slot(id,attr);
  for(const id of ['np-add-playlist','np-like','np-playback-settings','np-session-note','np-edit-queue','np-repeat'])dialog.querySelector('#'+provider+'-'+id).remove();
  dialog.querySelector('.np-queue').classList.remove('queue-edit-mode');dialog.querySelector('.np-queue-heading h2').textContent='Queue';
  const manage=slot('np-clear-queue','data-'+provider+'-party-link');manage.textContent='Manage queue';manage.hidden=false;
  slot('np-show-queue','data-'+provider+'-queue').innerHTML=window.ArrifyPlayer.icon('queue');slot('np-up-next','data-'+provider+'-queue');
  for(const name of ['listen','queue'])slot('np-'+name,'data-'+provider+'-panel').setAttribute('data-'+provider+'-panel',name);slot('np-back-playing','data-'+provider+'-back');
  for(const node of dialog.querySelectorAll('[data-control]')){const control=node.dataset.control;node.removeAttribute('data-control');if(control==='shuffle'){const spacer=document.createElement('span');spacer.className='np-transport-spacer';spacer.setAttribute('aria-hidden','true');node.replaceWith(spacer);continue}const action=({'play-pause':'toggle',previous:'previous',next:'next','output-button':'output'})[control];if(action)node.setAttribute('data-'+provider+'-'+action,'')}
  dialog.querySelector('[data-'+provider+'-seek]').closest('.np-timeline').setAttribute('data-'+provider+'-clock','');dialog.querySelector('[data-'+provider+'-artist]').removeAttribute('href');dialog.querySelector('[data-'+provider+'-album]').removeAttribute('href');
  dialog.addEventListener('close',()=>{document.body.classList.remove('apple-player-expanded');document.querySelector('[data-'+provider+'-expand]')?.focus({preventScroll:true})});
  document.body.append(dialog);setupQueueSheet(dialog);if(typeof ResizeObserver==='function'){const observer=new ResizeObserver(()=>window.ArrifyPlayer.fitLayout(dialog));for(const selector of ['.np-shell','.np-details','.np-listen'])observer.observe(dialog.querySelector(selector))}return dialog;
 }

 function externalMiniHTML(provider,label,{track:t,art='',playing=false,busy=false,local=false,wireless=false}){
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  return '<button type="button" class="apple-mini-track" data-'+provider+'-expand aria-haspopup="dialog" aria-label="Open '+esc(label)+' player"><img '+(!t?'hidden ':'')+'src="'+esc(art||'/assets/icon-192.png')+'" alt=""><div><strong>'+esc(t?.title||'')+'</strong><span>'+esc(t?.artist||'')+'</span><small>'+esc(label)+'</small></div></button><div class="apple-mini-transport"><button data-'+provider+'-output '+(!local?'disabled ':'')+'aria-label="Choose AirPlay output" title="Choose AirPlay output" class="'+(wireless?'apple-wireless':'')+'">'+(window.ArrifyOutput?.symbol?window.ArrifyOutput.symbol(wireless):'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M4 16H2V3h20v13h-2M12 13l7 8H5z"/></svg>')+'</button><button data-'+provider+'-previous aria-label="Previous '+esc(label)+' song" '+(!local||!t||busy?'disabled':'')+'><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M20 4v16L7 12zM3 4h3v16H3z"/></svg></button><button data-'+provider+'-toggle aria-label="'+(playing?'Pause':local?'Resume':'Play')+' '+esc(label)+'" '+(busy||!t?'disabled':'')+'><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">'+(playing?'<path d="M7 4h4v16H7zm6 0h4v16h-4z"/>':'<path d="M7 3v18l15-9z"/>')+'</svg></button><button data-'+provider+'-next aria-label="Next '+esc(label)+' song" '+(!local||!t||busy?'disabled':'')+'><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M4 4v16l13-8zM18 4h3v16h-3z"/></svg></button></div><div class="apple-mini-progress" aria-hidden="true"></div><div class="apple-mini-timeline"><span data-'+provider+'-position>0:00</span><input data-'+provider+'-seek type="range" min="0" max="1" value="0" step="1" aria-label="Seek '+esc(label)+' song"><span data-'+provider+'-duration>0:00</span></div>';
 }

 function renderExternalDialog(provider,label,{view,track:t,artwork,local=false,playing=false,busy=false,wireless=false,status=''}){
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const dialog=document.querySelector('#'+provider+'-full-player');if(!dialog?.open)return;if(!view){dialog.close();return}const src=artwork(t)||'/assets/icon-512.png';
  for(const image of dialog.querySelectorAll('.np-backdrop,[data-'+provider+'-cover]')){image.hidden=!t;if(t&&image.getAttribute('src')!==src)image.src=src}dialog.querySelector('#'+provider+'-np-placeholder').hidden=!!t;
  const upcoming=view.items.filter(item=>item.status==='queued'&&item.entry_id!==t?.entry_id),message=(local?status:'')||(!t?'Add songs to this party to get started.':'');
  for(const [selector,value] of Object.entries({['[data-'+provider+'-party-name]']:view.name,['[data-'+provider+'-title]']:t?.title||'Queue is empty',['[data-'+provider+'-artist]']:t?.artist||'',['[data-'+provider+'-album]']:t?.album||'',['[data-'+provider+'-full-status]']:message,['[data-'+provider+'-up-next]']:upcoming[0]?.title||'The queue is yours to fill.'}))dialog.querySelector(selector).textContent=value;
  dialog.querySelector('[data-'+provider+'-full-status]').hidden=!message;
  for(const [action,name]of [['previous','previous'],['toggle',playing?'pause':'play'],['next','next']])for(const button of dialog.querySelectorAll('[data-'+provider+'-'+action+']')){button.innerHTML=window.ArrifyPlayer.icon(name);button.disabled=!t||busy||(action!=='toggle'&&!local);button.setAttribute('aria-label',action==='toggle'?(playing?'Pause':'Play'):action==='previous'?'Previous song':'Next song')}
  const output=dialog.querySelector('[data-'+provider+'-output]');output.innerHTML=window.ArrifyPlayer.icon('volume')+'<span>'+(wireless?'AirPlay':'This device')+'</span>';output.disabled=!local;output.setAttribute('aria-label','Choose AirPlay output');output.classList.toggle('output-connected',wireless);
  const rows=dialog.querySelector('[data-'+provider+'-queue-items]'),signature=JSON.stringify([t,upcoming]);if(rows._queueKey!==signature){rows._queueKey=signature;const row=item=>'<div class="queue-track"><img src="'+esc(artwork(item)||'/assets/icon-192.png')+'" alt=""><div><strong>'+esc(item.title)+'</strong><small>'+esc(item.artist)+(item.requested_by?' · '+esc(item.requested_by):'')+'</small></div></div>';rows.innerHTML=(t?'<p class="queue-label">'+(t.status==='playing'?'PLAYING NOW':'UP FIRST')+'</p>'+row(t):'')+'<p class="queue-label">COMING UP</p>'+upcoming.map(row).join('')+(!upcoming.length?'<p class="muted">Add songs to this party to keep listening.</p>':'')}
  window.ArrifyPlayer.fitLayout(dialog);
 }
  return{setQueueSheet,init,sync,open,close,attachAudio,likeIcon,paintLike,icon,fitLayout,createExternalDialog,externalMiniHTML,renderExternalDialog};
})();

window.ArrifyPlayer.musicServiceHeading=(provider,name,connected=false)=>{const apple=provider==='apple';const mark=apple?'<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M17.05 12.54c.03 3.15 2.76 4.2 2.79 4.21-.02.07-.44 1.5-1.44 2.98-.87 1.28-1.77 2.56-3.19 2.59-1.39.03-1.84-.84-3.43-.84-1.59 0-2.09.81-3.4.87-1.37.05-2.41-1.39-3.29-2.66C3.3 17.1 1.93 12.37 3.77 9.18c.92-1.58 2.55-2.58 4.32-2.61 1.35-.03 2.62.91 3.43.91.81 0 2.33-1.13 3.93-.96.67.03 2.55.27 3.76 2.04-.1.06-2.24 1.3-2.16 3.98ZM14.43 4.8c.73-.88 1.23-2.1 1.1-3.31-1.05.04-2.31.7-3.06 1.58-.67.77-1.26 2-1.1 3.18 1.17.09 2.36-.59 3.06-1.45Z"/></svg>':'<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="11" fill="currentColor"/><g fill="none" stroke="#101b13" stroke-width="1.6" stroke-linecap="round"><path d="M5.7 8.5c4-1.2 8.4-.8 12 1.2M6.6 12c3.3-.9 6.9-.5 9.9 1.1M7.5 15.3c2.7-.6 5.2-.3 7.7 1"/></g></svg>';return '<div class="music-service-heading"><span class="music-service-mark music-service-'+provider+'">'+mark+'</span><div><h3>'+name+'</h3><span class="music-service-state'+(connected?' is-connected':'')+'">'+(connected?'Connected':'Account connection')+'</span></div></div>'};

/* Reusable account/library action sheet; mutations run only after confirmation. */
window.ArrifyPlayer.actionDialog=options=>{
 let d=document.querySelector('#action-dialog');if(d?.open){d.focus();return}if(!d){d=document.createElement('dialog');d.id='action-dialog';d.className='action-dialog';document.body.append(d)}
 const trigger=document.activeElement;d.setAttribute('aria-labelledby','action-dialog-title');d.setAttribute('aria-describedby','action-dialog-description');d.innerHTML='<button type="button" class="dialog-close" aria-label="Close dialog">×</button><h2 id="action-dialog-title"></h2><p id="action-dialog-description"></p><form novalidate><label hidden><span></span><input name="value" maxlength="120" required autocomplete="off"></label><p class="action-dialog-error" role="alert" hidden></p><div class="action-dialog-buttons"><button type="button" class="chip" data-action-cancel>Cancel</button><button type="submit" class="primary"></button></div></form>';
 d.querySelector('h2').textContent=options.title;d.querySelector('#action-dialog-description').textContent=options.description||'';const input=d.querySelector('input'),label=d.querySelector('label'),submit=d.querySelector('[type=submit]'),error=d.querySelector('[role=alert]');if(options.inputLabel){label.hidden=false;label.querySelector('span').textContent=options.inputLabel;input.value=options.value||''}else input.disabled=true;submit.textContent=options.confirmLabel||'Save';submit.classList.toggle('danger-button',!!options.danger);let busy=false;
 const close=()=>{if(!busy)d.close()};d.querySelector('.dialog-close').onclick=close;d.querySelector('[data-action-cancel]').onclick=close;d.oncancel=e=>{if(busy)e.preventDefault()};d.onclose=()=>{if(trigger?.isConnected)trigger.focus()};input.oninput=()=>{input.removeAttribute('aria-invalid');error.hidden=true};
 d.querySelector('form').onsubmit=async event=>{event.preventDefault();if(busy)return;const value=input.value.trim();if(options.inputLabel&&!value){input.setAttribute('aria-invalid','true');error.textContent='Enter a playlist name.';error.hidden=false;input.focus();return}busy=true;d.setAttribute('aria-busy','true');error.hidden=true;for(const b of d.querySelectorAll('button'))b.disabled=true;try{await options.onConfirm(value);d.close()}catch(e){error.textContent=e.message||'Could not save. Please try again.';error.hidden=false}finally{busy=false;d.removeAttribute('aria-busy');for(const b of d.querySelectorAll('button'))b.disabled=false}};
 d.showModal();if(options.inputLabel){input.focus();input.select()}else d.querySelector('[data-action-cancel]').focus();
};

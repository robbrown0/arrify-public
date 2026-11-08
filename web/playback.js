/* Two native media decks: browser-managed buffering, never an offline music cache. */
'use strict';
window.ArrifyPlayback={
  create(config){
    let active=config.element,spare=null,prepared=null,wanted=null,serial=0,busy=false,intent=false,handoffs=0;
    active.preload='auto';
    // Retain one WebKit/iOS media element for session and output continuity.
    // This native handoff remains separate from a future gapless transport.
    const stableElement=Boolean(config.stableElement||/iPhone|iPad|iPod/.test(navigator.userAgent||'')||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1)||typeof active.webkitShowPlaybackTargetPicker==='function');
    let cancelSeek=null,cancelStart=null,warmGeneration=0,warmSignature=null,warmReady=new Set(),gesturePrime=null;
    function configureAudioSession(){
      // Feature-detected: older browsers retain their normal native media session.
      try{if(navigator.audioSession)navigator.audioSession.type='playback'}catch{}
    }
    function clearPrime(){if(gesturePrime){URL.revokeObjectURL(gesturePrime);gesturePrime=null}}
    function prime(){
      // Safari requires the first play() on this element inside the actual tap,
      // before station selection waits for the server. This short PCM clip is silent.
      if(!stableElement||busy||intent||active.getAttribute('src')||remote(active)||gesturePrime)return;
      const pcm=new Uint8Array(1644),header=new DataView(pcm.buffer);
      const text=(offset,value)=>{for(let i=0;i<value.length;i++)pcm[offset+i]=value.charCodeAt(i)};
      text(0,'RIFF');header.setUint32(4,1636,true);text(8,'WAVEfmt ');header.setUint32(16,16,true);header.setUint16(20,1,true);header.setUint16(22,1,true);header.setUint32(24,8000,true);header.setUint32(28,16000,true);header.setUint16(32,2,true);header.setUint16(34,16,true);text(36,'data');header.setUint32(40,1600,true);
      const url=URL.createObjectURL(new Blob([pcm],{type:'audio/wav'}));gesturePrime=url;active.src=url;
      const finish=()=>{if(gesturePrime!==url)return;active.pause();active.removeAttribute('src');active.load();clearPrime()};
      try{configureAudioSession();Promise.resolve(active.play()).then(finish,finish)}catch{finish()}
    }
    function start(element,run){
      // WebKit can leave play() pending forever without an error event.
      return new Promise((resolve,reject)=>{
        let timer;const playing=()=>{if(run===serial&&!element.paused){clean();resolve()}};const clean=()=>{clearTimeout(timer);element.removeEventListener('playing',playing);if(cancelStart===cancel)cancelStart=null};element.addEventListener('playing',playing);
        const cancel=()=>{clean();reject(abort())};cancelStart=cancel;
        timer=setTimeout(()=>{clean();reject(Object.assign(Error('Audio did not start.'),{name:'PlaybackStartTimeout'}))},config.startupTimeoutMs||(element.dataset.profile==='original'?10000:20000));
        try{configureAudioSession();Promise.resolve(element.play()).then(()=>{clean();run===serial?resolve():reject(abort())},error=>{clean();reject(run===serial?error:abort())})}catch(error){clean();reject(error)}
      });
    }
    // Ask the server to finish compatible audio before the current song ends.
    // HEAD never advances the queue and does not create a second media element.
    function cancelWarm(){warmGeneration++;warmSignature=null}
    function warm(track,profile){
      if(!track||!intent||busy||navigator.connection?.saveData||!['aac','mp3'].includes(profile))return;
      const tracks=[track,...(config.getUpcoming?.()||[])].filter(Boolean);
      const ids=[...new Set(tracks.map(t=>t.id))].slice(0,2);
      const urls=ids.map(id=>'/api/v1/stream/'+id+'?profile='+profile+'&level='+(config.getLevel?.()||'off')+(config.streamQuery?.(id)||''));
      const signature=urls.join('|');if(warmSignature===signature)return;
      cancelWarm();warmSignature=signature;const generation=warmGeneration;
      // Sequential server preparation leaves capacity for selected-song playback.
      // Finish the current render, but never start stale queued work after a tap.
      (async()=>{for(const url of urls){
        if(generation!==warmGeneration||!intent||busy)return;
        if(warmReady.has(url))continue;
        try{const response=await fetch(url,{method:'HEAD',headers:{'X-Arrify-Prepare':'1'}});
          if(response.ok){warmReady.add(url);if(warmReady.size>12)warmReady.delete(warmReady.values().next().value)}
          else {if(generation===warmGeneration)warmSignature=null;return}
        }catch{if(generation===warmGeneration)warmSignature=null;return}
      }})();
    }
    const abort=()=>Object.assign(Error('Playback replaced'),{name:'AbortError'});
    function restore(element,offset,run){
      return new Promise((resolve,reject)=>{
        const target=()=>Number.isFinite(element.duration)&&element.duration>0?Math.min(offset,Math.max(0,element.duration-.05)):offset;
        let receiverStarted=false,finished=false;
        let timer;const clean=()=>{finished=true;clearTimeout(timer);for(const event of ['loadedmetadata','canplay','progress','webkitcurrentplaybacktargetiswirelesschanged'])element.removeEventListener(event,seek);element.removeEventListener('seeked',done);element.removeEventListener('error',failed);if(cancelSeek===cancel)cancelSeek=null};
        const cancel=()=>{clean();reject(abort())};
        const failed=error=>{clean();reject(error?.name?error:Error('Could not restore playback position.'))};
        const done=()=>{if(run!==serial)return cancel();if(Math.abs(element.currentTime-target())>.6)return;clean();resolve()};
        const seek=()=>{if(finished)return;if(run!==serial)return cancel();if(element.readyState<1)return;
          // Some AirPlay receivers reject seeks until their playback session
          // starts. Start the existing element once, then restore the checkpoint.
          if(remote(element)&&element.paused&&intent&&!receiverStarted){
            receiverStarted=true;start(element,run).then(()=>{if(!finished&&run===serial&&intent)seek()},failed);return;
          }
          try{if(Math.abs(element.currentTime-offset)<.1&&!element.seeking){clean();resolve();return}element.currentTime=target()}catch(error){if(error?.name==='NotSupportedError')failed(error)}};
        cancelSeek=cancel;for(const event of ['loadedmetadata','canplay','progress','webkitcurrentplaybacktargetiswirelesschanged'])element.addEventListener(event,seek);element.addEventListener('seeked',done);element.addEventListener('error',failed);timer=setTimeout(failed,12000);seek();
      });
    }
    const remote=element=>Boolean(element.webkitCurrentPlaybackTargetIsWireless||element.remote?.state==='connected');
    active.setAttribute('x-webkit-airplay','allow');active.disableRemotePlayback=false;
    function discard(){if(spare){spare.pause();spare.removeAttribute('src');spare.load();spare.remove()}spare=null;prepared=null}
    function prepare(track,profile){
      profile=config.streamProfile?.(profile)||profile;
      wanted=track?{track,profile}:null;
      warm(track,profile);
      if(!track||!intent||navigator.connection?.saveData||(config.getLevel?.()&&config.getLevel()!=='off')||stableElement||remote(active)){discard();return}
      if(busy)return;
      const key=track.id+':'+profile+':'+(config.getLevel?.()||'off');
      if(prepared===key&&spare&&!spare.error)return;
      discard();spare=document.createElement('audio');spare.id='audio-next';spare.preload='auto';spare.hidden=true;spare.setAttribute('aria-hidden','true');spare.setAttribute('x-webkit-airplay','deny');spare.disableRemotePlayback=true;
      spare.dataset.trackId=String(track.id);spare.dataset.profile=profile;spare.dataset.streamOffset='0';spare.src='/api/v1/stream/'+track.id+'?profile='+profile+'&level='+(config.getLevel?.()||'off')+(config.streamQuery?.(track.id)||'');prepared=key;
      document.body.append(spare);spare.load();
    }
    function activate(element){active=element;active.id='audio';active.setAttribute('x-webkit-airplay','allow');active.disableRemotePlayback=false;config.onActivate(active)}
    async function load(track,profile,offset=0,autoplay=true){
      cancelWarm();clearPrime();
      profile=config.streamProfile?.(profile)||profile;
      const run=++serial;cancelSeek?.();cancelStart?.();busy=true;intent=autoplay;if(wanted?.track.id===track.id&&wanted.profile===profile)wanted=null;
      const old=active,volume=old.volume,muted=old.muted,rate=old.playbackRate,sink=old.sinkId;
      let candidate=null;
      if(!offset&&!remote(old)&&spare&&!spare.error&&prepared===track.id+':'+profile+':'+(config.getLevel?.()||'off')){candidate=spare;spare=null;prepared=null}
      else discard();
      // Replacing the source already stops the previous resource. Avoid an
      // extra pause command on the continuous WebKit/AirPlay session: that
      // unnecessarily tells the output to pause just before the next song.
      if(candidate||!autoplay||(!stableElement&&!remote(old)))old.pause();
      const url='/api/v1/stream/'+track.id+'?profile='+profile+'&level='+(config.getLevel?.()||'off')+(['flac','pcm'].includes(profile)?'&start='+offset:'')+(config.streamQuery?.(track.id)||'');
      try{
        if(candidate){
          old.id='audio-retired';candidate.volume=volume;candidate.muted=muted;candidate.playbackRate=rate;activate(candidate);
          if(sink&&candidate.setSinkId)try{await candidate.setSinkId(sink)}catch{config.onNotice?.('Choose your audio output again.')}
        }else{
          old.dataset.trackId=String(track.id);old.dataset.profile=profile;old.dataset.streamOffset=String(['flac','pcm'].includes(profile)?offset:0);old.src=url;old.load();
          if(offset&&! ['flac','pcm'].includes(profile))await restore(old,offset,run);
        }
        if(run!==serial)throw abort();
        if(autoplay&&intent){
          try{await start(active,run)}
          catch(error){
            // Some mobile browsers allow only the media element started by a
            // user gesture. Reuse that element if a prepared deck is refused.
            if(candidate&&error.name==='NotAllowedError'&&run===serial){
              candidate.pause();candidate.removeAttribute('src');candidate.load();candidate.id='audio-unused';candidate.remove();
              activate(old);old.dataset.trackId=String(track.id);old.dataset.profile=profile;old.dataset.streamOffset=String(['flac','pcm'].includes(profile)?offset:0);old.src=url;old.load();if(offset&&! ['flac','pcm'].includes(profile))await restore(old,offset,run);if(run===serial&&intent)await start(old,run);candidate=null;
            }else throw error;
          }
        }
        if(run!==serial)throw abort();
        if(candidate){handoffs++;old.removeAttribute('src');old.load();old.remove()}
      }catch(error){if(run!==serial)throw abort();throw error}finally{
        if(candidate&&old!==active){old.removeAttribute('src');old.load();old.remove()}
        if(run===serial){busy=false;if(wanted)prepare(wanted.track,wanted.profile)}
      }
    }
    function pause(){cancelWarm();intent=false;cancelStart?.();active.pause()}
    async function resume(){intent=true;if(busy)return;await start(active,serial);if(wanted)prepare(wanted.track,wanted.profile)}
    function stop(){cancelWarm();++serial;cancelSeek?.();cancelStart?.();intent=false;busy=false;discard();active.pause();active.removeAttribute('src');active.load();clearPrime()}
    return{load,prepare,pause,resume,stop,prime,cancelPendingStart:()=>{if(busy){cancelSeek?.();cancelStart?.()}},get priming(){return Boolean(gesturePrime)},observePlaying:()=>{if(active.paused||active.ended||active.error||!active.src)return false;intent=true;return true},interrupt:()=>{intent=false},get active(){return active},get loading(){return busy},get intent(){return intent},
      snapshot:()=>({current:active.dataset.trackId||null,prepared:spare?.dataset.trackId||null,readyState:spare?.readyState||0,handoffs,loading:busy,stableElement})};
  }
};

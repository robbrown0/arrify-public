/* Output routing stays in the browser/OS. No microphone access is requested. */
'use strict';
window.ArrifyOutput={create({getAudio}){
 const $=s=>document.querySelector(s),dialog=$('#output-dialog'),attached=new WeakSet();let version=0;
 const apple=()=>/iPhone|iPad|iPod/.test(navigator.userAgent)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
 function picker(audio){if(typeof audio.webkitShowPlaybackTargetPicker==='function')return'airplay';if(typeof audio.setSinkId==='function'&&typeof navigator.mediaDevices?.selectAudioOutput==='function')return'speakers';if(typeof audio.remote?.prompt==='function')return'remote';return null}
 function status(){const a=getAudio(),wireless=Boolean(a.webkitCurrentPlaybackTargetIsWireless||a.remote?.state==='connected');for(const b of document.querySelectorAll('#output-button,[data-control="output-button"]')){b.classList.toggle('output-connected',wireless||Boolean(a.sinkId&&a.sinkId!=='default'));b.title=wireless?'Audio output · wireless':'Choose audio output'}document.dispatchEvent(new Event('arrify-output-change'))}
 function attachAudio(audio){audio.setAttribute('x-webkit-airplay','allow');audio.disableRemotePlayback=false;if(attached.has(audio))return;attached.add(audio);for(const event of ['webkitcurrentplaybacktargetiswirelesschanged','webkitplaybacktargetavailabilitychanged','playing','loadedmetadata'])audio.addEventListener(event,status);for(const name of ['connect','disconnect'])audio.remote?.addEventListener?.(name,status);status()}
 function helpText(){return apple()?'On iPhone or iPad, open Control Center, touch and hold the music controls, then tap AirPlay to choose a speaker or headphones.':!window.isSecureContext?'Open Arrify over HTTPS to use browser speaker selection. You can also choose an output in your device’s sound settings.':'For Bluetooth or another system speaker, choose the output in your device’s sound settings. This browser may show only speakers it already has permission to use.'}
 async function refresh(message){
  const run=++version,a=getAudio();$('#output-summary').textContent=message||'Choose a speaker below or use your device’s sound controls.';$('#output-help').textContent=helpText();const kind=picker(a),button=$('#output-system-picker');button.hidden=!kind;button.textContent=kind==='airplay'?'Choose AirPlay output':kind==='speakers'?'Choose speakers':'Choose wireless output';const list=$('#output-devices');list.replaceChildren();$('#output-refresh').hidden=typeof a.setSinkId!=='function'||!navigator.mediaDevices?.enumerateDevices;
  if(typeof a.setSinkId!=='function'||!navigator.mediaDevices?.enumerateDevices)return;
  let devices=[];try{devices=(await navigator.mediaDevices.enumerateDevices()).filter(d=>d.kind==='audiooutput'&&d.deviceId&&d.deviceId!=='default'&&d.label)}catch{}if(run!==version||a!==getAudio())return;
  const choices=[{deviceId:'',label:'System default'},...devices];for(const device of choices){const b=document.createElement('button');b.type='button';b.className='output-device';b.textContent=device.label;b.setAttribute('aria-pressed',String((a.sinkId||'')===device.deviceId||(a.sinkId==='default'&&!device.deviceId)));b.onclick=async()=>{b.disabled=true;try{await getAudio().setSinkId(device.deviceId);status();await refresh('Output updated.')}catch{await refresh('Couldn’t switch speakers. Check your device’s sound settings.')}};list.append(b)}
 }
 function show(message){refresh(message);if(!dialog.open)dialog.showModal()}
 async function choose(){
  const a=getAudio(),kind=picker(a);attachAudio(a);
  // Remove route/settings overlays before presenting native UI, in this same click.
  if(kind)for(const id of ['listening-dialog','output-dialog']){const overlay=$('#'+id);if(overlay?.open)overlay.close()}
  try{
   // Native pickers must be invoked directly within the user's click gesture.
   if(kind==='airplay'){a.webkitShowPlaybackTargetPicker();return}
   if(kind==='speakers'){const device=await navigator.mediaDevices.selectAudioOutput();await getAudio().setSinkId(device.deviceId);status();show('Output updated.');return}
   if(kind==='remote'){await a.remote.prompt();status();return}
   show();
  }catch(error){if(error.name==='AbortError')return;show(error.name==='NotAllowedError'?'Speaker selection wasn’t allowed. You can try again or use your device’s sound settings.':'No output was selected. Try again or use your device’s sound settings.')}
 }
 window.addEventListener('focus',status);document.addEventListener('visibilitychange',()=>{if(!document.hidden)status()});
 $('#output-system-picker').addEventListener('click',choose);$('#output-refresh').addEventListener('click',()=>refresh());navigator.mediaDevices?.addEventListener?.('devicechange',()=>{if(dialog.open)refresh()});
 return{choose,show,attachAudio};
}};

window.ArrifyOutput.describe=audio=>{const n=navigator,phone=/iPhone|iPod/.test(n.userAgent),tablet=/iPad/.test(n.userAgent)||(n.platform==='MacIntel'&&n.maxTouchPoints>1),apple=phone||tablet,host=phone?'iPhone':tablet?'iPad':/Android/.test(n.userAgent)?'Android':/Mac/.test(n.platform||'')?'Mac':/Win/.test(n.platform||'')?'Windows':'This device';const airplay=Boolean(audio.webkitCurrentPlaybackTargetIsWireless),wireless=airplay||audio.remote?.state==='connected',speaker=Boolean(audio.sinkId&&audio.sinkId!=='default');return{apple,wireless,host,kind:airplay?'airplay':'volume',label:airplay?'AirPlay':wireless?'Wireless output':speaker?'Selected speaker':'Audio output'}};

/* AirPlay symbol: Apple WebKit modern-media-controls/images/iOS/Airplay.svg.
Copyright (C) 2005, 2006, 2007, 2008, 2009 Apple Inc. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:
1.  Redistributions of source code must retain the above copyright
    notice, this list of conditions and the following disclaimer.
2.  Redistributions in binary form must reproduce the above copyright
    notice, this list of conditions and the following disclaimer in the
    documentation and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY APPLE INC. AND ITS CONTRIBUTORS ``AS IS'' AND ANY
EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL APPLE INC. OR ITS CONTRIBUTORS BE LIABLE FOR ANY
DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

*/
window.ArrifyOutput.symbol=airplay=>airplay?"<svg aria-hidden=\"true\" viewBox=\"0 0 16 13\" fill=\"currentColor\"><path d=\"M11.1001033,9.39348478 L11.9211522,10.324939 L14.5705804,10.324939 C15.4675804,10.324939 15.9642652,9.83506087 15.9642652,8.93120761 L15.9642652,1.39681739 C15.9642652,0.492978261 15.4675804,0.01 14.5705804,0.01 L1.43372717,0.01 C0.585067391,0.01 0.04,0.492978261 0.04,1.39681739 L0.04,8.93120761 C0.04,9.83506087 0.536769565,10.324939 1.43372717,10.324939 L4.09007935,10.324939 L4.90423261,9.39348478 L1.60621739,9.39348478 C1.22673043,9.39348478 1.02664348,9.18650217 1.02664348,8.80701522 L1.02664348,1.52100978 C1.02664348,1.1346413 1.22673043,0.934554348 1.60621739,0.934554348 L14.3980478,0.934554348 C14.7775913,0.934554348 14.9776783,1.1346413 14.9776783,1.52100978 L14.9776783,8.80701522 C14.9776783,9.18650217 14.7775913,9.39348478 14.3980478,9.39348478 L11.1001033,9.39348478 Z M12.2178348,12.9881967 C12.5421565,12.9881967 12.666363,12.6501134 12.4593522,12.4155255 L8.29884348,7.703075 C8.1401587,7.52368913 7.86417717,7.52368913 7.70547826,7.703075 L3.54501196,12.4155255 C3.33801522,12.6501134 3.46220761,12.9881967 3.78648696,12.9881967 L12.2178348,12.9881967 Z\"/></svg>":"<svg aria-hidden=\"true\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><path d=\"M4 9h4l5-4v14l-5-4H4zM17 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14\"/></svg>";

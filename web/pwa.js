/* iPad Home Screen launch settings; desktop and ordinary browser layout stay unchanged. */
'use strict';
(()=>{
 const ipad=/iPad/.test(navigator.userAgent)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
 if(!ipad)return;
 document.documentElement.classList.add('ipad-device');
 const manifest=document.querySelector('link[rel="manifest"]');
 if(manifest)manifest.href='/manifest-ipad.webmanifest';
 const modes=['(display-mode: standalone)','(display-mode: fullscreen)'].map(q=>matchMedia(q));
 const update=()=>document.documentElement.classList.toggle('ipad-installed',!!navigator.standalone||modes.some(m=>m.matches));
 modes.forEach(m=>m.addEventListener?.('change',update));update();
})();

'use strict';
importScripts('/device-buffer-worker.js?release=20261108-file-recovery');
const deviceBuffer=new ArrifyDeviceBuffer();
self.addEventListener('message',event=>{if(event.data?.type==='device-buffer'&&event.source?.id){const work=deviceBuffer.update(event.source.id,event.data);event.ports?.[0]?.postMessage({ok:true});event.waitUntil(work)}});
self.addEventListener('message',event=>{if(event.data?.type==='device-buffer-status'&&event.source?.id)event.ports?.[0]?.postMessage(deviceBuffer.snapshot(event.source.id))});
const CACHE='arrify-shell-v11-branding-20261108';
const ASSETS=['/branding.js','/buffer-status.js','/assets/channels/xmplaylist.ico','/assets/channels/80s-on-8.svg','/assets/channels/the-bridge.svg','/assets/channels/the-blend.svg','/assets/channels/1st-wave.svg','/assets/channels/the-pulse.svg','/assets/channels/classic-rewind.svg','/assets/channels/the-spectrum.svg','/navigation.js','/pwa.js','/pwa.css','/spotify.js','/party-import.js','/guest-wifi.js','/party-tv.js','/apple-music.js','/connections.js','/party-host.js','/party-guest.js','/party.css','/metadata.js','/browse.js','/listening.js','/api.js','/gapless.js','/timeline.js','/output.js','/brand.css','/assets/fonts/outfit-latin-wght-normal.woff2','/assets/fonts/dm-sans-latin-wght-normal.woff2','/style.css','/app.js','/player.js','/playback.js','/install.js','/offline.html','/manifest.webmanifest','/assets/icon-192.png','/assets/icon-512.png','/assets/apple-touch-icon.png','/assets/arrify-mark.jpg','/assets/arrify-wordmark.jpg'];
self.addEventListener('install',event=>event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(ASSETS)).then(()=>self.skipWaiting())));
// Worker updates must remain compatible with open pages. Activate the cache
// repair without requiring every tab to close; never reload or replace audio.
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key.startsWith('arrify-shell-')&&key!==CACHE||key==='arrify-audio-buffer-v1').map(key=>caches.delete(key)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',event=>{
  const request=event.request,url=new URL(request.url);
  if(request.method==='GET'&&url.origin===self.location.origin&&/^\/api\/v1\/stream\/[0-9]+$/.test(url.pathname)&&event.clientId){event.respondWith(deviceBuffer.handle(event.clientId,request,event));return;}
  if(request.method!=='GET'||url.origin!==self.location.origin||url.pathname.startsWith('/api/')||request.headers.has('Range'))return;
  if(request.mode==='navigate'){
    event.respondWith(fetch(request).catch(()=>caches.match('/offline.html')));return;
  }
  if(!ASSETS.includes(url.pathname))return;
  if(/^[a-f0-9]{20}$/.test(url.searchParams.get('v')||'')&&/\.(js|css)$/.test(url.pathname)){
    event.respondWith(caches.open(CACHE).then(async cache=>{
      const hit=await cache.match(request);if(hit)return hit;
      const response=await fetch(request);
      if(response.ok&&response.headers.get('Cache-Control')?.includes('immutable')){
        await cache.put(request,response.clone());
        const keys=await cache.keys();
        // Retain a bounded number of versioned responses; never cache streams/APIs.
        const versions=keys.filter(key=>new URL(key.url).searchParams.has('v'));
        for(const old of versions.slice(0,Math.max(0,versions.length-96)))await cache.delete(old);
      }
      return response;
    }));return;
  }
  event.respondWith(fetch(request).then(response=>{if(response.ok){const copy=response.clone();event.waitUntil(caches.open(CACHE).then(cache=>cache.put(url.pathname,copy)))}return response}).catch(()=>caches.match(url.pathname)));
});

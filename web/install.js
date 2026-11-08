'use strict';
(()=>{
  let invitation=null,installedHere=false;
  const button=document.querySelector('#install-app'),dialog=document.querySelector('#install-dialog');
  const installed=()=>matchMedia('(display-mode: standalone)').matches||matchMedia('(display-mode: fullscreen)').matches||navigator.standalone;
  function refresh(){button.hidden=Boolean(installed()||installedHere);button.textContent=invitation?'Install Arrify':'Add Arrify to your device'}
  window.addEventListener('beforeinstallprompt',event=>{event.preventDefault();invitation=event;refresh()});
  window.addEventListener('appinstalled',()=>{installedHere=true;invitation=null;button.hidden=true;dialog.close()});
  button.addEventListener('click',async()=>{if(invitation){const event=invitation;invitation=null;await event.prompt();await event.userChoice;refresh()}else{document.querySelector('#install-help').textContent=isSecureContext?'Use Share → Add to Home Screen (or your browser’s Install option), then launch Arrify from its Home Screen icon. On iPad, leave Open as Web App enabled if shown. Safari navigation controls are hidden; the status bar is controlled by iPadOS.':'Open https://arrify.robnets.com to install Arrify on your home screen.';dialog.showModal()}});
  function connection(){const status=document.querySelector('#network-status');status.hidden=navigator.onLine;status.textContent='You’re offline. Buffered music can keep playing; browsing needs a connection.'}
  window.addEventListener('offline',connection);window.addEventListener('online',connection);connection();refresh();
  if('serviceWorker'in navigator&&isSecureContext)navigator.serviceWorker.register('/sw.js',{updateViaCache:'none'}).catch(()=>{});
})();

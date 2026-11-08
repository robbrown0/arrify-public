'use strict';
(()=>{
 const button=document.getElementById('back');if(!button)return;
 const key='arrifyNavigation',saved=history.state?.[key];
 const id=saved?.id||String(Date.now())+'-'+Math.random().toString(36).slice(2);
 let depth=saved?.id===id&&Number.isSafeInteger(saved.depth)?Math.max(0,saved.depth):0;
 let layers=[],pending=false,routing=false,afterBack=null,url=location.href;
 const dialogs=['now-playing-dialog','apple-full-player','spotify-full-player'];
 const route=()=>location.hash.slice(1)||'home';
 function parent(){const [view,detail]=route().split('/');if(detail){if(view==='library')return'library';if(['albums','artists','browse','admin','party'].includes(view))return view;if(view==='personal')return'library/playlists';if(view==='collection')return'home';if(view==='radio')return'browse'}return'home'}
 function paint(){button.disabled=!layers.length&&depth===0&&route()==='home';button.setAttribute('aria-label',layers.length?'Close current view':depth>0?'Go back':'Back to '+parent().replace('/',' '));button.title='Back'}
 function stamp(push=false){history[push?'pushState':'replaceState']({...history.state,[key]:{id,depth,layers:[...layers]}},'');paint()}
 function read(){const list=[];if(document.body.classList.contains('search-focused'))list.push('search');for(const name of dialogs){const d=document.getElementById(name);if(d?.open){list.push(name);if(d.classList.contains('queue-sheet-open'))list.push(name+':queue')}}return list}
 function apply(target){
  const search=target.includes('search');document.body.classList.toggle('search-focused',search);if(!search&&document.activeElement?.id==='search')document.activeElement.blur();
  for(const name of dialogs){let d=document.getElementById(name);if(target.includes(name)&&!d?.open){const trigger=name==='now-playing-dialog'?'#now-open':name==='apple-full-player'?'[data-apple-expand]':'[data-spotify-expand]';document.querySelector(trigger)?.click();d=document.getElementById(name)}if(!d?.open)continue;
   if(!target.includes(name)){d.close();continue}const queue=target.includes(name+':queue');if(queue!==d.classList.contains('queue-sheet-open')){const selector=name==='now-playing-dialog'?(queue?'#np-show-queue':'#np-back-playing'):name==='apple-full-player'?(queue?'[data-apple-queue]':'[data-apple-back]'):(queue?'[data-spotify-queue]':'[data-spotify-back]');d.querySelector(selector)?.click()}
  }
 }
 function sync(){
  if(pending||routing||location.href!==url)return;
  const next=read();let common=0;while(common<layers.length&&layers[common]===next[common])common++;
  if(common<layers.length){const count=layers.length-common;pending=true;afterBack=()=>{apply(next);layers=read();stamp()};history.go(-count);return}
  for(const layer of next.slice(common)){layers.push(layer);depth++;stamp(true)}paint();
 }
 const observed=new WeakSet();const layerObserver=new MutationObserver(sync);
 function observe(){for(const name of dialogs){const d=document.getElementById(name);if(d&&!observed.has(d)){observed.add(d);layerObserver.observe(d,{attributes:true,attributeFilter:['open','class']});d.addEventListener('cancel',event=>{if(d.classList.contains('queue-sheet-open')){event.preventDefault();const next=layers.slice(0,-1);apply(next);sync()}})}}sync()}
 new MutationObserver(observe).observe(document.body,{attributes:true,attributeFilter:['class'],childList:true});
 // Reloaded documents start with closed layers; never resurrect a keyboard automatically.
 layers=read();stamp();observe();
 window.addEventListener('popstate',()=>{
  const mark=history.state?.[key],sameURL=location.href===url;
  if(!mark&&location.href!==url){url=location.href;routing=true;return}
  depth=mark?.id===id&&Number.isSafeInteger(mark.depth)?Math.max(0,mark.depth):0;
  layers=sameURL&&mark?.id===id&&Array.isArray(mark.layers)?mark.layers:[];
  pending=true;apply(layers);url=location.href;if(!sameURL&&mark?.id===id)stamp();
  const done=afterBack;afterBack=null;queueMicrotask(()=>{pending=false;if(done)done();paint()});
 });
 window.addEventListener('hashchange',()=>{routing=false;url=location.href;const mark=history.state?.[key];if(mark?.id===id&&Number.isSafeInteger(mark.depth))depth=Math.max(0,mark.depth);else{depth++;layers=[];stamp();sync()}paint()});
 function back(){sync();if(pending)return;if(layers.length||depth>0&&history.length>1)history.back();else if(route()!=='home')location.hash=parent()}
 button.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();back()});
 // Finish dismissing layers before an in-app link changes the URL. This avoids
 // racing a delayed history.back() against navigation to an album or artist.
 document.addEventListener('click',event=>{const a=event.target.closest('a[href^="#"],[data-go]');if(!a||!layers.length||event.defaultPrevented||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;event.preventDefault();event.stopImmediatePropagation();if(pending)return;document.dispatchEvent(new CustomEvent('arrify-before-navigate',{detail:{element:a}}));const destination=a.hash||'#'+a.dataset.go,count=layers.length;pending=true;apply([]);afterBack=()=>{layers=[];stamp();location.hash=destination};history.go(-count)},true);
 document.addEventListener('keydown',event=>{if(event.key==='Enter'&&layers.length&&event.target.matches('[data-go]')){event.preventDefault();event.stopImmediatePropagation();event.target.click()}},true);
 window.ArrifyNavigation={back,reset(){pending=false;afterBack=null;layers=[];depth=0;url=location.href;stamp()}};
})();

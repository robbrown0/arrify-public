'use strict';
// Shared indexed catalog, scoped queries and revision-fenced paging on every device.
function catalogBrowserView(view, navigation=true) {
 const title=view[0].toUpperCase()+view.slice(1);
 return (navigation?tabs(view):'')+'<section class="catalog-browser"><h1 class="page-title">'+title+'</h1><div class="catalog-controls"><label><span class="sr-only">Search '+view+'</span><input id="catalog-query" type="search" maxlength="200" autocomplete="off" placeholder="Search '+view+'"></label><label>Sort<select id="catalog-sort"><option value="title">'+(view==='songs'?'Title':'Name')+' · A–Z</option>'+(view==='artists'?'':'<option value="artist">Artist · A–Z</option>')+'<option value="recent">Recently added</option><option value="relevance">Relevance</option></select></label></div><p id="catalog-count" class="subheading" role="status"></p><div id="catalog-actions"></div><div id="catalog-results"></div><div id="catalog-more" class="browse-pagination"><span role="status"></span><button class="chip" type="button" hidden>Retry</button></div></section>';
}
function mountCatalogBrowser(view, epoch) {
 const root=document.querySelector('.catalog-browser'),query=root.querySelector('#catalog-query'),sort=root.querySelector('#catalog-sort'),results=root.querySelector('#catalog-results'),more=root.querySelector('#catalog-more'),retry=more.querySelector('button'),status=more.querySelector('span'),owner=me?.id;
 const storage='arrify:catalog-sort:'+owner+':'+view;
 try{const saved=localStorage.getItem(storage);if([...sort.options].some(x=>x.value===saved))sort.value=saved}catch{}
 let generation=0,disposed=false,busy=false,offset=0,revision=null,rows=[],failed=false,observer,timer;
 const valid=()=>!disposed&&epoch===requestEpoch&&owner===me?.id&&root.isConnected;
 let browseSort=sort.value;
 function syncSort(){const searching=Boolean(query.value.trim());sort.value=searching?'relevance':browseSort;sort.disabled=searching}
 const options=()=>({q:query.value.trim(),sort:query.value.trim()?'relevance':browseSort});
 function keepQueryVisible(){if(!valid()||document.activeElement!==query)return;requestAnimationFrame(()=>{if(!valid()||document.activeElement!==query)return;const viewport=window.visualViewport,rect=query.getBoundingClientRect(),top=Math.max(viewport?.offsetTop||0,document.querySelector('.topbar')?.getBoundingClientRect().bottom||0)+12,bottom=(viewport?.offsetTop||0)+(viewport?.height||innerHeight)-12;if(rect.top<top||rect.bottom>bottom)query.scrollIntoView({block:'center',behavior:'instant'})})}
 query.addEventListener('focus',keepQueryVisible);window.visualViewport?.addEventListener('resize',keepQueryVisible);
 function playbackContext(){if(view==='songs')context={source:{kind:'catalog',id:JSON.stringify(options())}}}
 async function load(reset=false){
  if(!valid()||(!reset&&(busy||offset===null)))return;
  if(reset){generation++;offset=0;revision=null;rows=[];root.querySelector('#catalog-count').textContent='';results.replaceChildren();root.querySelector('#catalog-actions').replaceChildren();playbackContext();keepQueryVisible()}
  const ticket=generation;busy=true;failed=false;retry.hidden=true;status.textContent='Loading '+view+'…';more.hidden=false;more.setAttribute('aria-busy','true');
  const params=new URLSearchParams({...options(),kind:view==='songs'?'tracks':view,limit:'50',offset:String(offset)});if(revision)params.set('revision',revision);
  try{
   const page=await api('/catalog?'+params);if(!valid()||ticket!==generation)return;
   const seen=new Set(rows.map(x=>x.id));rows.push(...page.items.filter(x=>!seen.has(x.id)));revision=page.revision;offset=page.next_offset;
   playbackContext();
   results.innerHTML=view==='songs'?table(rows):cards(rows.map(x=>({...x,tracks:x.track_count})),view);
   root.querySelector('#catalog-count').textContent=page.total.toLocaleString()+' '+view;
   if(view==='songs')root.querySelector('#catalog-actions').innerHTML=playActions(page.total);
   if(!rows.length)results.innerHTML=empty('No matching '+view,'Try a different search.');
   syncCollectionButton();keepQueryVisible();more.hidden=offset===null;status.textContent='More music loads as you scroll.';
  }catch(error){if(valid()&&ticket===generation){failed=true;status.textContent=error.status===409?'The library changed. Refresh this list to continue.':(error.message||'Couldn’t load '+view+'.');retry.textContent=error.status===409?'Refresh list':'Retry';retry.dataset.reset=String(error.status===409);retry.hidden=false}}
  finally{if(valid()&&ticket===generation){busy=false;more.removeAttribute('aria-busy');observer?.unobserve(more);if(offset!==null&&!failed)observer?.observe(more)}}
 }
 query.oninput=()=>{syncSort();keepQueryVisible();clearTimeout(timer);generation++;timer=setTimeout(()=>load(true),250)};
 sort.onchange=()=>{browseSort=sort.value;try{localStorage.setItem(storage,sort.value)}catch{}clearTimeout(timer);load(true)};
 retry.onclick=()=>load(retry.dataset.reset==='true');
 if(typeof IntersectionObserver==='function'){observer=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)&&!failed)load()},{rootMargin:'0px 0px 400px 0px'})}
 load(true);
 return {dispose(){disposed=true;generation++;clearTimeout(timer);observer?.disconnect();window.visualViewport?.removeEventListener('resize',keepQueryVisible)}};
}

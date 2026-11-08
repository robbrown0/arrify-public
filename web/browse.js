/* Browse reads cached library classifications and daily chart snapshots. */
'use strict';
const browseRequestsInFlight=new Map();
// Explicit parent links also work when a category is opened directly.
function browseBackLink(key){
 if(!key)return '';
 let target='browse',label='Browse';
 if(key.startsWith('decade-')){
  const [decade,genre]=key.slice(7).split('~');
  if(genre){target='browse/decade-'+encodeURIComponent(decade);label=decade+'s'}
  else{target='browse/decades';label='Decades'}
 }else if(key.startsWith('genre-')){target='browse/genres';label='Genres'}
 else if(key.startsWith('chart-')){target='browse/hot';label='What’s Hot'}
 else if(key.startsWith('releases-')&&key!=='releases-overall'){target='browse/releases-overall';label='What’s New'}
 return '<a class="browse-back" href="#'+esc(target)+'"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m12 5-7 7 7 7M5 12h14"/></svg><span>Back to '+esc(label)+'</span></a>';
}
const browseScrollPositions=new Map();
function rememberBrowsePosition(route,content){
 if(!route||content.getAttribute('aria-busy')==='true')return;
 browseScrollPositions.delete(route);
 browseScrollPositions.set(route,{page:window.scrollY,content:content.scrollTop});
 if(browseScrollPositions.size>40)browseScrollPositions.delete(browseScrollPositions.keys().next().value);
}
function restoreBrowsePosition(route,content){
 const saved=browseScrollPositions.get(route);if(!saved)return;
 content.scrollTop=saved.content;window.scrollTo(0,saved.page);
}
function browseTabs(active){return '<nav class="browse-tabs" aria-label="Browse categories">'+[['','All'],['releases-overall','What’s New'],['hot','What’s Hot'],['decades','Decades'],['genres','Genres']].map(([id,name])=>'<a class="chip '+(active===id?'selected':'')+'" href="#browse'+(id?'/'+id:'')+'" '+(active===id?'aria-current="page"':'')+'>'+name+'</a>').join('')+'</nav>'}
function browseTiles(items,prefix){return '<div class="browse-grid">'+items.map((x,i)=>'<a class="browse-tile browse-color-'+i%6+'" href="#browse/'+prefix+encodeURIComponent(x.id)+'"><strong>'+esc(x.name)+'</strong><span>'+Number(x.count).toLocaleString()+' songs</span><span aria-hidden="true" class="browse-tile-mark">'+(prefix==='decade-'?'◷':'♫')+'</span></a>').join('')+'</div>'}
function browseIndexNote(d){return d.indexing?'<p class="section-description" role="status">Organizing your library in the background'+(d.total?' · '+Number(d.scanned).toLocaleString()+' / '+Number(d.total).toLocaleString():'')+'. <button class="text-button" data-browse-refresh>Refresh Browse</button></p>':d.error?'<p class="section-description">Some library details could not be read. Available categories are shown; indexing will retry.</p>':''}
function browseChartRows(d,isNew=false){return '<div class="browse-chart-list">'+d.items.map((x,index)=>{const local=browseRequestsInFlight.get(String(me?.id)+':'+x.request_key),requested=x.requested||local==='requested',busy=local==='pending';return '<article class="browse-chart-row"><span class="browse-rank">'+(isNew?index+1:x.rank)+'</span><div class="browse-chart-copy"><strong>'+esc(x.title)+'</strong><span>'+esc(x.artist)+'</span>'+(isNew?'<small class="browse-chart-position">Chart #'+Number(x.rank)+'</small>':'')+'</div>'+(x.track?'<button class="chip" data-play="'+x.track.id+'" aria-label="Play '+esc(x.title)+'">Play</button><button class="song-options" data-song-menu="'+x.track.id+'" aria-label="More options for '+esc(x.title)+'">⋯</button>':'<button class="chip" data-browse-request="'+esc(x.request_key)+'" data-request-artist="'+esc(x.artist)+'" data-request-title="'+esc(x.title)+'" data-genre="'+esc(d.genre)+'" '+(requested||busy?'disabled':'')+' aria-label="Request '+esc(x.title)+' by '+esc(x.artist)+'">'+(requested?'Requested':busy?'Requesting…':'Request')+'</button>')+'<small class="browse-request-status" role="status"></small></article>'}).join('')+'</div>'}
async function browseView(key,epoch){
 const overview=await api('/browse');if(epoch!==requestEpoch)return '';const chosen=key==='new'?'hot':key||'';let html='<div class="browse-page">'+browseBackLink(chosen)+'<h1 class="page-title">Browse</h1>'+browseTabs(chosen.startsWith('decade')?'decades':chosen.startsWith('genre')?'genres':chosen.startsWith('releases-')?'releases-overall':chosen==='hot'||chosen.startsWith('chart-')?'hot':'')+browseIndexNote(overview);
 if(!chosen){return html+section('Discover','<a class="browse-new-release" href="#browse/releases-overall"><strong>What’s New</strong><span>Recent releases in the charts · past 90 days</span><span aria-hidden="true">›</span></a><a class="browse-new-release" href="#browse/hot"><strong>What’s Hot</strong><span>U.S. Top 40 and genre rankings · refreshed daily</span><span aria-hidden="true">›</span></a>')+section('Decades',browseTiles(overview.decades,'decade-'))+section('Genres',browseTiles(overview.genres,'genre-'))+'</div>'}
 if(chosen==='decades')return html+section('Through the decades',browseTiles(overview.decades,'decade-'))+'</div>';
 if(chosen==='genres')return html+section('All genres',browseTiles(overview.genres,'genre-'))+'</div>';
 if(chosen==='hot')return html+section('What’s Hot','<div class="browse-feature"><div><h2>U.S. Top 40</h2><p>Most played on Apple Music · refreshed daily.</p></div><a class="chip" href="#browse/chart-overall">View chart</a></div>')+section('Hot by genre','<p class="section-description">iTunes Store · U.S. song rankings, refreshed daily. Older releases can chart too.</p><div class="browse-grid">'+overview.genres.filter(g=>g.id!=='other').map((g,i)=>'<a class="browse-tile browse-color-'+i%6+'" href="#browse/chart-'+g.id+'"><strong>'+esc(g.name)+'</strong><span>Top 40</span></a>').join('')+'</div>')+'</div>';
 if(chosen.startsWith('chart-')||chosen.startsWith('releases-')){
  const isNew=chosen.startsWith('releases-'),genre=chosen.slice(chosen.indexOf('-')+1),d=await api('/browse/charts?genre='+encodeURIComponent(genre)+'&new='+(isNew?'1':'0'));if(epoch!==requestEpoch)return '';context={ids:d.items.filter(x=>x.track).map(x=>x.track.id)};
  const name=genre==='overall'?'U.S.':overview.genres.find(g=>g.id===genre)?.name||genre;
  html+='<div class="section-head"><h2>'+esc(name)+(isNew?' · What’s New':' · What’s Hot — Top 40')+'</h2><button class="chip" data-browse-refresh>Refresh</button></div>';
  html+='<details class="browse-chart-info"><summary><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v6"/><circle cx="12" cy="7.5" r=".8" fill="currentColor" stroke="none"/></svg><span>Chart info</span></summary><div class="browse-chart-info-body">';
  html+='<p>'+esc(d.source||'Chart snapshot is being prepared')+(d.fetched?' · Updated '+new Date(d.fetched*1000).toLocaleString():'')+'.</p>';
  html+='<p>'+(isNew?'Songs released in the past 90 days that appear in these rankings. Up to 40 matches, in chart order; not a complete new-release catalog. List numbers are consecutive; “Chart #” shows the original source ranking.':genre==='overall'?'The first 40 songs in Apple Music’s U.S. most-played feed. Includes releases from any year.':'The first 40 songs in this iTunes Store U.S. genre ranking. Includes releases from any year.')+'</p>';
  if(d.provider_updated)html+='<p>Source updated: '+esc(d.provider_updated)+'</p>';
  html+='<p>Play uses songs in your library. Request verifies the song and submits its official album. Follow progress in Music requests.</p><p>Charts update daily. Refresh checks the saved snapshot.</p></div></details>';
  if(d.stale&&!d.pending)html+='<p class="section-description" role="status">Showing the last available chart while updates retry.</p>';
  if(d.pending)html+=empty('Charts are being prepared.','Browse your library while the first daily snapshot loads, then tap Refresh.');else{if(context.ids.length)html+=playActions(context.ids.length);html+='<p class="section-description browse-chart-count">'+d.items.length+' songs</p>'+browseChartRows(d,isNew);if(!d.items.length)html+=empty('No recent releases in this chart.','Try another genre or check the full Top 40.');}
  if(isNew||genre!=='overall')html+='<div class="browse-subfilters"><a class="chip" href="#browse/'+(isNew?'releases-':'chart-')+'overall">All genres</a>'+overview.genres.filter(g=>g.id!=='other').map(g=>'<a class="chip '+(g.id===genre?'selected':'')+'" href="#browse/'+(isNew?'releases-':'chart-')+g.id+'">'+esc(g.name)+'</a>').join('')+'</div>';
  return html+'</div>';
 }
 const match=/^(genre|decade)-([^~]+)(?:~([^~]*))?(?:~([^~]+))?$/.exec(chosen);if(!match)throw Error('Browse category not found.');let genre='',decade='',kind='hits';if(match[1]==='genre'){genre=decodeURIComponent(match[2]);kind=match[3]||'hits'}else{decade=match[2];genre=match[3]||'';kind=match[4]||'hits'}
 const query=new URLSearchParams({page:'1',genre,decade,kind,limit:'100'}),d=await api('/browse?'+query);if(epoch!==requestEpoch)return '';const filters={genre,decade,kind};context={browse:filters,total:d.total};
 const href=(g,k)=>'#browse/'+(decade?'decade-'+decade+'~'+g+'~'+k:'genre-'+genre+'~'+k);
 html+='<h2>'+esc(d.name)+'</h2>';
 if(decade)html+='<nav class="browse-subfilters" aria-label="Genres in this decade"><a class="chip '+(!genre?'selected':'')+'" href="'+href('',kind)+'">All genres</a>'+d.genre_counts.filter(g=>g.id!=='other').map(g=>'<a class="chip '+(genre===g.id?'selected':'')+'" href="'+href(g.id,kind)+'">'+esc(g.name)+'</a>').join('')+'</nav>';
 html+='<nav class="browse-subfilters" aria-label="Content type">'+[['hits','Essentials'],['songs','Songs'],['albums','Albums'],['artists','Artists']].map(([k,label])=>'<a class="chip '+(kind===k?'selected':'')+'" href="'+href(genre,k)+'" '+(kind===k?'aria-current="page"':'')+'>'+label+'</a>').join('')+'</nav>';
 html+='<p class="section-description">'+Number(d.total).toLocaleString()+' '+(kind==='albums'?'albums':kind==='artists'?'artists':'songs')+'. '+(kind==='hits'?'Up to 200 familiar recordings, selected by curated appearances and available listen counts—not an official historical chart.':'All matching library content, grouped using file genre tags.')+(decade?' Original release year is used when tagged; otherwise the file’s release year is used. Unknown years are excluded.':'')+'</p>';
 if(d.tracks){html+=d.tracks.length?playActions(d.total)+table(d.tracks):empty('No matching songs yet.',d.indexing?'Library indexing is still running. Refresh Browse in a moment.':kind==='hits'?'Open Songs to browse everything in this category.':'Matching library additions will appear after indexing.');}else html+=cards(d.items,kind);
 if(d.next_offset<d.total)html+='<button class="chip" data-browse-more="'+esc(query.toString())+'" data-offset="'+d.next_offset+'">Load more</button>';
 return html+'</div>';
}
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.hasAttribute('data-browse-refresh'))safe(render);if(b.dataset.browseRequest)submitBrowseRequest(b);if(b.dataset.browseMore)safe(async()=>{const epoch=requestEpoch,q=new URLSearchParams(b.dataset.browseMore);q.set('offset',b.dataset.offset);b.disabled=true;try{const d=await api('/browse?'+q);if(epoch!==requestEpoch)return;if(d.tracks){const all=[...shownTracks,...d.tracks];$('.track-table').outerHTML=table(all)}else{const tmp=document.createElement('div');tmp.innerHTML=cards(d.items,d.kind);$('.card-grid').insertAdjacentHTML('beforeend',tmp.firstElementChild.innerHTML)}b.dataset.offset=d.next_offset;if(d.next_offset>=d.total)b.remove()}finally{b.disabled=false}})});

async function submitBrowseRequest(button){
 const owner=me?.id,key=String(owner)+':'+button.dataset.browseRequest;if(!owner||button.disabled||browseRequestsInFlight.has(key))return;
 browseRequestsInFlight.set(key,'pending');button.disabled=true;button.textContent='Requesting…';const status=button.closest('.browse-chart-row').querySelector('.browse-request-status');status.textContent='Verifying the song and official album…';
 try{const result=await api('/browse/request',{genre:button.dataset.genre,entry:button.dataset.browseRequest});browseRequestsInFlight.set(key,'requested');if(me?.id!==owner)return;for(const control of document.querySelectorAll('[data-browse-request]'))if(control.dataset.browseRequest===button.dataset.browseRequest){control.disabled=true;control.textContent=result.available?'Available':'Requested';control.closest('.browse-chart-row').querySelector('.browse-request-status').textContent=result.available?'This song is already in your library.':'Submitted. Follow progress in Music requests.';}toast(result.available?'This song is already available.':'Request submitted. Follow progress in Music requests.');}
 catch(error){browseRequestsInFlight.delete(key);if(me?.id!==owner)return;for(const control of document.querySelectorAll('[data-browse-request]'))if(control.dataset.browseRequest===button.dataset.browseRequest){control.disabled=false;control.textContent='Request';control.closest('.browse-chart-row').querySelector('.browse-request-status').textContent=error.code==='timeout'||error.code==='connection'?'Could not confirm submission. Check Music requests before trying again.':error.message;}}
}

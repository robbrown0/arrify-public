'use strict';
window.ArrifyListening={
 device(){const n=navigator,ua=n.userAgent,newId=()=>typeof crypto.randomUUID==='function'?crypto.randomUUID():Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join('');let id=newId(),restored=false;try{const previous=sessionStorage.getItem('arrify:listening-tab'),reload=performance.getEntriesByType('navigation')[0]?.type==='reload';if(reload&&/^[a-f0-9-]{16,64}$/.test(previous||'')){id=previous;restored=true}sessionStorage.setItem('arrify:listening-tab',id)}catch{}return{id,restored,name:/iPhone/.test(ua)?'iPhone':/iPad/.test(ua)||(n.platform==='MacIntel'&&n.maxTouchPoints>1)?'iPad':/Android/.test(ua)?'Android':/Mac/.test(n.platform)?'Mac':/Win/.test(n.platform)?'Windows':'Browser'}},
 remote(state,id){return Boolean(state?.listening?.owner&&state.listening.owner!==id)},
 position(state,receivedAt,now=Date.now()){const d=state?.listening;let pos=Number(state?.position)||0;if(d?.online&&d.playing&&d.observed==='playing'&&d.ack===d.intent){const age=Math.max(0,(state.server_time||0)-d.seen)+Math.max(0,(now-receivedAt)/1000);pos+=Math.min(8,age)}return d?.duration>0?Math.min(pos,d.duration):pos}
};

/* Same-origin JSON requests; retry reads only, never replay a mutation. */
'use strict';
window.ArrifyAPI={create({getCSRF=()=>'',fetcher=(...args)=>fetch(...args),pause=ms=>new Promise(resolve=>setTimeout(resolve,ms))}={}){
 const partyReads=new Map();let owner=getCSRF();
 async function transport(path,data,cached=null){
  const write=data!==undefined&&data!==null,controller=new AbortController(),timer=setTimeout(()=>controller.abort(),path.includes('/import-playlists')?60000:path==='/browse/request'?90000:path.startsWith('/discover')?60000:12000);
  try{
   for(let attempt=0;;attempt++){
    try{
     const response=await fetcher('/api/v1'+path,{method:write?'POST':'GET',signal:controller.signal,headers:write?{'Accept':'application/json','Content-Type':'application/json','X-CSRF-Token':getCSRF()}:{'Accept':'application/json',...(cached?.etag?{'If-None-Match':cached.etag}:{})},body:write?JSON.stringify(data):undefined});
     if(response.status===304&&cached?.value)return cached.value;
     if(cached)cached.etag=response.headers.get('ETag');
     let value;const contentType=response.headers.get('Content-Type')||'';
     try{if(!contentType.toLowerCase().includes('application/json'))throw Error('Unexpected response');value=JSON.parse(await response.text());if(!value||typeof value!=='object'||Array.isArray(value))throw Error('Unexpected response')}
     catch(cause){if(cause.name==='AbortError')throw cause;const error=Error('Arrify is briefly unavailable. Try again in a moment.');error.status=response.ok?502:response.status;error.code='invalid-response';throw error}
     if(!response.ok){const error=Error(typeof value.error==='string'?value.error:'The request could not be completed.');error.status=response.status;throw error}
     return value;
    }catch(error){
     const retryable=!error.status||[502,503,504].includes(error.status);
     if(!write&&attempt<2&&retryable&&error.name!=='AbortError'&&!controller.signal.aborted){await pause(350*(attempt+1));continue}
     throw error;
    }
   }
  }catch(error){
   if(error.name==='AbortError'){const timeout=Error('The connection is taking too long. Please try again.');timeout.code='timeout';throw timeout}
   if(!error.status){const offline=Error('Couldn’t reach Arrify. Check your connection and try again.');offline.code='connection';throw offline}
   throw error;
  }finally{clearTimeout(timer)}
 }
 return async function request(path,data){
  const identity=getCSRF();if(identity!==owner){partyReads.clear();owner=identity}
  if(data!==undefined&&data!==null){partyReads.clear();return transport(path,data)}
  if(!/^\/parties\/[A-Za-z0-9_-]+$/.test(path))return transport(path,data);
  let entry=partyReads.get(path);
  if(!entry){entry={value:null,etag:null,at:0,pending:null};partyReads.set(path,entry);if(partyReads.size>8)partyReads.delete(partyReads.keys().next().value)}
  if(!entry.pending&&(!entry.value||Date.now()-entry.at>=250)){
   entry.pending=transport(path,undefined,entry).then(value=>{entry.value=value;entry.at=Date.now();return value}).finally(()=>{entry.pending=null});
  }
  const value=entry.pending?await entry.pending:entry.value;
  // Callers own their objects; one provider must not mutate another view's snapshot.
  return JSON.parse(JSON.stringify(value));
 }
}};

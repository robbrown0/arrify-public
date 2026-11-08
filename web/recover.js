'use strict';
(()=>{
 const $=s=>document.querySelector(s);let token=location.hash.slice(1);
 // Keep the token out of history, referrers, storage, and copied page addresses.
 if(token)history.replaceState(null,'',location.pathname);
 async function post(path,data={}){const response=await fetch('/api/v1/auth/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data),cache:'no-store'});const result=await response.json();if(!response.ok)throw Error(result.error||'Recovery failed.');return result}
 function message(text){$('#recovery-message').textContent=text}
 async function initialize(){
  if(!token){$('#recovery-start').hidden=false;return}
  if(location.protocol!=='https:'&&!['127.0.0.1','localhost'].includes(location.hostname))throw Error('Open the reset link on Arrify’s HTTPS address.');
  const data=await post('recovery-check',{token});$('#recovery-username').value=data.username;$('#recovery-help').textContent='Use 12 or more characters. Save this one in Passwords.';$('#recovery-form').hidden=false;
 }
 $('#recovery-start').onclick=async()=>{const button=$('#recovery-start');button.disabled=true;try{const data=await post('recovery-link');location.replace(data.url)}catch(error){message(error.message);button.disabled=false}};
 $('#recovery-form').onsubmit=async event=>{event.preventDefault();const button=$('#recovery-save');if($('#recovery-password').value!==$('#recovery-confirm').value){message('Those passwords don’t match.');return}button.disabled=true;try{const data=await post('recover',{token,password:$('#recovery-password').value});token='';$('#recovery-form').hidden=true;$('#recovery-password').value='';$('#recovery-confirm').value='';$('#recovery-help').textContent='All set.';message(data.message);$('#recovery-signin').hidden=false}catch(error){message(error.message);button.disabled=false}};
 window.addEventListener('hashchange',()=>{if(!location.hash)return;token=location.hash.slice(1);history.replaceState(null,'',location.pathname);$('#recovery-start').hidden=true;initialize().catch(error=>message(error.message))});
 initialize().catch(error=>message(error.message));
})();

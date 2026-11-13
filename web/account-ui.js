'use strict';
window.ArrifyPassword = {
 enhance(input, confirmation) {
  for (const field of [input, confirmation].filter(Boolean)) {
   if(field.dataset.enhanced)continue;field.dataset.enhanced='true';
   const wrap=document.createElement('span');wrap.className='password-entry';field.before(wrap);wrap.append(field);
   const toggle=document.createElement('button');toggle.type='button';toggle.className='password-eye';toggle.setAttribute('aria-label','Show password');toggle.setAttribute('aria-pressed','false');
   toggle.innerHTML='<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>';
   toggle.onclick=()=>{const show=field.type==='password';field.type=show?'text':'password';toggle.setAttribute('aria-pressed',String(show));toggle.setAttribute('aria-label',show?'Hide password':'Show password')};wrap.append(toggle);
  }
  if(!confirmation)return;
  const hint=document.createElement('small');hint.className='password-feedback';input.closest('label').append(hint);
  const match=document.createElement('small');match.className='password-feedback';confirmation.closest('label').append(match);
  function update(){const p=input.value,ok=p.length>=15&&p.length<=128;hint.textContent=ok?'✓ Length requirement met. Use a unique password.':'Use 15–128 characters. Try a few unrelated words.';hint.classList.toggle('met',ok);const same=!!confirmation.value&&p===confirmation.value;match.textContent=same?'✓ Passwords match':confirmation.value?'Passwords don’t match yet.':'';match.classList.toggle('met',same);confirmation.setCustomValidity(confirmation.value&&!same?'Passwords must match.':'')}
  input.addEventListener('input',update);confirmation.addEventListener('input',update);update();
 }
};

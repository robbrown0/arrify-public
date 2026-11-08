'use strict';
window.ArrifyBranding={
 apply(value){
  if(!/^\/api\/v1\/branding\/icon\?v=[a-f0-9]{20}$/.test(value?.icon_url||''))return;
  for(const image of document.querySelectorAll('[data-brand-icon]'))image.src=value.icon_url;
  for(const link of document.querySelectorAll('link[rel="icon"],link[rel="apple-touch-icon"]'))link.href=value.icon_url;
 },
 async refresh(){try{const r=await fetch('/api/v1/branding',{cache:'no-store'});if(r.ok)this.apply(await r.json())}catch{}},
 markup(value){const icon=/^\/api\/v1\/branding\/icon\?v=[a-f0-9]{20}$/.test(value?.icon_url||'')?value.icon_url:'/api/v1/branding/icon';return '<section class="branding-editor"><h2>Server icon</h2><img class="branding-preview" src="'+icon+'" width="112" height="112" alt="Current server icon"><p>Used on the sign-in page, website, and inside the iOS app. iPhone Home Screen icons are included in app updates.</p><form id="branding-form"><label>Choose an image<input id="branding-file" type="file" accept="image/png,image/jpeg,image/webp" required></label><p class="muted">PNG, JPEG, or WebP, up to 4 MB. A square image works best.</p><button class="primary" type="submit">Upload icon</button> <button id="branding-reset" type="button" class="chip" '+(value.custom?'':'disabled')+'>Restore default</button><p id="branding-status" role="status"></p></form></section>'},
 async encode(file){
  if(!file||file.size>4*1024*1024||!['image/png','image/jpeg','image/webp'].includes(file.type))throw Error('Choose a PNG, JPEG, or WebP image up to 4 MB.');
  const bitmap=await createImageBitmap(file);
  try{
   const canvas=document.createElement('canvas');canvas.width=512;canvas.height=512;
   if(bitmap.width>8192||bitmap.height>8192)throw Error('Choose an image smaller than 8192 pixels.');
   const context=canvas.getContext('2d'),scale=Math.min(512/bitmap.width,512/bitmap.height),width=bitmap.width*scale,height=bitmap.height*scale;context.drawImage(bitmap,(512-width)/2,(512-height)/2,width,height);
   return canvas.toDataURL('image/png').split(',')[1];
  }finally{bitmap.close()}
 }
};
window.ArrifyBranding.refresh();

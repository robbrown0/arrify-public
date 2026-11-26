"""Manual real-audio check; requires audiobook_preview.py with ARRIFY_MULTIPART_BOOK=1."""
import asyncio,json,os
from playwright.async_api import async_playwright

async def check(browser,sleep):
 page=await browser.new_page(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
 await page.add_init_script('''window.bookStarts=[];window.bookSeeks=[];const original=Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype,'currentTime');Object.defineProperty(HTMLMediaElement.prototype,'currentTime',{get:original.get,set(value){if(this.id==='audiobook-audio'&&this.src.includes('/stream/'))window.bookSeeks.push({url:this.src,position:value});original.set.call(this,value)}});document.addEventListener('playing',event=>{if(event.target.id==='audiobook-audio'&&event.target.src.includes('/stream/'))window.bookStarts.push(event.target.src)},true);''')
 if sleep:
  await page.add_init_script('''document.addEventListener('loadedmetadata',event=>{if(event.target.id==='audiobook-audio'&&event.target.src.includes('/stream/')&&event.target.src.endsWith('/0')){const control=document.getElementById('book-sleep');control.value='chapter';control.dispatchEvent(new Event('change',{bubbles:true}))}},true)''')
 errors=[];progress=[];page.on('pageerror',lambda error:errors.append(str(error)));page.on('request',lambda request:progress.append(json.loads(request.post_data)) if request.url.endswith('/audiobooks/progress') and request.post_data else None)
 await page.goto('http://127.0.0.1:18850',wait_until='networkidle')
 await page.locator('#username').fill('navqualifier');await page.locator('#password').fill('Synthetic-navigation-123');await page.locator('#auth-form button[type=submit]').click()
 await page.locator('.mobile-nav a[href="#library"]').wait_for(state='visible')
 await page.locator('.mobile-nav a[href="#library"]').click();await page.locator('#books-library-shortcut').click()
 await page.locator('#book-grid .book-card').first.wait_for(state='visible')
 await page.locator('#book-grid .book-card a').first.click()
 await page.locator('.book-detail-actions').wait_for(state='visible')
 restart=page.locator('[data-book-restart="book-one"]')
 await (restart if await restart.count() else page.locator('.book-detail-actions [data-book-play="book-one"]')).click()
 for _ in range(150):
  starts=await page.evaluate('window.bookStarts')
  if (sleep and starts) or (not sleep and len(starts)>=2):break
  await page.wait_for_timeout(100)
 await page.wait_for_timeout(800)
 starts=await page.evaluate('window.bookStarts');seeks=await page.evaluate('window.bookSeeks')
 assert starts and starts[0].endswith('/0'),starts
 if sleep:
  assert len(starts)==1 and await page.locator('#audiobook-audio').evaluate('(audio)=>audio.paused'),starts
 else:
  assert len(starts)==2 and starts[1].endswith('/1'),starts
  assert any(s['url'].endswith('/1') and s['position']==0 for s in seeks),seeks
  assert await page.locator('#audiobook-audio').evaluate('(audio)=>audio.ended')
  for _ in range(100):
   if any(value.get('finished') and abs(value['position']-0.4)<0.001 for value in progress):break
   await page.wait_for_timeout(100)
  assert any(value.get('finished') and abs(value['position']-0.4)<0.001 for value in progress),progress
 assert not errors,errors
 await page.close();return {'sleep_at_chapter':sleep,'played_parts':len(starts),'no_opening_trim':True,'page_errors':errors}

async def main():
 async with async_playwright() as p:
  browser=await p.chromium.launch(executable_path=os.environ.get('CHROMIUM_EXECUTABLE'),args=['--no-sandbox'])
  print(json.dumps([await check(browser,False),await check(browser,True)]));await browser.close()
asyncio.run(main())

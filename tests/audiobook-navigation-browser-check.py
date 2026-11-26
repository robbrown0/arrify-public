"""Manual phone navigation check against the synthetic audiobook preview only."""
import asyncio,json,os
from playwright.async_api import async_playwright

async def main():
 async with async_playwright() as p:
  browser=await p.chromium.launch(executable_path=os.environ.get('CHROMIUM_EXECUTABLE'),headless=True,args=['--no-sandbox'])
  page=await browser.new_page(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
  errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  await page.goto('http://127.0.0.1:18850',wait_until='networkidle')
  await page.locator('#username').fill('navqualifier');await page.locator('#password').fill('Synthetic-navigation-123')
  await page.locator('#auth-form button[type=submit]').click()
  await page.locator('.mobile-nav a[href="#library"]').wait_for(state='visible')
  await page.locator('.mobile-nav a[href="#library"]').click()
  row=page.locator('.library-shortcuts.library-pinned #books-library-shortcut')
  await row.wait_for(state='visible')
  assert await row.evaluate('(n)=>!n.classList.contains("books-library-shortcut")')
  await page.screenshot(path='/tmp/arrify-books-library-standard-row.png',full_page=True)
  async def delayed(route):
   await asyncio.sleep(1);await route.continue_()
  await page.route('**/api/v1/audiobooks/library?*',delayed)
  await page.locator('.library-filters a[href="#library/audiobooks"]').click()
  await page.get_by_text('Loading audiobooks…',exact=True).wait_for(state='visible')
  assert not await page.get_by_text('Loading your music…',exact=True).count()
  await page.locator('#book-grid .book-card').first.wait_for(state='visible')
  assert await page.locator('.library-filters a[aria-current="page"]').inner_text()=='Audiobooks'
  assert 'active' in (await page.locator('.mobile-nav a[href="#library"]').get_attribute('class') or '')
  await page.unroute('**/api/v1/audiobooks/library?*',delayed)
  await page.screenshot(path='/tmp/arrify-books-standard-shelf.png',full_page=True)
  await page.locator('#book-grid .book-card a').first.click()
  await page.locator('.book-detail-actions').wait_for(state='visible')
  assert not await page.locator('.book-back-link').count()
  await page.locator('#back').click()
  await page.locator('#book-grid').wait_for(state='visible')
  await page.get_by_role('link',name='Listening settings',exact=True).click()
  await page.get_by_role('heading',name='Audiobook settings',exact=True).wait_for(state='visible')
  assert not await page.get_by_role('link',name='Back to audiobooks',exact=True).count()
  await page.locator('#back').click();await page.locator('#book-grid').wait_for(state='visible')
  assert not errors,errors
  print(json.dumps({'standard_library_row':True,'selected_library_filter':True,'audiobook_loading_copy':True,'single_back_navigation':True,'mobile_library_stays_selected':True,'page_errors':errors}))
  await browser.close()
asyncio.run(main())

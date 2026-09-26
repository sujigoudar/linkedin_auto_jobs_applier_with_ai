import asyncio, json, os
from pathlib import Path
from playwright.async_api import async_playwright
BASE='http://127.0.0.1:8765'
OUT=Path(os.environ['AUDIT_EVIDENCE']);OUT.mkdir(parents=True,exist_ok=True)
results=[]
async def run():
 async with async_playwright() as p:
  for engine in ('chromium','firefox','webkit'):
   try: browser=await getattr(p,engine).launch(headless=True)
   except Exception as e:
    results.append(dict(case='launch',engine=engine,status='BLOCKED',detail=str(e)));continue
   for view,width,height in [('desktop',1440,900),('tablet',768,1024),('phone',390,844)]:
    ctx=await browser.new_context(viewport={'width':width,'height':height})
    external=[];errors=[]
    async def route(r):
     if r.request.url.startswith(BASE+'/'):await r.continue_()
     else:external.append(r.request.url);await r.abort()
    await ctx.route('**/*',route)
    page=await ctx.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
    async def dialog(d):await d.accept()
    page.on('dialog',dialog)
    def check(case,condition,detail=None):results.append(dict(case=case,engine=engine,viewport=view,status='PASS' if condition else 'FAIL',detail=detail))
    async def fillform(form,values):
     for name,value in values.items():await page.fill(f'#{form} [name={name}]',value)
     await page.click(f'#{form} button');await page.wait_for_timeout(150)
    async def data(path):return await (await ctx.request.get(BASE+path)).json()
    try:
     await page.goto(BASE);await page.locator('#login-overlay').wait_for(state='visible')
     check('login_required',await page.locator('#app').is_hidden())
     await fillform('login-form',{'password':'wrong'})
     check('wrong_password',await page.locator('#app').is_hidden() and bool(await page.locator('#login-error').inner_text()))
     await fillform('login-form',{'password':'audit-browser-password'})
     await page.locator('#app').wait_for(state='visible');await page.evaluate('refreshAll()')
     a=f'audit_{engine}_{view}';s='source_'+a
     await fillform('account-form',{'account_id':a,'broker':'paper','multiplier':'1'})
     check('account_create',any(x['account_id']==a for x in (await data('/accounts'))['accounts']))
     await fillform('account-form',{'account_id':a,'broker':'paper','multiplier':'2'})
     check('account_update',next(x for x in (await data('/accounts'))['accounts'] if x['account_id']==a)['multiplier']==2)
     await fillform('routing-rule-form',{'source':s,'destinations':a})
     check('routing_create',any(x['source']==s for x in (await data('/routing-rules'))['routing_rules']))
     await fillform('provider-form',{'provider_id':s,'display_name':'Audit provider'})
     check('provider_create',any(x['provider_id']==s for x in (await data('/providers'))['providers']))
     await fillform('analyst-form',{'provider_id':s,'analyst_id':'test','display_name':'Audit analyst'})
     check('analyst_create',bool(next(x for x in (await data('/providers'))['providers'] if x['provider_id']==s)['analysts']))
     r=await ctx.request.post(BASE+'/webhook/'+s,data={'symbol':'AUDIT','side':'buy','quantity':5,'price':100},headers={'X-Webhook-Secret':'audit-browser-ingress'})
     check('ingress_to_order',r.status==200)
     await page.evaluate('refreshAll()')
     row=page.locator('#positions-table tr').filter(has_text=a).filter(has_text='AUDIT')
     check('position_visible',await row.count()==1)
     await row.locator('button').click();await page.wait_for_timeout(150)
     check('manual_exit',not any(x['account_id']==a for x in (await data('/positions'))['positions']))
     for symbol in ['AUDIT2','AUDIT3']:
      await ctx.request.post(BASE+'/webhook/'+s,data={'symbol':symbol,'side':'buy','quantity':1},headers={'X-Webhook-Secret':'audit-browser-ingress'})
     await page.evaluate('refreshAll()');await page.locator('#flatten-buttons button').filter(has_text=a).click();await page.wait_for_timeout(150)
     check('flatten',not any(x['account_id']==a for x in (await data('/positions'))['positions']))
     m=a+'_managed'
     await page.check('#account-form [name=managed_lifecycle]')
     await fillform('account-form',{'account_id':m,'broker':'paper','multiplier':'1'})
     await fillform('routing-rule-form',{'source':s+'_managed','destinations':m})
     await ctx.request.post(BASE+'/webhook/'+s+'_managed',data={'symbol':'MANAGED','side':'buy','quantity':10,'stop_loss':90,'price':100},headers={'X-Webhook-Secret':'audit-browser-ingress'})
     await page.evaluate('refreshAll()');check('coverage_view',m in await page.locator('#lifecycles-table').inner_text())
     await page.locator(f'button[onclick="deleteAnalyst(\'{s}\', \'test\')"]').click();await page.wait_for_timeout(150)
     check('analyst_delete',not next(x for x in (await data('/providers'))['providers'] if x['provider_id']==s)['analysts'])
     await page.locator(f'button[onclick="deleteProvider(\'{s}\')"]').click();await page.wait_for_timeout(150)
     check('provider_delete',not any(x['provider_id']==s for x in (await data('/providers'))['providers']))
     rid=next(x['id'] for x in (await data('/routing-rules'))['routing_rules'] if x['source']==s)
     await page.locator(f'button[onclick="deleteRoutingRule({rid})"]').click();await page.wait_for_timeout(150)
     check('routing_delete',not any(x['id']==rid for x in (await data('/routing-rules'))['routing_rules']))
     await page.locator(f'button[onclick="deleteAccount(\'{a}\')"]').click();await page.wait_for_timeout(150)
     check('flat_account_delete',not any(x['account_id']==a for x in (await data('/accounts'))['accounts']))
     check('no_page_overflow',not await page.evaluate('document.documentElement.scrollWidth > innerWidth + 1'))
     check('password_accessible_label',bool(await page.locator('#login-password').get_attribute('aria-label')) or await page.locator('label[for=login-password]').count()>0)
     check('pnl_charts_implemented',await page.locator('canvas,svg[role=img]').count()>0)
     payload='<img src="/audit-missing-image" onerror="window.__auditStoredXss=17">'
     await ctx.request.post(BASE+'/webhook/unrouted-audit',data={'symbol':payload,'side':'buy','quantity':1},headers={'X-Webhook-Secret':'audit-browser-ingress'})
     await page.evaluate('refreshAll()');await page.wait_for_timeout(150)
     check('untrusted_signal_cannot_execute_script',await page.evaluate('window.__auditStoredXss || null') is None)
     health=await data('/health')
     check('readiness_visible',all(health.get(k) for k in ('price_monitor_ok','reconciler_ok')) or not await page.locator('#health-dot').evaluate("e=>e.classList.contains('ok')"),health)
     await page.screenshot(path=str(OUT/f'{engine}-{view}.png'),full_page=True)
     await page.click('button[onclick="logout()"]');await page.locator('#login-overlay').wait_for(state='visible')
     check('logout',(await ctx.request.get(BASE+'/accounts')).status==401)
     check('no_js_errors',not errors,errors);check('no_external_requests',not external,external)
    except Exception as e:check('driver_completion',False,str(e))
    await ctx.close()
   await browser.close()
 (OUT/'browser-results.json').write_text(json.dumps(results,indent=2))
 print(json.dumps({'total':len(results),'pass':sum(x['status']=='PASS' for x in results),'fail':sum(x['status']=='FAIL' for x in results),'blocked':sum(x['status']=='BLOCKED' for x in results)}))
asyncio.run(run())

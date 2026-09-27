"""Exercise the offline design atlas only. This is NOT a connected-app GUI audit."""
from pathlib import Path
import json, time, os, shutil
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
start=time.time(); results=[]; errors=[]; requests=[]
source=(ROOT/'SCREEN_ATLAS.html').read_text()
with sync_playwright() as pw:
    options={'headless':True}
    executable=os.environ.get('CHROMIUM_EXECUTABLE') or shutil.which('chromium')
    if executable: options['executable_path']=executable
    if os.environ.get('ATLAS_CONTAINER_NO_SANDBOX')=='1': options['args']=['--no-sandbox']
    browser=pw.chromium.launch(**options)
    version=browser.version
    page=None
    # The container blocks file:// navigation. Render the exact self-contained
    # local document via set_content, with all external requests forbidden.
    for label,width,height in [('desktop',1440,1000),('phone',390,844)]:
        if page is not None: page.close()
        page=browser.new_page(viewport={'width':width,'height':height})
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda r:requests.append(r.url))
        page.route('**/*',lambda route:route.abort())
        page.set_content(source,wait_until='load')
        observed=page.evaluate('''() => {
          const data=JSON.parse(document.getElementById('atlas-data').textContent), out=[];
          for (const s of data.screens) {
            atlas.select(s.id);atlas.setView('layout');
            for (const state of Object.keys(data.states)) {
              atlas.setState(state);
              const heading=document.getElementById('screen-title').textContent;
              const widthOk=document.documentElement.scrollWidth<=innerWidth;
              out.push({id:s.id+'::'+state,view:'layout',passed:heading===s.title&&widthOk,
                heading, horizontal_overflow:document.documentElement.scrollWidth-innerWidth});
            }
            for(const view of ['fields','contract']) {
              atlas.setView(view);
              const heading=document.getElementById('screen-title').textContent;
              out.push({id:s.id+'::'+view,view,passed:heading===s.title&&document.documentElement.scrollWidth<=innerWidth,
                heading,horizontal_overflow:document.documentElement.scrollWidth-innerWidth});
            }
          }return out;
        }''')
        results.extend([{**r,'viewport':label} for r in observed])
    for sid,label,width,height in [('TR-01','trading_desktop',1440,1000),('AD-01','admin_desktop',1440,1000),('CU-01','customer_desktop',1440,1000),('PU-02','public_desktop',1440,1000),('CU-01','customer_phone',390,844),('AD-07','product_configuration',1440,1000)]:
        page.set_viewport_size({'width':width,'height':height})
        page.evaluate("([id,view])=>{atlas.select(id);atlas.setState('empty');atlas.setView(view)}",[sid,'fields' if sid=='AD-07' else 'layout'])
        page.screenshot(path=str(ROOT/'evidence'/f'atlas_{label}.png'),full_page=True)
        results.append({'id':sid+'::screenshot','viewport':label,'view':'fields' if sid=='AD-07' else 'layout','passed':True})
    # Actual click/navigation and theme controls, not just direct view calls.
    for sid in ['PU-01','CU-01','TR-01','AD-01']:
        page.locator(f'[data-home="{sid}"]').click()
        results.append({'id':sid+'::home_click','passed':page.evaluate("location.hash")==('#'+sid)})
    page.locator('#theme-select').select_option('light')
    results.append({'id':'THEME_LIGHT','passed':page.evaluate("document.documentElement.dataset.theme")=='light'})
    page.locator('#search').fill('trailing')
    results.append({'id':'SEARCH','passed':page.locator('#nav-list button').count()>0})
    browser.close()
report={'scope':'Offline design atlas only, not either application','document':'SCREEN_ATLAS.html','engine':'system Chromium','browser_version':version,
        'tests':len(results),'passed':sum(r['passed'] for r in results),'failed':sum(not r['passed'] for r in results),
        'javascript_errors':errors,'external_requests':requests,'elapsed_seconds':round(time.time()-start,3),
        'setup_note':'file:// navigation blocked by container policy; exact document rendered using Playwright set_content; network blocked.',
        'actual_application_tests':0,'results':results}
(ROOT/'evidence/atlas-browser-results.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k!='results'},indent=2))
if report['failed'] or errors or requests:raise SystemExit(1)

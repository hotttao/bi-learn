// Real native Explore preview, saved dashboard render and editor inspection.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from '../.local/browser/node_modules/playwright-core/index.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const env=Object.fromEntries(fs.readFileSync(path.join(root,'.env'),'utf8').split('\n')
 .filter(l=>l&&!l.startsWith('#')&&l.includes('=')).map(l=>[l.slice(0,l.indexOf('=')),l.slice(l.indexOf('=')+1)]));
const assets=JSON.parse(fs.readFileSync(path.join(root,'evidence/s6-assets.json')));
const base=new URL(assets.dashboard_url).origin;
const browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--no-proxy-server']});
const context=await browser.newContext({viewport:{width:1440,height:1100},locale:'en-US'});
const page=await context.newPage();page.setDefaultTimeout(35000);
const responses=[];
page.on('response',async response=>{
 if(response.url().includes('/api/v1/chart/data')&&response.request().method()==='POST'){
  try{responses.push({request:response.request().postDataJSON(),response:await response.json()});}catch{}
 }
});
const check=(ok,message)=>{if(!ok)throw new Error(message);};
const industry=()=>[...responses].reverse().find(r=>r.request.form_data?.viz_type==='echarts_timeseries_bar')?.response.result?.[0]?.data;
async function expectIndustry(){
 await page.waitForFunction(()=>document.querySelectorAll('canvas').length>0);
 for(let i=0;i<100;i++){
  const data=industry();
  if(data?.length===2&&data.every(r=>({软件:120,零售:125})[r.industry]===Number(r.net_revenue)))return data;
  await page.waitForTimeout(200);
 }
 throw new Error('Industry data missing/different');
}
const report={verified_at_utc:new Date().toISOString(),dashboard_url:assets.dashboard_url};
try{
 await page.goto(base+'/login/');await page.locator('#username').fill('admin');
 await page.locator('#password').fill(env.SUPERSET_ADMIN_PASSWORD);await page.locator('input[type=submit],button[type=submit]').click();
 await page.waitForURL(url=>!url.pathname.startsWith('/login'));
 responses.length=0;
 await page.goto(assets.preview.explore_url);
 report.unsaved_preview={url:page.url(),industry:await expectIndustry()};
 await page.waitForTimeout(1200);
 await page.screenshot({path:path.join(root,'evidence/s6-unsaved-preview.png'),fullPage:true});
 responses.length=0;
 await page.goto(assets.dashboard_url);
 await page.getByText('245.00',{exact:true}).waitFor();
 report.saved_dashboard={kpi:245,industry:await expectIndustry()};
 check(await page.locator(`.dashboard-chart-id-${assets.chart_ids.industry} canvas`).count()>0,'Saved bar has no canvas');
 await page.waitForTimeout(1200);
 await page.screenshot({path:path.join(root,'evidence/s6-dashboard.png'),fullPage:true});
 await page.goto(base+'/superset/welcome/');responses.length=0;await page.goto(assets.dashboard_url);
 await page.getByText('245.00',{exact:true}).waitFor();
 report.reopened={kpi:245,industry:await expectIndustry()};
 responses.length=0;
 await page.goto(base+`/explore/?slice_id=${assets.chart_ids.industry}`);
 report.saved_explore={industry:await expectIndustry()};
 await page.locator('button').filter({hasText:/^Save$/}).waitFor({state:'visible'});
 report.saved_explore.save_visible=true;
 await page.getByText('Customize',{exact:true}).click();
 await page.getByText('Horizontal',{exact:true}).click();
 await page.locator('button').filter({hasText:/^Update chart$/}).click();
 await page.locator('button').filter({hasText:/^Save$/}).click();
 const dialog=page.getByRole('dialog');
 await dialog.waitFor({state:'visible'});
 fs.writeFileSync(path.join(root,'.local/s6-save-dialog.txt'),await dialog.innerText());
 await dialog.locator('button').filter({hasText:/^Save$/}).click();
 await dialog.waitFor({state:'hidden'});
 const readSaved=async()=>{
  const response=await page.request.get(base+`/api/v1/chart/${assets.chart_ids.industry}`);
  check(response.ok(),'Saved chart readback failed');
  return JSON.parse((await response.json()).result.params);
 };
 let saved=await readSaved();check(saved.orientation==='horizontal','UI save did not persist orientation');
 responses.length=0;await page.reload();
 await expectIndustry();saved=await readSaved();check(saved.orientation==='horizontal','Orientation not retained on reopen');
 report.ui_edit={orientation:'horizontal',saved:true,reopened:true,industry:industry(),restored_by_mcp_later:true};
 await page.waitForTimeout(1000);
 await page.screenshot({path:path.join(root,'evidence/s6-ui-edited.png'),fullPage:true});
 report.editor_controls=await page.locator('[data-test]').evaluateAll(nodes=>nodes.filter(n=>/orientation|format/i.test(n.getAttribute('data-test')||'')).map(n=>({test:n.getAttribute('data-test'),text:n.textContent.slice(0,350)})));
 fs.writeFileSync(path.join(root,'evidence/s6-browser.json'),JSON.stringify(report,null,2)+'\n');
 fs.writeFileSync(path.join(root,'.local/s6-editor-inspection.txt'),await page.locator('body').innerText());
 console.log(JSON.stringify(report,null,2));
}catch(error){await page.screenshot({path:path.join(root,'.local/s6-browser-failure.png'),fullPage:true});throw error;}
finally{await browser.close();}

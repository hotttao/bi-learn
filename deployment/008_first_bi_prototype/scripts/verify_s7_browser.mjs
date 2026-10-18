// Independent UI check after the real Codex conversation; no asset edits here.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from '../.local/browser/node_modules/playwright-core/index.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const env=Object.fromEntries(fs.readFileSync(path.join(root,'.env'),'utf8').split('\n').filter(l=>l&&!l.startsWith('#')&&l.includes('=')).map(l=>[l.slice(0,l.indexOf('=')),l.slice(l.indexOf('=')+1)]));
const fixture=JSON.parse(fs.readFileSync(path.join(root,'evidence/s7-fixture.json')));
const state=JSON.parse(fs.readFileSync(path.join(root,'.local/s7-main-session.json')));
const base=new URL(fixture.dashboard_url).origin;
const browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--no-proxy-server']});
const context=await browser.newContext({viewport:{width:1440,height:1200},locale:'en-US'});
const page=await context.newPage();page.setDefaultTimeout(40000);
const responses=[];
page.on('response',async response=>{
 if(response.url().includes('/api/v1/chart/data')&&response.request().method()==='POST'){
  try{responses.push({request:response.request().postDataJSON(),response:await response.json()});}catch{}
 }
});
const check=(ok,msg)=>{if(!ok)throw new Error(msg);};
const latest=viz=>[...responses].reverse().find(r=>r.request.form_data?.viz_type===viz)?.response.result?.[0]?.data;
const date=v=>typeof v==='number'?new Date(v).toISOString().slice(0,10):String(v).slice(0,10);
async function dataMatches(){
 await page.getByText('245.0',{exact:true}).waitFor();
 await page.getByText('本看板用于固定十月净收入核验；请保留此说明。',{exact:true}).waitFor();
 for(let i=0;i<150;i++){
  const bar=latest('echarts_timeseries_bar');const trend=latest('echarts_timeseries_line');
  const industry=Object.fromEntries((bar||[]).map(r=>[r.industry,Number(r.net_revenue)]));
  const daily=Object.fromEntries((trend||[]).map(r=>[date(r.order_date),Number(r.net_revenue)]));
  if(industry.软件===120&&industry.零售===125&&Object.keys(industry).length===2&&daily['2026-10-01']===120&&daily['2026-10-02']===125&&Object.keys(daily).length===2){
   check(await page.locator(`.dashboard-chart-id-${state.bar_id} canvas`).count()>0,'Bar missing rendered canvas');
   check(await page.locator(`.dashboard-chart-id-${state.trend_id} canvas`).count()>0,'Line missing rendered canvas');
   return {kpi:245,industry,daily,manual_note_visible:true};
  }
  await page.waitForTimeout(200);
 }
 throw new Error('Rendered dashboard responses differ from expected total/industry/daily');
}
function toolPayload(call){return JSON.parse(call.result.content.filter(c=>c.type==='text').map(c=>c.text).join('\n'));}
const dashboardOnly=process.argv.includes('--dashboard-only');
const report={verified_at_utc:new Date().toISOString(),dashboard_url:fixture.dashboard_url,scope:dashboardOnly?'saved dashboard only; independent previews pending':'dashboard and independent previews'};
try{
 await page.goto(base+'/login/');await page.locator('#username').fill('admin');await page.locator('#password').fill(env.SUPERSET_ADMIN_PASSWORD);
 await page.locator('input[type=submit],button[type=submit]').click();await page.waitForURL(url=>!url.pathname.startsWith('/login'));
 responses.length=0;await page.goto(fixture.dashboard_url);report.dashboard=await dataMatches();
 const response=await page.request.get(base+`/api/v1/chart/${state.bar_id}`);check(response.ok(),'Bar REST readback failed');
 const bar=(await response.json()).result;const params=JSON.parse(bar.params);
 check(params.orientation==='horizontal'&&params.row_limit===77&&params.y_axis_format===',.1f'&&params.color_scheme==='d3Category10','Manual settings not preserved');
 report.bar={id:state.bar_id,name:bar.slice_name,orientation:params.orientation,color_scheme:params.color_scheme,row_limit:params.row_limit,y_axis_format:params.y_axis_format};
 await page.waitForTimeout(1200);await page.screenshot({path:path.join(root,'evidence/s7-dashboard.png'),fullPage:true});
 await page.goto(base+'/superset/welcome/');responses.length=0;await page.goto(fixture.dashboard_url);report.reopened=await dataMatches();
 report.previews={};
 for(const [id,type] of (dashboardOnly?[]:[['details_preview','table'],['kpi_preview','big_number']])){
  const proof=JSON.parse(fs.readFileSync(path.join(root,`evidence/s7-case-${id}.json`)));
  const call=proof.calls.find(c=>c.server==='bi008_superset'&&c.tool==='generate_chart'&&c.arguments.request.config.chart_type===type&&!c.arguments.request.save_chart);
  check(call,'Missing actual preview call '+id);const preview=toolPayload(call);responses.length=0;
  await page.goto(preview.explore_url);
  if(type==='big_number')await page.getByText('245.00',{exact:true}).waitFor();
  else{
   await page.getByRole('table').first().waitFor();
   for(const value of [70,75])await page.getByText(new RegExp('^'+value+'(?:\\.0+)?$')).first().waitFor();
   const bodyRows=await page.getByRole('table').first().locator('tbody tr').allTextContents();
   check(bodyRows.length===4,'Expected four rendered detail rows');
   const detail=latest('table');
   check(detail?.length===4&&detail.map(r=>Number(r.net_amount)).sort((a,b)=>a-b).join(',')==='50,50,70,75','Detail preview data mismatch');
  }
  report.previews[id]={url:preview.explore_url,rendered:true,saved_chart_created:false};
  await page.screenshot({path:path.join(root,`evidence/s7-${id}.png`),fullPage:true});
 }
 fs.writeFileSync(path.join(root,dashboardOnly?'evidence/s7-browser-dashboard.json':'evidence/s7-browser.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report,null,2));
}catch(error){await page.screenshot({path:path.join(root,'.local/s7-browser-failure.png'),fullPage:true});throw error;}
finally{await browser.close();}

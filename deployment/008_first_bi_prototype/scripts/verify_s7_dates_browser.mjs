// Verify user-operated native date filters and actual rendered/query results.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from '../.local/browser/node_modules/playwright-core/index.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const env=Object.fromEntries(fs.readFileSync(path.join(root,'.env'),'utf8').split('\n').filter(l=>l&&!l.startsWith('#')&&l.includes('=')).map(l=>[l.slice(0,l.indexOf('=')),l.slice(l.indexOf('=')+1)]));
const assets=JSON.parse(fs.readFileSync(path.join(root,'evidence/s7-date-assets.json')));
const base=new URL(assets.dashboard_url).origin;
const browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--no-proxy-server']});
const context=await browser.newContext({viewport:{width:1440,height:1200},locale:'en-US'});
const page=await context.newPage();page.setDefaultTimeout(30000);
const responses=[];const check=(ok,msg)=>{if(!ok)throw Error(msg);};
page.on('response',async r=>{if(r.url().includes('/api/v1/chart/data')&&r.request().method()==='POST')try{responses.push({request:r.request().postDataJSON(),response:await r.json()});}catch{}});
const latest=viz=>[...responses].reverse().find(r=>r.request.form_data?.viz_type===viz)?.response.result?.[0];
async function expect(total,days){
 for(let i=0;i<150;i++){
  const kpi=latest('big_number_total'),line=latest('echarts_timeseries_line'),bar=latest('echarts_timeseries_bar');
  if(kpi&&line&&bar&&!kpi.error&&!line.error&&!bar.error){
   const value=kpi.data?.[0]?.net_revenue;
   if((total===null?value===null:Number(value)===total)&&line.data?.length===days){
    if(total!==null){await page.getByText(total.toFixed(2),{exact:true}).waitFor();check(await page.locator('canvas').count()>=2,'Missing rendered plots');}
    check((bar.data||[]).reduce((s,r)=>s+Number(r.net_revenue),0)===(total??0),'Industry data differs');
    return {total:total,daily:line.data,industry:bar.data,query:kpi.query,rendered:true};
   }
  }
  await page.waitForTimeout(200);
 }
 throw Error('Date filter did not reach all chart queries: '+JSON.stringify(responses.map(r=>r.response.result?.[0]?.data)));
}
async function screenshot(name){
 await page.waitForTimeout(1600);await page.evaluate(()=>window.scrollTo(0,0));
 await page.screenshot({path:path.join(root,'evidence/'+name),fullPage:true});
}
async function change(start,end){
 await page.locator('[data-test="time-range-trigger"]').click();
 const fields=page.locator('input[type="text"]');const n=await fields.count();check(n>=2,'Missing date bound inputs');
 await fields.nth(n-2).fill(start);await fields.nth(n-1).fill(end);
 await page.getByRole('button',{name:/^apply$/i}).click();responses.length=0;
 await page.getByRole('button',{name:'Apply filters',exact:true}).click();
}
const report={verified_at_utc:new Date().toISOString(),dashboard_url:assets.dashboard_url,ranges:[]};
try{
 await page.goto(base+'/login/');await page.locator('#username').fill('admin');await page.locator('#password').fill(env.SUPERSET_ADMIN_PASSWORD);await page.locator('input[type=submit],button[type=submit]').click();await page.waitForURL(u=>!u.pathname.startsWith('/login'));
 await page.goto(assets.dashboard_url);report.baseline=await expect(245,2);
 for(const [start,end,total,days] of [['2026-10-01','2026-10-02',120,1],['2026-10-02','2026-10-03',125,1],['2026-09-01','2026-10-01',null,0],['2026-09-01','2026-11-01',245,2]]){
  await change(start,end);report.ranges.push({start_inclusive:start,end_exclusive:end,...await expect(total,days)});
  if(total===125)await screenshot('s7-date-single-day.png');
 }
 await change('2026-10-01','2026-11-01');await expect(245,2);await screenshot('s7-date-dashboard.png');
 report.default_restored=true;
 const proof=JSON.parse(fs.readFileSync(path.join(root,'evidence/s7-date-codex.json')));
 const call=proof.calls.find(c=>c.server==='bi008_superset'&&c.tool==='generate_chart');
 const payload=JSON.parse(call.result.content.filter(c=>c.type==='text').map(c=>c.text).join('\n'));
 responses.length=0;await page.goto(payload.explore_url);
 for(let i=0;i<100;i++){if(latest('echarts_timeseries_bar'))break;await page.waitForTimeout(200);}
 const chart=latest('echarts_timeseries_bar');check(chart?.data?.length===1&&chart.data[0].industry==='零售'&&Number(chart.data[0].net_revenue)===125,'Real Codex selected-date preview differs');
 await page.locator('canvas').first().waitFor();report.codex_preview={url:payload.explore_url,data:chart.data,rendered:true};
 await screenshot('s7-date-codex.png');
 fs.writeFileSync(path.join(root,'evidence/s7-dates-browser.json'),JSON.stringify(report,null,2)+'\n');console.log('Native date selector and real Codex preview rendered correctly');
}catch(e){await page.screenshot({path:path.join(root,'.local/s7-dates-browser-failure.png'),fullPage:true});console.log((await page.locator('body').innerText()).slice(-2000));throw e;}
finally{await browser.close();}

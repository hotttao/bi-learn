// Verify real Trino-backed chart rendering and shared native date filtering.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '../.local/browser/node_modules/playwright-core/index.mjs';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const env = Object.fromEntries(fs.readFileSync(path.join(root, '.env'), 'utf8').split('\n')
  .filter(l => l && !l.startsWith('#') && l.includes('=')).map(l => [l.slice(0,l.indexOf('=')),l.slice(l.indexOf('=')+1)]));
const assets = JSON.parse(fs.readFileSync(path.join(root, 'evidence/s4-assets.json')));
const base = new URL(assets.dashboard_url).origin;
const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome', headless: true,
  args: ['--no-sandbox', '--no-proxy-server'] });
const context = await browser.newContext({ viewport: {width: 1440, height: 1200}, locale: 'en-US' });
const page = await context.newPage();
page.setDefaultTimeout(30000);
const responses = [];
page.on('response', async r => {
  if (r.url().includes('/api/v1/chart/data') && r.request().method() === 'POST') {
    try {responses.push({request:r.request().postDataJSON(),response:await r.json()});} catch {}
  }
});
const check = (ok,msg) => {if(!ok) throw new Error(msg);};
const latest = viz => [...responses].reverse().find(r => r.request.form_data?.viz_type === viz)?.response.result?.[0]?.data;
async function expectData(kpi, daily, industries) {
  await page.getByText(kpi.toFixed(2),{exact:true}).waitFor();
  await page.waitForFunction(() => document.querySelectorAll('canvas').length >= 2);
  const end=Date.now()+30000;
  while(Date.now()<end) {
    const bar=latest('echarts_timeseries_bar');
    const got=Object.fromEntries((bar||[]).map(r=>[r.industry,r.net_revenue]));
    const trend=latest('echarts_timeseries_line')?.map(r=>r.net_revenue);
    if(JSON.stringify(trend)===JSON.stringify(daily) && Object.keys(got).length===Object.keys(industries).length &&
      Object.entries(industries).every(([k,v])=>got[k]===v)) {
      // Wait for ECharts' entrance animation before capturing the visible bars.
      await page.waitForTimeout(1500);
      return {kpi,daily,industry:got};
    }
    await page.waitForTimeout(200);
  }
  throw new Error('Three chart responses did not reconcile: '+JSON.stringify({trend:latest('echarts_timeseries_line'),bar:latest('echarts_timeseries_bar')}));
}
async function changeRange(end) {
  await page.locator('[data-test="time-range-trigger"]').click();
  await page.locator('input[type="text"]').last().fill(end);
  await page.getByRole('button',{name:/^apply$/i}).click();
  await page.getByRole('button',{name:'Apply filters',exact:true}).click();
}
const report={verified_at_utc:new Date().toISOString(),dashboard_url:assets.dashboard_url};
try {
  await page.goto(base+'/login/');
  await page.locator('#username').fill('admin');
  await page.locator('#password').fill(env.SUPERSET_ADMIN_PASSWORD);
  await page.locator('input[type=submit],button[type=submit]').click();
  await page.waitForURL(url=>!url.pathname.startsWith('/login'));
  await page.goto(assets.dashboard_url);
  report.baseline=await expectData(245,[120,125],{'软件':120,'零售':125});
  const chart=page.locator(`.dashboard-chart-id-${assets.chart_id}`);
  await chart.scrollIntoViewIfNeeded();
  check(await chart.locator('canvas').count()>0,'Industry chart has no rendered canvas');
  await page.screenshot({path:path.join(root,'evidence/s4-dashboard.png'),fullPage:true});
  await changeRange('2026-10-02');
  report.date_filter=await expectData(120,[120],{'软件':120});
  await page.screenshot({path:path.join(root,'evidence/s4-date-filter.png'),fullPage:true});
  await changeRange('2026-11-01');
  report.restored=await expectData(245,[120,125],{'软件':120,'零售':125});
  await page.goto(base+'/superset/welcome/');
  await page.goto(assets.dashboard_url);
  report.reopened=await expectData(245,[120,125],{'软件':120,'零售':125});
  fs.writeFileSync(path.join(root,'evidence/s4-browser.json'),JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify(report,null,2));
} catch(error) {
  await page.screenshot({path:path.join(root,'.local/s4-failure.png'),fullPage:true});
  throw error;
} finally {await browser.close();}

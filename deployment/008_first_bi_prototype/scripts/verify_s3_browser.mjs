// Real Chrome checks against native Superset pages; no mocked chart rendering.
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chromium } from '../.local/browser/node_modules/playwright-core/index.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const env = Object.fromEntries(fs.readFileSync(path.join(root, '.env'), 'utf8')
  .split('\n').filter(line => line && !line.startsWith('#') && line.includes('='))
  .map(line => [line.slice(0, line.indexOf('=')), line.slice(line.indexOf('=') + 1)]));
const assets = JSON.parse(fs.readFileSync(path.join(root, 'evidence/s3-assets.json'), 'utf8'));
const layoutOnly = process.argv.includes('--layout-only');
const base = `http://${env.SUPERSET_BIND_HOST || '192.168.2.46'}:8088`;
const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome', headless: true,
  args: ['--no-sandbox', '--no-proxy-server'] });
const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, locale: 'en-US' });
const page = await context.newPage();
page.setDefaultTimeout(15000);
page.setDefaultNavigationTimeout(60000);
const responses = [];
page.on('response', async response => {
  if (response.url().includes('/api/v1/chart/data') && response.request().method() === 'POST') {
    try { responses.push({ request: response.request().postDataJSON(), response: await response.json() }); } catch {}
  }
});
const check = (condition, message) => { if (!condition) throw new Error(message); };
const dashboard = async () => {
  const response = await context.request.get(`${base}/api/v1/dashboard/${assets.dashboard_id}`);
  check(response.ok(), 'Dashboard read-back failed');
  return (await response.json()).result;
};
const waitKpi = async value => {
  await page.getByText(Number(value).toFixed(2), { exact: true }).waitFor({ state: 'visible' });
};
const changeRange = async end => {
  await page.locator('[data-test="time-range-trigger"]').click();
  await page.locator('input[type="text"]').last().fill(end);
  await page.getByRole('button', { name: /^apply$/i }).click();
  await page.getByRole('button', { name: 'Apply filters', exact: true }).click();
};
const mutate = value => execFileSync('python3', ['scripts/source_db.py', 'query', '--source', 'mysql', '--role', 'admin'],
  { cwd: root, input: `UPDATE orders SET paid_amount=${value} WHERE order_id=2 AND dept_id='A';\n`, encoding: 'utf8' });
const metricData = trend => [...responses].reverse().find(item =>
  item.request.form_data?.viz_type === (trend ? 'echarts_timeseries_line' : 'big_number_total'))?.response.result?.[0]?.data;
let mutated = false;
const report = layoutOnly
  ? JSON.parse(fs.readFileSync(path.join(root, 'evidence/s3-browser.json'), 'utf8'))
  : { verified_at_utc: new Date().toISOString(), dashboard_url: assets.dashboard_url };
try {
  await page.goto(base + '/login/');
  await page.locator('#username').fill('admin');
  await page.locator('#password').fill(env.SUPERSET_ADMIN_PASSWORD);
  await page.locator('input[type=submit], button[type=submit]').click();
  await page.waitForURL(url => !url.pathname.startsWith('/login'), { timeout: 60000 });
  await page.goto(assets.dashboard_url);
  await waitKpi(245);
  await page.waitForTimeout(1000);
  check(JSON.stringify(metricData(true)?.map(row => row.net_revenue)) === '[120,125]', 'Baseline daily query differs');
  report.baseline = { kpi: 245, daily: [120, 125] };

  if (!layoutOnly) {
  await changeRange('2026-10-02');
  await waitKpi(120);
  await page.waitForTimeout(600);
  check(JSON.stringify(metricData(true)?.map(row => row.net_revenue)) === '[120]', 'Date filter did not reach trend');
  report.native_date_filter = { start_inclusive: '2026-10-01', end_exclusive: '2026-10-02', kpi: 120, daily: [120] };
  await page.screenshot({ path: path.join(root, 'evidence/s3-date-filter.png'), fullPage: true });
  await changeRange('2026-11-01');
  await waitKpi(245);
  }

  const before = (await dashboard()).position_json;
  await page.getByRole('button', { name: 'Edit dashboard', exact: true }).click();
  await page.waitForTimeout(500);
  const chartBox = await page.locator('[data-test="dashboard-component-chart-holder"]').first().boundingBox();
  const handle = page.locator('.resize-handle--bottom').first();
  const box = await handle.boundingBox();
  check(box, 'Resize handle not visible');
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2 + (chartBox.height < 380 ? 400 - chartBox.height : -48), { steps: 12 });
  await page.mouse.up();
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await page.getByRole('button', { name: 'Edit dashboard', exact: true }).waitFor();
  const saved = (await dashboard()).position_json;
  check(before !== saved, 'Resize did not change saved layout');
  await page.goto(base + '/superset/welcome/');
  await page.goto(assets.dashboard_url);
  await waitKpi(245);
  check((await dashboard()).position_json === saved, 'Layout lost after navigating away and reopening');
  report.layout = { verified_at_utc: new Date().toISOString(), resize_saved: true,
    reopen_preserved: true, position: JSON.parse(saved),
    chart_drag: 'not_verified_by_automation; manual review exercise remains' };

  if (!layoutOnly) {
  const original = execFileSync('python3', ['scripts/source_db.py', 'query', '--source', 'mysql', '--role', 'admin'],
    { cwd:root, input:"SELECT paid_amount FROM orders WHERE order_id=2 AND dept_id='A';\n", encoding:'utf8' }).trim();
  check(original === '80.00', 'Order 2 is not baseline; refusing to overwrite manual changes');
  mutated = true;
  mutate(90);
  await page.reload();
  await waitKpi(255);
  await page.waitForTimeout(600);
  check(JSON.stringify(metricData(true)?.map(row => row.net_revenue)) === '[120,135]', 'Changed order not reflected in daily trend');
  report.refresh = { changed_paid_amount: 90, kpi:255, daily:[120,135] };
  await page.screenshot({ path: path.join(root, 'evidence/s3-refresh-255.png'), fullPage: true });
  mutate(80);
  mutated = false;
  await page.reload();
  await waitKpi(245);
  await page.waitForTimeout(600);
  check(JSON.stringify(metricData(true)?.map(row => row.net_revenue)) === '[120,125]', 'Restored order not reflected');
  report.refresh.restored = { paid_amount:80, kpi:245, daily:[120,125] };
  }
  await page.screenshot({ path: path.join(root, 'evidence/s3-dashboard.png'), fullPage: true });
  fs.writeFileSync(path.join(root, 'evidence/s3-browser.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report, null, 2));
} catch (error) {
  console.log('Browser failure, page text:', (await page.locator('body').innerText()).slice(-5000));
  await page.screenshot({ path: path.join(root, '.local/s3-browser-failure.png'), fullPage: true });
  throw error;
} finally {
  if (mutated) mutate(80);
  await browser.close();
}

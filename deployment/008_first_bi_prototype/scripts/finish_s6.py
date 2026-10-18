"""S6 readback: actual results, retry identity, original assets and source baseline."""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json

from s3_assets import API, BASE
from discover_s6 import ROOT
from s6_assets import BAR_NAME, KPI_NAME, DASHBOARD_NAME, layout_ids


def main():
    assets=json.loads((ROOT/'evidence/s6-assets.json').read_text())
    first=json.loads((ROOT/'evidence/s6-creation.json').read_text())
    browser=json.loads((ROOT/'evidence/s6-browser.json').read_text())
    restored=json.loads((ROOT/'evidence/s6-ui-restore.json').read_text())
    discovery=json.loads((ROOT/'evidence/s6-discovery.json').read_text())
    source=json.loads((ROOT/'evidence/s6-source-baseline.json').read_text())
    original=json.loads((ROOT/'evidence/s2-sources.json').read_text())['reconciliation']
    assert source['snapshot_sha256']==original['snapshot_sha256']
    assert len(discovery['tools'])==24 and discovery['health']['version']=='6.1.0'
    assert assets['chart_ids']==first['chart_ids'] and assets['dashboard_id']==first['dashboard_id']
    assert first['reconciliation'] and all(not r['create_replayed'] for r in first['reconciliation'])
    assert not any(c['tool']=='generate_chart' and c['arguments']['request'].get('save_chart') for c in assets['calls'])
    assert not any(c['tool'] in ['generate_dashboard','create_virtual_dataset'] for c in assets['calls'])
    assert Decimal(assets['data']['kpi']['data'][0]['net_revenue'])==245
    industry=lambda rows:{r['industry']:Decimal(str(r['net_revenue'])) for r in rows}
    assert industry(assets['data']['industry']['data'])=={'软件':Decimal(120),'零售':Decimal(125)}
    assert industry(browser['unsaved_preview']['industry'])=={'软件':Decimal(120),'零售':Decimal(125)}
    assert browser['ui_edit']['saved'] and browser['ui_edit']['reopened']
    assert restored['restored_orientation']=='vertical'
    assert industry(restored['calls'][-1]['result']['data'])=={'软件':Decimal(120),'零售':Decimal(125)}
    api=API();cid=assets['chart_ids']['industry']
    bar=api.call('GET',f'/api/v1/chart/{cid}')['result']
    assert json.loads(bar['params'])['orientation']=='vertical'
    assert api.find('chart','slice_name',BAR_NAME)==cid
    assert api.find('chart','slice_name',KPI_NAME)==assets['chart_ids']['kpi']
    assert api.find('dashboard','dashboard_title',DASHBOARD_NAME)==assets['dashboard_id']
    dashboard=api.call('GET',f"/api/v1/dashboard/{assets['dashboard_id']}")['result']
    assert layout_ids(dashboard)==sorted(assets['chart_ids'].values())
    baseline=api.call('GET','/api/v1/dashboard/1')['result']
    assert layout_ids(baseline)==[1,2,3]
    expected=(ROOT/'sql/trino/orders_enriched_v1.sql').read_text().strip().rstrip(';')
    baseline_dataset=api.call('GET','/api/v1/dataset/2')['result']
    assert baseline_dataset['sql'].strip().rstrip(';')==expected
    assert any(m['metric_name']=='net_revenue' and m['expression'].replace(' ','')=='SUM(net_amount)' for m in baseline_dataset['metrics'])
    scoped=api.call('GET',f"/api/v1/dataset/{assets['dataset']['id']}")['result']
    assert scoped['sql'].strip().rstrip(';')==assets['dataset']['sql']
    main_mdl=hashlib.sha256((ROOT/'wren/target/mdl.json').read_bytes()).hexdigest()
    assert main_mdl==hashlib.sha256((ROOT/'evidence/w1-compiled-mdl.json').read_bytes()).hexdigest()
    report={'verified_at_utc':datetime.now(timezone.utc).isoformat(),'dashboard_url':assets['dashboard_url'],
        'dataset_id':assets['dataset']['id'],'chart_ids':assets['chart_ids'],'total':'245.00',
        'industry':{'软件':'120.00','零售':'125.00'},'repeated_pipeline_created_no_assets':True,
        'native_duplicate_add_refused_without_layout_change':assets['native_duplicate_add'],
        'real_browser_preview_rendered':True,'real_browser_edit_saved_and_reopened':True,'original_orientation_restored':True,
        'normal_dashboard_url':BASE+'/superset/dashboard/1/','normal_chart_ids':[1,2,3],
        's4_dataset_sql_unchanged':True,'source_snapshot_sha256':source['snapshot_sha256'],'wren_mdl_sha256':main_mdl,
        'scope':'S6 fixed calls only; natural-language Codex asset operations reserved for S7',
        'not_verified':assets['not_verified']}
    (ROOT/'evidence/s6-final-state.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('S6 final passed: native MCP preview/save/add/readback; browser editing; retry reconciliation; original S2/S4/Wren preserved')


if __name__=='__main__':main()

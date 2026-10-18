"""Read service/assets state, dependency compatibility and W2 evidence freshness."""
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json

import requests
from s3_assets import API, BASE
from source_db import ROOT, check
from guarded_wren_mcp import revision


def main():
    mdl = hashlib.sha256((ROOT/'wren/target/mdl.json').read_bytes()).hexdigest()
    check(mdl == hashlib.sha256((ROOT/'evidence/w1-compiled-mdl.json').read_bytes()).hexdigest(), 'W1 MDL changed')
    fixed = json.loads((ROOT/'evidence/w2-mcp-verification.json').read_text())
    check(fixed['calls']['get_runtime_contract']['mdl_sha256'] == mdl, 'Stale fixed MCP test')
    source_revision = revision(ROOT/'wren')
    check(fixed['calls']['get_runtime_contract']['source_revision'] == source_revision, 'Stale MCP knowledge/config snapshot')
    codex = json.loads((ROOT/'evidence/w2-codex-verification.json').read_text())
    check(codex['answer']['mdl_sha256'] == mdl, 'Stale Codex result')
    source = json.loads((ROOT/'evidence/w2-source-baseline.json').read_text())
    check(source['snapshot_sha256'] == json.loads((ROOT/'evidence/s2-sources.json').read_text())['reconciliation']['snapshot_sha256'], 'S2 source changed')
    deps = {line.split('==')[0]:line.split('==')[1] for line in (ROOT/'wren/requirements.lock').read_text().splitlines() if '==' in line and not line.startswith('#')}
    check(all(version(name) == pin for name,pin in deps.items()), 'W1 dependency drift')
    session = requests.Session(); session.trust_env = False
    session.get(BASE+'/health',timeout=10).raise_for_status()
    info = session.get('http://127.0.0.1:18080/v1/info',timeout=10); info.raise_for_status()
    check(not info.json()['starting'], 'Trino starting')
    assets = json.loads((ROOT/'evidence/s4-assets.json').read_text())
    api = API()
    dataset = api.call('GET',f"/api/v1/dataset/{assets['dataset_id']}")['result']
    expected = (ROOT/'sql/trino/orders_enriched_v1.sql').read_text().strip().rstrip(';')
    check(dataset['sql'].strip().rstrip(';') == expected, 'S4 dataset SQL changed')
    dashboard = api.call('GET',f"/api/v1/dashboard/{assets['dashboard_id']}")['result']
    charts = sorted(n['meta']['chartId'] for n in json.loads(dashboard['position_json']).values() if isinstance(n,dict) and n.get('type') == 'CHART')
    check(charts == [1,2,3], 'Dashboard charts changed')
    report = {'verified_at_utc':datetime.now(timezone.utc).isoformat(),'mdl_sha256':mdl,
              'source_revision':source_revision,
              'adapter_sha256':hashlib.sha256((ROOT/'scripts/guarded_wren_mcp.py').read_bytes()).hexdigest(),
              'source_snapshot_sha256':source['snapshot_sha256'],'w1_core_pins_preserved':len(deps),
              'versions':{name:version(name) for name in ['wrenai','wren-core-py','mcp']},
              'codex_actual_mcp_calls':len(codex['calls']),
              'superset':{'url':BASE+'/superset/dashboard/1/','healthy':True,'s4_sql_unchanged':True,'chart_ids':charts},
              'trino':info.json(),'scope':'W2 local stdio semantic query prototype; department authorization and automated publication NOT implemented'}
    (ROOT/'evidence/w2-final-state.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('W2 final state passed: source/MDL/core pins/Superset assets unchanged.')


if __name__ == '__main__': main()

"""Read final W1 state and prove the S4 BI assets still reference S4 SQL."""
from datetime import datetime, timezone
import hashlib
import json
import platform

import requests

from s3_assets import API, BASE
from source_db import ROOT, check


def main():
    verification=json.loads((ROOT/'evidence/w1-verification.json').read_text())
    mdl=ROOT/'wren/target/mdl.json'
    check(hashlib.sha256(mdl.read_bytes()).hexdigest()==verification['mdl_sha256'],'Verification references stale MDL')
    check(verification['fixtures']['cent_precision']['rows'][0]['net_revenue']=='245.0400000000','Cent fixture did not pass')
    session=requests.Session();session.trust_env=False
    session.get(BASE+'/health',timeout=10).raise_for_status()
    info=session.get('http://127.0.0.1:18080/v1/info',timeout=10);info.raise_for_status()
    check(not info.json()['starting'],'Trino still starting')
    api=API()
    assets=json.loads((ROOT/'evidence/s4-assets.json').read_text())
    dataset=api.call('GET',f"/api/v1/dataset/{assets['dataset_id']}")['result']
    expected=(ROOT/'sql/trino/orders_enriched_v1.sql').read_text().strip().rstrip(';')
    check(dataset['sql'].strip().rstrip(';')==expected,'S4 Superset SQL changed')
    check(any(m['metric_name']=='net_revenue' and m['expression']=='SUM(net_amount)' for m in dataset['metrics']),'S4 metric changed')
    dashboard=api.call('GET',f"/api/v1/dashboard/{assets['dashboard_id']}")['result']
    position=json.loads(dashboard['position_json'])
    charts=sorted(n['meta']['chartId'] for n in position.values() if isinstance(n,dict) and n.get('type')=='CHART')
    check(charts==[1,2,3],'Dashboard assets changed')
    (ROOT/'evidence/w1-compiled-mdl.json').write_bytes(mdl.read_bytes())
    report={'verified_at_utc':datetime.now(timezone.utc).isoformat(), 'python':platform.python_version(),
        'versions':verification['versions'],'mdl_sha256':verification['mdl_sha256'],
        'source_files':sorted(str(p.relative_to(ROOT/'wren')) for p in (ROOT/'wren').rglob('*') if p.is_file() and '/runtime/' not in str(p) and '/target/' not in str(p)),
        'source_sha256':{str(p.relative_to(ROOT/'wren')):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'wren').rglob('*') if p.is_file() and '/runtime/' not in str(p) and '/target/' not in str(p)},
        'compiled_mdl_evidence':'evidence/w1-compiled-mdl.json',
        'execution':'host project-local CLI/SDK in-process → existing Trino → S2 sources; no persistent Wren server',
        'core_queries':'passed with documented compatibility workarounds',
        'scope_policy':'7 named input probes rejected; generic qualifier protection NOT FIXED',
        'superset':{'url':BASE+'/superset/dashboard/1/','healthy':True,'dataset_id':assets['dataset_id'],'sql_unchanged':True,'chart_ids':charts},
        'trino':info.json(),'not_verified':verification['not_verified']}
    (ROOT/'evidence/w1-final-state.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

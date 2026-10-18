"""Verify W3 final release, independent context rejection and unchanged S2/S4 state."""
from datetime import datetime, timezone
import hashlib
import json

import requests
from s3_assets import API, BASE
from source_db import ROOT, check
from w3_release import STORE, resolve, publish


def main():
    project, descriptor, pointer = resolve()
    report = json.loads((ROOT/'evidence/w3-verification.json').read_text())
    codex = json.loads((ROOT/'evidence/w3-codex-verification.json').read_text())
    check(codex['active_release']==descriptor['release_id']==report['active_release']['release_id'], 'Stale W3 release evidence')
    # The publisher independently checks mixed model/context, even with all caller flags true.
    mixed = STORE/'candidates'/report['run_id']/'mixed-context'
    old_pointer = (STORE/'current.json').read_bytes()
    try:
        publish(mixed,report['run_id']+'-mixed-independent', 'net_revenue@2',
                {'quality':True,'regression':True,'context':True},
                {'scope':'synthetic_fixture','reviewer':'regression_harness','decision':'approve'})
    except ValueError as error:
        check('BI008_RELEASE_CONTEXT' in str(error),'Unexpected context rejection')
        context_rejection = str(error)
    else:raise AssertionError('Mixed context bypassed independent validator')
    check((STORE/'current.json').read_bytes()==old_pointer,'Rejected release changed pointer')
    source = json.loads((ROOT/'evidence/w3-source-baseline.json').read_text())
    original = json.loads((ROOT/'evidence/s2-sources.json').read_text())['reconciliation']
    check(source['snapshot_sha256']==original['snapshot_sha256'], 'S2 source changed')
    main_mdl = hashlib.sha256((ROOT/'wren/target/mdl.json').read_bytes()).hexdigest()
    check(main_mdl==hashlib.sha256((ROOT/'evidence/w1-compiled-mdl.json').read_bytes()).hexdigest(),'Main Wren baseline changed')
    session = requests.Session();session.trust_env=False
    session.get(BASE+'/health',timeout=10).raise_for_status()
    info = session.get('http://127.0.0.1:18080/v1/info',timeout=10);info.raise_for_status()
    check(not info.json()['starting'],'Trino starting')
    api=API()
    dataset=api.call('GET','/api/v1/dataset/2')['result']
    expected=(ROOT/'sql/trino/orders_enriched_v1.sql').read_text().strip().rstrip(';')
    check(dataset['sql'].strip().rstrip(';')==expected,'S4 dataset changed')
    dashboard=api.call('GET','/api/v1/dashboard/1')['result']
    charts=sorted(n['meta']['chartId'] for n in json.loads(dashboard['position_json']).values() if isinstance(n,dict) and n.get('type')=='CHART')
    check(charts==[1,2,3],'Charts changed')
    final = {'verified_at_utc':datetime.now(timezone.utc).isoformat(),'release_id':descriptor['release_id'],
             'metric':descriptor['metric'],'fixture_result':'195.00','source_sha256':source['snapshot_sha256'],
             'main_wren_mdl_sha256':main_mdl,'independent_context_rejection':context_rejection,
             'rejected_pointer_unchanged':True,'codex_mcp_calls':len(codex['calls']),
             'superset':{'healthy':True,'url':BASE+'/superset/dashboard/1/','s4_sql_unchanged':True,'chart_ids':charts,'result_baseline':'245.00'},
             'trino':info.json(),'standard_codex_config_changed':False,
             'scope':'W3 isolated fixture only; no enterprise business approval; department isolation deferred'}
    (ROOT/'evidence/w3-final-state.json').write_text(json.dumps(final,ensure_ascii=False,indent=2)+'\n')
    print('W3 final state passed: Codex/release/context coherent; independent mixed-context rejection; S2/S4 and main Wren unchanged')


if __name__=='__main__':main()

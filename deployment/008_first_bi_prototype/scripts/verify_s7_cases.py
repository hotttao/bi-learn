"""Run independent Chinese questions against real Codex and the verified MCPs."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import re
from pathlib import Path

from prepare_s7 import ROOT, save, validate_binding
from s3_assets import API
from verify_s7_codex import invoke, collect, decode, assert_wren_contract

CASES=json.loads((ROOT/'s7/questions.json').read_text())


def check_case(case,collect_only=False):
    binding=json.loads((ROOT/'evidence/s7-binding.json').read_text());validate_binding(binding)
    prefix='s7-case-'+case['id'];proof=ROOT/f'evidence/{prefix}.json'
    if proof.exists() and not collect_only:
        print(case['id'],'already passed; preserving prior evidence',flush=True);return
    before=API().call('GET','/api/v1/chart/?q=(page_size:100)')['count']
    if not collect_only:
        invoke(prefix,case['prompt'],readonly=True,allow_preview=bool(case.get('preview')))
    calls,answer,thread,events=collect(prefix)
    assert answer['status'] in case['statuses'],answer
    writes=[c for c in calls if c['server']=='bi008_superset' and c['tool'] in ['generate_chart','update_chart','add_chart_to_existing_dashboard']]
    assert not any(c['tool']!='generate_chart' or c['arguments']['request'].get('save_chart') for c in writes),'Unexpected saved mutation'
    if case.get('no_query'):
        assert not any(c['server']=='bi008_wren' and c['tool'] in ['run_sql','query_cube','dry_run'] for c in calls),'Queried before resolving an unsupported/ambiguous question'
    results=assert_wren_contract(calls,binding) if case.get('query') else []
    if 'total' in case:
        assert Decimal(answer['total'])==Decimal(case['total'])
        amounts=[Decimal(str(v)) for result in results for row in result['rows'] for v in row.values()
                 if isinstance(v,(str,int)) and re.fullmatch(r'\d+(\.\d+)?',str(v))]
        assert Decimal(case['total']) in amounts,'Expected money not present in real Wren query'
    if case['id']=='no_data':
        assert answer['total'] is None
        assert any(v is None or v==0 for result in results for row in result['rows'] for v in row.values()),'No evidence of empty data'
    if case['id']=='relative_month':assert '2026-10-01' in answer['summary'] and '2026-11-01' in answer['summary']
    if case['id']=='metric_redefinition':
        assert '不能' in answer['summary'] and '财务' in answer['summary'] and '缺少' in answer['summary']
        assert not writes,'Redefined metric through chart writes'
    if case['id']=='missing_customer_rule':assert '未知' in answer['summary'] and '保留' in answer['summary']
    if case.get('preview'):
        previews=[c for c in writes if c['arguments']['request']['config']['chart_type']==case['preview']]
        assert previews,'Expected native chart type preview missing'
        assert all(decode(c).get('success') and decode(c).get('explore_url') and decode(c).get('chart') is None for c in previews)
    if case['id']=='details_preview':
        actual={r['order_id']:(r['dept_id'],r['industry'],Decimal(r['net_amount'])) for r in answer['rows']}
        assert actual=={1:('A','软件',Decimal(70)),2:('A','零售',Decimal(75)),3:('B','软件',Decimal(50)),4:('B','零售',Decimal(50))}
        assert all(r['order_date'] in ['2026-10-01','2026-10-02'] for r in answer['rows'])
    after=API().call('GET','/api/v1/chart/?q=(page_size:100)')['count']
    assert before==after,'Independent question created a saved chart'
    save(proof,{'verified_at_utc':datetime.now(timezone.utc).isoformat(),'case':case['id'],'prompt':case['prompt'],
                'expected':{k:v for k,v in case.items() if k not in ['id','prompt']},'calls':calls,'answer':answer,
                'new_saved_charts':0,'checks_passed':True,'scope':'one real independent Codex question; no department isolation claim'})
    print(case['id'],'passed;',len(calls),'actual MCP calls',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--case',choices=['all']+[c['id'] for c in CASES],default='all');parser.add_argument('--collect-only',action='store_true')
    args=parser.parse_args()
    for case in CASES:
        if args.case in ['all',case['id']]:check_case(case,args.collect_only)

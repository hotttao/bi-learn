"""A real Codex question with a user-selected single-day range on dataset 2."""
from datetime import datetime, timezone
from decimal import Decimal
import json
import argparse
from prepare_s7 import ROOT,save,validate_binding
import verify_s7_codex as runner
from s3_assets import API


def main(collect_only=False):
    binding=json.loads((ROOT/'evidence/s7-date-binding.json').read_text());validate_binding(binding)
    runner.CWD=ROOT/'.local/s7-date-analysis'
    prefix='s7-date-codex';proof=ROOT/f'evidence/{prefix}.json'
    if proof.exists() and not collect_only:print('Date question already passed; preserving evidence');return
    before=API().call('GET','/api/v1/chart/?q=(page_size:100)')['count']
    prompt='只看2026年10月2日这一天的净销售额，按当前行业比较。先通过Wren实际核对合计，再使用全日期数据集做行业柱状图预览，不能包含10月1日，不保存。输出合计并在summary提供真实预览链接。'
    if not collect_only:runner.invoke(prefix,prompt,readonly=True,allow_preview=True)
    calls,answer,thread,events=runner.collect(prefix);results=runner.assert_wren_contract(calls,binding)
    assert answer['status']=='previewed' and Decimal(answer['total'])==125,answer
    assert any(Decimal(str(row.get('net_revenue',row.get('total',0))))==125 for result in results for row in result['rows'])
    previews=[c for c in calls if c['server']=='bi008_superset' and c['tool']=='generate_chart']
    assert previews
    for c in previews:
        req=c['arguments']['request'];assert req['dataset_id']==2 and not req.get('save_chart')
        assert req['config']['x']['name']=='industry'
        assert {(f['op'],f['value']) for f in req['config']['filters'] if f['column']=='order_date'}=={('>=','2026-10-02'),('<','2026-10-03')}
        assert runner.decode(c)['success']
    assert not any(c['tool'] in ['update_chart','add_chart_to_existing_dashboard'] for c in calls)
    assert API().call('GET','/api/v1/chart/?q=(page_size:100)')['count']==before
    save(proof,{'verified_at_utc':datetime.now(timezone.utc).isoformat(),'prompt':prompt,'calls':calls,'answer':answer,'checks_passed':True,'new_saved_charts':0})
    print('Real Codex selected-date question passed;',len(calls),'MCP calls')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--collect-only',action='store_true');main(parser.parse_args().collect_only)

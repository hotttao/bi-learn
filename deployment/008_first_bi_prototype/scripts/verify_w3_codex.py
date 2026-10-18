"""Verify a fresh Codex session consumes the active W3 release, not W2 context."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import subprocess

from w3_release import ROOT, resolve


def main(collect_only=False):
    project, descriptor, pointer = resolve()
    if descriptor['metric'] != 'net_revenue@2':raise SystemExit('Run verify_w3.py first; final active fixture must be metric v2')
    args = [str(ROOT/'.local/codex-bin'),'exec','--ephemeral','--json','-s','read-only',
            '--disable','shell_tool','--disable','unified_exec','--disable','multi_agent',
            '-c','approval_policy="never"',
            '-c','mcp_servers.bi008_wren.command='+json.dumps(str(ROOT/'scripts/w3_mcp.sh')),
            '--output-schema',str(ROOT/'codex/w3-answer.schema.json'),
            '-o',str(ROOT/'.local/w3-codex-answer.json'),(ROOT/'codex/w3-prompt.txt').read_text()]
    if not collect_only:
        with (ROOT/'.local/w3-codex-events.jsonl').open('w') as out,(ROOT/'.local/w3-codex-stderr.log').open('w') as err:
            subprocess.run(args,cwd=ROOT,stdout=out,stderr=err,check=True,timeout=300)
    events = [json.loads(line) for line in (ROOT/'.local/w3-codex-events.jsonl').read_text().splitlines()]
    assert any(e['type']=='turn.completed' for e in events)
    items = [e['item'] for e in events if e['type']=='item.completed']
    assert all(i['type'] in ['agent_message','reasoning','mcp_tool_call'] for i in items)
    calls = [i for i in items if i['type']=='mcp_tool_call']
    assert calls and all(c['server']=='bi008_wren' and not c.get('error') for c in calls)
    payload = lambda c:json.loads(c['result']['content'][0]['text'])
    contract = next(payload(c) for c in calls if c['tool']=='get_runtime_contract')
    assert contract['release_id']==descriptor['release_id'] and contract['metric']=='net_revenue@2'
    assert contract['active_release_matches_session']
    assert any(c['tool']=='get_instructions' and "'paid'" in str(payload(c)) and 'net_revenue@2' in str(payload(c)) for c in calls)
    queries = [c for c in calls if c['tool']=='run_sql']
    assert len(queries)==3
    for c in queries:
        assert not payload(c)['truncated']
        for tool in ['dry_plan','dry_run']:
            assert any(p['tool']==tool and p['arguments']['sql']==c['arguments']['sql'] for p in calls[:calls.index(c)])
    answer = json.loads((ROOT/'.local/w3-codex-answer.json').read_text())
    assert answer['release_id']==descriptor['release_id'] and answer['metric']=='net_revenue@2'
    from w3_release import digest
    assert answer['mdl_sha256']==contract['mdl_sha256']==digest((project/'target/mdl.json').read_bytes())
    assert answer['eligible_statuses']==['paid'] and Decimal(answer['total'])==195
    assert {r['name']:Decimal(r['amount']) for r in answer['industry']}=={'软件':Decimal(70),'零售':Decimal(125)}
    assert {r['date']:Decimal(r['amount']) for r in answer['daily']}=={'2026-10-01':Decimal(70),'2026-10-02':Decimal(125)}
    # Check real returned values too; final answer alone is insufficient.
    returned = [payload(c)['rows'] for c in queries]
    money = []
    for rows in returned:
        numeric = [v for row in rows for v in row.values() if isinstance(v,str) and v.replace('.','',1).isdigit()]
        money.append(sorted(Decimal(v) for v in numeric))
    assert sorted(money)==sorted([[Decimal(195)],[Decimal(70),Decimal(125)],[Decimal(70),Decimal(125)]])
    assert resolve()[2]==pointer,'Release changed during Codex run'
    report = {'verified_at_utc':datetime.now(timezone.utc).isoformat(),'client':subprocess.check_output([str(ROOT/'.local/codex-bin'),'--version'],text=True).strip(),
              'config_override':'one exec process only; standard bi008_wren config unchanged',
              'active_release':descriptor['release_id'],'calls':calls,'answer':answer,
              'checks':{'actual_tool_results_checked':True,'new_metric_rules_used':True,'release_matched':True,
                        'sql_planned_and_dry_run':True,'no_shell_or_delegation':True}}
    (ROOT/'evidence/w3-codex-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('W3 Codex passed:',len(calls),'MCP calls; metric v2, 195 / 70+125; model and context same release')


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--collect-only',action='store_true')
    main(parser.parse_args().collect_only)

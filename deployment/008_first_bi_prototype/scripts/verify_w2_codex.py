"""Run the installed Codex as the MCP product under test; no development delegation."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def run(case):
    local = ROOT/'.local'
    prefix = 'w2-codex' if case == 'baseline' else 'w2-codex-'+case
    args = [str(local/'codex-bin'), 'exec', '--ephemeral', '--json', '-s', 'read-only',
            '--disable', 'shell_tool', '--disable', 'unified_exec', '--disable', 'multi_agent',
            '-c', 'approval_policy="never"']
    if case in ('baseline','synonym'):
        args += ['--output-schema', str(ROOT/'codex/w2-answer.schema.json')]
    args += ['-o', str(local/f'{prefix}-answer.json'),
             (ROOT/'codex'/('w2-prompt.txt' if case == 'baseline' else 'w2-'+case+'-prompt.txt')).read_text()]
    with (local/f'{prefix}-events.jsonl').open('w') as out, (local/f'{prefix}-stderr.log').open('w') as err:
        subprocess.run(args, cwd=ROOT, stdout=out, stderr=err, check=True, timeout=300)


def collect(case):
    prefix = 'w2-codex' if case == 'baseline' else 'w2-codex-'+case
    events = [json.loads(line) for line in (ROOT/f'.local/{prefix}-events.jsonl').read_text().splitlines()]
    assert any(e['type'] == 'turn.completed' for e in events)
    items = [e['item'] for e in events if e['type'] == 'item.completed']
    assert all(i['type'] in ('agent_message', 'reasoning', 'mcp_tool_call') for i in items), 'Unexpected shell/delegation action'
    calls = [i for i in items if i['type'] == 'mcp_tool_call']
    assert calls and all(i['server'] == 'bi008_wren' and not i.get('error') for i in calls)
    def payload(call):
        return json.loads(call['result']['content'][0]['text'])
    answer_text = (ROOT/f'.local/{prefix}-answer.json').read_text()
    checks = {}
    if case in ('baseline','synonym'):
        answer = json.loads(answer_text)
        mdl_hash = hashlib.sha256((ROOT/'wren/target/mdl.json').read_bytes()).hexdigest()
        assert answer['mdl_sha256'] == mdl_hash
        assert Decimal(answer['total']) == Decimal('245')
        assert {r['name']:Decimal(r['amount']) for r in answer['industry']} == {'软件':Decimal('120'),'零售':Decimal('125')}
        assert {r['date']:Decimal(r['amount']) for r in answer['daily']} == {'2026-10-01':Decimal('120'),'2026-10-02':Decimal('125')}
        queries = [c for c in calls if c['tool'] == 'run_sql']
        assert len(queries) == 3
        for call in queries:
            sql = call['arguments']['sql']
            for tool in ['dry_plan','dry_run']:
                assert any(c['tool'] == tool and c['arguments']['sql'] == sql for c in calls[:calls.index(call)])
            data = payload(call)
            assert not data['truncated']
            assert all(isinstance(row['amount'],str) for row in data['rows'])
            if data['row_count'] == 1:
                assert Decimal(data['rows'][0]['amount']) == Decimal('245')
            elif 'name' in data['rows'][0]:
                assert {r['name']:Decimal(r['amount']) for r in data['rows']} == {'软件':Decimal('120'),'零售':Decimal('125')}
            else:
                assert {r['date']:Decimal(r['amount']) for r in data['rows']} == {'2026-10-01':Decimal('120'),'2026-10-02':Decimal('125')}
        checks = {'actual_query_results_checked':True,'each_query_planned_and_dry_run':True,'money_strings':True}
    else:
        assert not any(c['tool'] in ['run_sql','query_cube'] for c in calls), 'Ambiguous question executed before clarification'
        assert '时间' in answer_text and any(s in answer_text for s in ['净收入','实付','收入确认'])
        answer = answer_text
        checks = {'no_data_query_before_clarification':True,'time_and_metric_clarification_present':True}
    report = {'verified_at_utc':datetime.now(timezone.utc).isoformat(),'case':case,
              'client':subprocess.check_output([str(ROOT/'.local/codex-bin'),'--version'],text=True).strip(),
              'transport':'real Codex CLI → stdio MCP → Wren → Trino',
              'shell_and_multi_agent_disabled':True,'calls':calls,'answer':answer,'checks':checks}
    (ROOT/f'evidence/{prefix}-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    if case == 'baseline':
        config_path = ROOT/'evidence/w2-codex-config.json'
        config = json.loads(config_path.read_text())
        config['model_session_invocation_verified'] = True
        config['invocation_evidence'] = 'evidence/w2-codex-verification.json'
        config_path.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n')
    print(case, 'passed;', len(calls), 'actual MCP calls')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--case',choices=['baseline','ambiguity','synonym'],default='baseline')
    parser.add_argument('--collect-only',action='store_true',help='Verify a previously completed local run')
    args = parser.parse_args()
    if not args.collect_only:
        run(args.case)
    collect(args.case)

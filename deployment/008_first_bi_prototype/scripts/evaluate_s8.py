"""Schema-only vs identical schema plus reviewed rules, using the installed Codex.

Every trial is an isolated real product invocation, not development delegation.
The MCP/tools/data/model config are held constant; only project rule text differs.
"""
import argparse
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation
import json
import re
from pathlib import Path
import subprocess
import tempfile
import time

from configure_w2 import TOOLS
from prepare_s7 import ROOT,save
from verify_s7_codex import decode

QUESTIONS={
 'current_industry':'统计2026年全年的净销售额，按客户当前行业分组，并给出不重复总额。',
 'historical_industry':'统计2026年全年的净销售额，按下单时客户行业分组，并给出不重复总额。',
 'overlapping_tags':'统计2026年全年的净销售额，按客户标签分组，允许同一客户属于多个标签；保留没有标签的订单，并给出不重复总额，解释分组金额能否相加。'}


def evaluate(condition,question,rep,collect_only=False):
    prefix=f's8-{condition}-{question}-{rep}';proof=ROOT/f'evidence/{prefix}.json'
    if proof.exists() and not collect_only:return json.loads(proof.read_text())
    project=ROOT/f'.local/s8/catalogs/500/{condition}'
    events_path=ROOT/f'.local/{prefix}-events.jsonl';answer_path=ROOT/f'.local/{prefix}-answer.json'
    elapsed=json.loads(proof.read_text()).get('elapsed_seconds') if collect_only and proof.exists() else None
    if not collect_only:
      # Outside the repository: parent BI instructions cannot leak the reviewed rules.
      with tempfile.TemporaryDirectory(prefix='bi008-s8-analysis-') as tmp:
        Path(tmp,'AGENTS.md').write_text((ROOT/'s8/ANALYSIS.md').read_text())
        args=[str(ROOT/'.local/codex-bin'),'exec','--ephemeral','--skip-git-repo-check','--json',
              '--disable','shell_tool','--disable','unified_exec','--disable','multi_agent',
              '-c','approval_policy="never"','-c','sandbox_mode="read-only"',
              '-c','mcp_servers.bi008_s8.command='+json.dumps(str(ROOT/'scripts/s8_wren_mcp.sh')),
              '-c','mcp_servers.bi008_s8.args='+json.dumps([str(project)]),
              '-c','mcp_servers.bi008_s8.enabled_tools='+json.dumps(TOOLS),
              '-c','mcp_servers.bi008_s8.required=true','-c','mcp_servers.bi008_s8.tool_timeout_sec=90',
              '--output-schema',str(ROOT/'s8/answer.schema.json'),'-o',str(answer_path),QUESTIONS[question]]
        start=time.perf_counter()
        with events_path.open('w') as out,(ROOT/f'.local/{prefix}-stderr.log').open('w') as err:
            result=subprocess.run(args,cwd=tmp,stdout=out,stderr=err,timeout=420)
        elapsed=round(time.perf_counter()-start,3)
        if result.returncode:
            save(proof,{'condition':condition,'question':question,'rep':rep,'status':'execution_failed','cli_exit':result.returncode,'elapsed_seconds':elapsed,'numeric_correct':False})
            return json.loads(proof.read_text())
    events=[json.loads(l) for l in events_path.read_text().splitlines()];assert any(e['type']=='turn.completed' for e in events)
    items=[e['item'] for e in events if e['type']=='item.completed'];assert all(i['type'] in ['agent_message','reasoning','mcp_tool_call'] for i in items)
    calls=[i for i in items if i['type']=='mcp_tool_call'];assert all(c['server']=='bi008_s8' and c['tool'] in TOOLS for c in calls)
    answer=json.loads(answer_path.read_text());gold=json.loads((ROOT/'s8/expected-relations.json').read_text())
    queried=[c for c in calls if c['tool']=='run_sql' and not c.get('error') and isinstance(decode(c),dict) and 'rows' in decode(c)]
    ordered=True
    for c in queried:
        for tool in ['dry_plan','dry_run']:
            ordered &= any(p['tool']==tool and p['arguments'].get('sql')==c['arguments']['sql'] for p in calls[:calls.index(c)])
    try:
        grouped={r['label']:Decimal(r['amount']) for r in answer['groups']}
        numeric=len(answer['groups'])==len(gold[question]) and answer['status']=='answered' and Decimal(answer['total'])==Decimal(gold['total']) and grouped=={k:Decimal(v) for k,v in gold[question].items()}
    except (InvalidOperation,TypeError):numeric=False
    # Check actual result values, not only final model prose.
    actual_values=[]
    for c in queried:
        for row in decode(c)['rows']:
            for value in row.values():
                try:actual_values.append(Decimal(str(value)))
                except InvalidOperation:pass
    actual_groups=all(any(row.get(key)==label and any(Decimal(str(v))==Decimal(amount) for v in row.values() if isinstance(v,(int,float,Decimal)) or isinstance(v,str) and re.fullmatch(r'-?\d+(\.\d+)?',v))
                       for c in queried for row in decode(c)['rows'] for key in row) for label,amount in gold[question].items())
    total_observed=Decimal(gold['total']) in actual_values or question!='overlapping_tags' and sum(Decimal(v) for v in gold[question].values())==Decimal(gold['total']) and actual_groups
    executed=numeric and queried and actual_groups and total_observed
    result={'verified_at_utc':datetime.now(timezone.utc).isoformat(),'condition':condition,'question':question,'rep':rep,'status':answer['status'],
            'elapsed_seconds':elapsed,'calls':calls,'answer':answer,'numeric_correct':bool(executed),'plan_checks_before_execution':bool(ordered),
            'clarified':answer['status']=='needs_clarification','answered_without_supplied_business_rules':condition=='schema_only' and answer['status']=='answered',
            'usage':next((e['usage'] for e in events if e['type']=='turn.completed'),None),
            'scope':'500 model aliases; 7 real business tables plus empty-stub synthetic noise; no baseline parent instructions'}
    save(proof,result);print(condition,question,rep,result['status'],'correct',result['numeric_correct'],flush=True);return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--condition',choices=['all','schema_only','schema_and_rules'],default='all');parser.add_argument('--collect-only',action='store_true');args=parser.parse_args()
    results=[]
    # Alternate conditions per question/repetition rather than all one condition first.
    for rep in [1,2]:
      for question in QUESTIONS:
       for condition in ['schema_only','schema_and_rules']:
        if args.condition in ['all',condition]:results.append(evaluate(condition,question,rep,args.collect_only))
    summary={}
    for condition in ['schema_only','schema_and_rules']:
        rows=[r for r in results if r['condition']==condition]
        summary[condition]={'trials':len(rows),'numeric_correct':sum(bool(r.get('numeric_correct')) for r in rows),'clarifications':sum(r.get('status')=='needs_clarification' for r in rows),'execution_failures':sum(r.get('status')=='execution_failed' for r in rows),
            'model_elapsed_seconds':round(sum(r.get('elapsed_seconds') or 0 for r in rows),3),
            'tool_calls':sum(len(r.get('calls',[])) for r in rows),
            'usage':{k:sum((r.get('usage') or {}).get(k,0) for r in rows) for k in ['input_tokens','cached_input_tokens','output_tokens']}}
    save(ROOT/'evidence/s8-agent-comparison.json',{'verified_at_utc':datetime.now(timezone.utc).isoformat(),'conditions':summary,
       'trial_files':[f"s8-{r['condition']}-{r['question']}-{r['rep']}.json" for r in results],
       'limits':['only two repetitions per question, not statistical proof','same schema in both conditions, only rules differ','noise aliases do not measure 500 real source-table discovery','grep/full-context backend, no semantic vector retrieval claim']})
    print(json.dumps(summary),flush=True)


if __name__=='__main__':main()

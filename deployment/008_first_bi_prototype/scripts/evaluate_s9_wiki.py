"""Actual Codex incremental file reads + Wren query-only MCP; no metadata MCP."""
import argparse
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from prepare_s7 import ROOT,save
from prepare_s9_wiki import RELEASE,verify_release
from evaluate_s8 import QUESTIONS
from verify_s7_codex import decode

TOOLS=['dry_plan','dry_run','run_sql']
NEEDED={'current_industry':{'sales_orders','sales_refunds','crm_customers'},'historical_industry':{'sales_orders','sales_refunds','crm_industry_history'},'overlapping_tags':{'sales_orders','sales_refunds','crm_customer_tags'}}


def trial(question,rep,collect_only=False):
    prefix=f's9-wiki-{question}-{rep}';proof=ROOT/f'evidence/{prefix}.json'
    if proof.exists() and not collect_only:return json.loads(proof.read_text())
    verify_release();elapsed=json.loads(proof.read_text()).get('elapsed_seconds') if proof.exists() else None
    if not collect_only:
      with tempfile.TemporaryDirectory(prefix='bi008-s9-wiki-') as tmp:
        shutil.copytree(RELEASE,tmp,dirs_exist_ok=True);verify_release(Path(tmp))
        args=[str(ROOT/'.local/codex-bin'),'exec','--ephemeral','--skip-git-repo-check','--json','--disable','multi_agent',
          '-c','approval_policy="never"','-c','sandbox_mode="read-only"',
          '-c','mcp_servers.bi008_s9_query.command='+json.dumps(str(ROOT/'scripts/s9_wiki_query_mcp.sh')),
          '-c','mcp_servers.bi008_s9_query.enabled_tools='+json.dumps(TOOLS),
          '-c','mcp_servers.bi008_s9_query.default_tools_approval_mode="approve"','-c','mcp_servers.bi008_s9_query.required=true',
          '--output-schema',str(ROOT/'s8/answer.schema.json'),'-o',str(ROOT/f'.local/{prefix}-answer.json'),QUESTIONS[question]]
        start=time.perf_counter()
        with (ROOT/f'.local/{prefix}-events.jsonl').open('w') as out,(ROOT/f'.local/{prefix}-stderr.log').open('w') as err:
            subprocess.run(args,cwd=tmp,stdout=out,stderr=err,check=True,timeout=420)
        elapsed=round(time.perf_counter()-start,3);verify_release(Path(tmp))
    events=[json.loads(l) for l in (ROOT/f'.local/{prefix}-events.jsonl').read_text().splitlines()];assert any(e['type']=='turn.completed' for e in events)
    items=[e['item'] for e in events if e['type']=='item.completed'];assert all(i['type'] in ['agent_message','reasoning','mcp_tool_call','command_execution'] for i in items)
    calls=[i for i in items if i['type']=='mcp_tool_call'];assert calls and all(c['server']=='bi008_s9_query' and c['tool'] in TOOLS and not c.get('error') for c in calls)
    commands=[i for i in items if i['type']=='command_execution']
    assert all(c.get('exit_code')==0 and c.get('status')=='completed' for c in commands),commands
    text='\n'.join(str(c.get('command','')) for c in commands)
    read_models=set(re.findall(r'wren/models/([A-Za-z0-9_]+)/metadata.yml',text));assert read_models==NEEDED[question],(read_models,NEEDED[question],text)
    assert 'wiki/index.md' in text and 'SKILL.md' in text and 'wiki/metrics/net-revenue.md' in text
    assert not any(x in text for x in ['expected-relations','s8/expected','evidence/','/home/tao/code/','python ','curl ','wget ','wren query']),text
    queries=[c for c in calls if c['tool']=='run_sql']
    for c in queries:
        prior=calls[:calls.index(c)]
        plans=[i for i,p in enumerate(prior) if p['tool']=='dry_plan' and p['arguments']['sql']==c['arguments']['sql']]
        checks=[i for i,p in enumerate(prior) if p['tool']=='dry_run' and p['arguments']['sql']==c['arguments']['sql']]
        assert plans and checks and min(plans)<max(checks),'dry_plan → dry_run → run_sql required'
        assert not decode(c).get('error'),decode(c)
    answer=json.loads((ROOT/f'.local/{prefix}-answer.json').read_text());gold=json.loads((ROOT/'s8/expected-relations.json').read_text())
    assert answer['status']=='answered' and Decimal(answer['total'])==Decimal(gold['total'])
    assert len(answer['groups'])==len(gold[question]) and {r['label']:Decimal(r['amount']) for r in answer['groups']}=={k:Decimal(v) for k,v in gold[question].items()}
    rows=[r for c in queries for r in decode(c)['rows']];values=[]
    for row in rows:
        for v in row.values():
            try:values.append(Decimal(str(v)))
            except InvalidOperation:pass
    for label,amount in gold[question].items():
        assert any(label in row.values() and any(Decimal(str(v))==Decimal(amount) for v in row.values() if isinstance(v,(int,float)) or isinstance(v,str) and re.fullmatch(r'-?\d+(\.\d+)?',v)) for row in rows)
    assert Decimal(gold['total']) in values or question!='overlapping_tags' and sum(Decimal(v) for v in gold[question].values())==Decimal(gold['total'])
    result={'verified_at_utc':datetime.now(timezone.utc).isoformat(),'question':question,'rep':rep,'elapsed_seconds':elapsed,'numeric_correct':True,
       'metadata_mcp_calls':0,'read_models':sorted(read_models),'file_commands':commands,'calls':calls,'answer':answer,
       'usage':next(e['usage'] for e in events if e['type']=='turn.completed')}
    save(proof,result);print(question,rep,'passed;',len(commands),'file calls;',len(calls),'query MCP calls',flush=True);return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--collect-only',action='store_true');args=parser.parse_args()
    rows=[trial(q,rep,args.collect_only) for rep in [1,2] for q in QUESTIONS]
    baseline=json.loads((ROOT/'evidence/s8-agent-comparison.json').read_text())['conditions']['schema_and_rules']
    summary={'trials':len(rows),'numeric_correct':sum(r['numeric_correct'] for r in rows),'metadata_mcp_calls':0,
       'query_mcp_calls':sum(len(r['calls']) for r in rows),'file_tool_calls':sum(len(r['file_commands']) for r in rows),
       'model_elapsed_seconds':round(sum(r['elapsed_seconds'] or 0 for r in rows),3),
       'usage':{k:sum(r['usage'].get(k,0) for r in rows) for k in ['input_tokens','cached_input_tokens','output_tokens']}}
    save(ROOT/'evidence/s9-wiki-comparison.json',{'verified_at_utc':datetime.now(timezone.utc).isoformat(),'file_wiki':summary,'s8_full_context_historical_baseline':baseline,
      'scope':'incremental file reads, no metadata MCP, native YAML remains canonical; prospective small-sample comparison, shell reads enabled only in this branch'})
    print(json.dumps(summary),flush=True)


if __name__=='__main__':main()

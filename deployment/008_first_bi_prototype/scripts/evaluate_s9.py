"""Prospective topic-context trials; compare to the retained S8 full-schema runs."""
import argparse
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
from configure_w2 import TOOLS
from evaluate_s8 import QUESTIONS
from prepare_s7 import ROOT,save
from verify_s7_codex import decode

TOOLS=TOOLS+['list_topics']


def trial(question,rep,collect_only=False):
    prefix=f's9-{question}-{rep}';proof=ROOT/f'evidence/{prefix}.json'
    if proof.exists() and not collect_only:return json.loads(proof.read_text())
    elapsed=json.loads(proof.read_text()).get('elapsed_seconds') if proof.exists() else None
    if not collect_only:
      with tempfile.TemporaryDirectory(prefix='bi008-s9-analysis-') as tmp:
        Path(tmp,'AGENTS.md').write_text((ROOT/'s9/ANALYSIS.md').read_text())
        args=[str(ROOT/'.local/codex-bin'),'exec','--ephemeral','--skip-git-repo-check','--json',
              '--disable','shell_tool','--disable','unified_exec','--disable','multi_agent',
              '-c','approval_policy="never"','-c','sandbox_mode="read-only"',
              '-c','mcp_servers.bi008_s9.command='+json.dumps(str(ROOT/'scripts/s9_wren_mcp.sh')),
              '-c','mcp_servers.bi008_s9.enabled_tools='+json.dumps(TOOLS),'-c','mcp_servers.bi008_s9.default_tools_approval_mode="approve"','-c','mcp_servers.bi008_s9.required=true',
              '-c','mcp_servers.bi008_s9.tool_timeout_sec=90',
              '--output-schema',str(ROOT/'s8/answer.schema.json'),'-o',str(ROOT/f'.local/{prefix}-answer.json'),QUESTIONS[question]]
        start=time.perf_counter()
        with (ROOT/f'.local/{prefix}-events.jsonl').open('w') as out,(ROOT/f'.local/{prefix}-stderr.log').open('w') as err:
            subprocess.run(args,cwd=tmp,stdout=out,stderr=err,check=True,timeout=420)
        elapsed=round(time.perf_counter()-start,3)
    events=[json.loads(l) for l in (ROOT/f'.local/{prefix}-events.jsonl').read_text().splitlines()]
    assert any(e['type']=='turn.completed' for e in events)
    items=[e['item'] for e in events if e['type']=='item.completed'];assert all(i['type'] in ['agent_message','reasoning','mcp_tool_call'] for i in items)
    calls=[i for i in items if i['type']=='mcp_tool_call'];assert all(c['server']=='bi008_s9' and c['tool'] in TOOLS and not c.get('error') for c in calls)
    contexts=[decode(c) for c in calls if c['tool']=='get_context'];assert contexts and all(c['status']=='ready' and len(c['model_names'])<=3 for c in contexts)
    assert all(c['strategy']=='reviewed_topic_closure' and c['schema_utf8_bytes']/83225<=0.1 for c in contexts)
    answer=json.loads((ROOT/f'.local/{prefix}-answer.json').read_text());gold=json.loads((ROOT/'s8/expected-relations.json').read_text())
    queries=[c for c in calls if c['tool']=='run_sql']
    for c in queries:
        for tool in ['dry_plan','dry_run']:assert any(p['tool']==tool and p['arguments']['sql']==c['arguments']['sql'] for p in calls[:calls.index(c)])
    values=[];rows=[row for c in queries for row in decode(c)['rows']]
    for row in rows:
        for v in row.values():
            try:values.append(Decimal(str(v)))
            except InvalidOperation:pass
    assert answer['status']=='answered' and Decimal(answer['total'])==Decimal(gold['total'])
    assert len(answer['groups'])==len(gold[question]) and {r['label']:Decimal(r['amount']) for r in answer['groups']}=={k:Decimal(v) for k,v in gold[question].items()}
    for label,amount in gold[question].items():
        assert any(label in row.values() and any(Decimal(str(v))==Decimal(amount) for v in row.values() if isinstance(v,(int,float)) or isinstance(v,str) and re.fullmatch(r'-?\d+(\.\d+)?',v)) for row in rows)
    assert Decimal(gold['total']) in values or question!='overlapping_tags' and sum(Decimal(v) for v in gold[question].values())==Decimal(gold['total'])
    result={'verified_at_utc':datetime.now(timezone.utc).isoformat(),'question':question,'rep':rep,'elapsed_seconds':elapsed,
        'calls':calls,'answer':answer,'numeric_correct':True,'usage':next(e['usage'] for e in events if e['type']=='turn.completed')}
    save(proof,result);print(question,rep,'passed;',len(calls),'calls;',elapsed,'seconds',flush=True);return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--collect-only',action='store_true');args=parser.parse_args()
    results=[trial(q,rep,args.collect_only) for rep in [1,2] for q in QUESTIONS]
    baseline=json.loads((ROOT/'evidence/s8-agent-comparison.json').read_text())['conditions']['schema_and_rules']
    summary={'trials':len(results),'numeric_correct':sum(r['numeric_correct'] for r in results),
       'model_elapsed_seconds':round(sum(r['elapsed_seconds'] or 0 for r in results),3),'tool_calls':sum(len(r['calls']) for r in results),
       'usage':{k:sum(r['usage'].get(k,0) for r in results) for k in ['input_tokens','cached_input_tokens','output_tokens']}}
    save(ROOT/'evidence/s9-agent-comparison.json',{'verified_at_utc':datetime.now(timezone.utc).isoformat(),'s9_topic_context':summary,'s8_full_context_historical_baseline':baseline,
       'trial_files':[f"s9-{r['question']}-{r['rep']}.json" for r in results],
       'limits':['prospective comparison to historical S8, not randomized paired timing experiment','same existing model configuration; tools add list_topics and context behavior differs','only 3 questions and 2 repetitions','curated topic matching, not learned or vector retrieval']})
    print(json.dumps(summary),flush=True)


if __name__=='__main__':main()

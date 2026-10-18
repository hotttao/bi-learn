"""Real stdio topic-context regression and dependency/version refusals."""
import asyncio
from datetime import datetime,timezone,timedelta
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import yaml
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

from prepare_s7 import ROOT,save
from verify_w2_mcp import payload
from inspect_s8_catalog import SQL
from topic_wren_mcp import Topics,PROJECT,REGISTRY

QUESTIONS={'current_industry':'2026年净销售额按客户当前行业统计','historical_industry':'2026年净收入按下单时客户行业统计','overlapping_tags':'2026年净销售额按客户标签统计'}


async def main():
    report={'verified_at_utc':datetime.now(timezone.utc).isoformat(),'contexts':{},'queries':{},'refusals':{},'calls':[]}
    baseline=json.loads((ROOT/'evidence/s8-catalog.json').read_text());full=next(x['schema_utf8_bytes'] for x in baseline['contexts'] if x['models']==500)
    gold=json.loads((ROOT/'s8/expected-relations.json').read_text())
    params=StdioServerParameters(command=str(ROOT/'scripts/s9_wren_mcp.sh'))
    with (ROOT/'.local/s9-mcp.log').open('w') as log:
     async with stdio_client(params,errlog=log) as (read,write):
      async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=120)) as session:
       await session.initialize()
       async def call(name,args=None,refuse=False):
        raw=await session.call_tool(name,args or {});result=payload(raw);report['calls'].append({'tool':name,'arguments':args or {},'result':result})
        if refuse:assert raw.isError or isinstance(result,dict) and result.get('error'),result
        else:assert not raw.isError and not (isinstance(result,dict) and result.get('error')),result
        return result
       report['refusals']['before_selection']=await call('run_sql',{'sql':'SELECT * FROM sales_orders'},True)
       assert (await call('list_models'))['models']==[]
       assert len((await call('list_topics'))['topics'])==4
       for topic,question in QUESTIONS.items():
        ctx=await call('get_context',{'question':question});assert ctx['status']=='ready' and len(ctx['model_names'])==3
        assert ctx['schema_utf8_bytes']/full<=0.1
        models=await call('list_models');assert {x['name'] for x in models['models']}==set(ctx['model_names'])
        await call('get_instructions')
        for tool in ['dry_plan','dry_run']:await call(tool,{'sql':SQL[topic]})
        result=await call('run_sql',{'sql':SQL[topic]});assert {r['label']:format(Decimal(r['amount']),'.2f') for r in result['rows']}==gold[topic]
        ctx['full_schema_utf8_bytes']=full;ctx['schema_ratio']=ctx['schema_utf8_bytes']/full;report['contexts'][topic]=ctx;report['queries'][topic]=result
       await call('get_context',{'question':'2026年净收入合计'})
       report['refusals']['outside_model']=await call('run_sql',{'sql':'SELECT * FROM finance_revenue_entries'},True)
       report['refusals']['outside_in_cte']=await call('dry_plan',{'sql':'WITH x AS (SELECT * FROM crm_industry_history) SELECT * FROM x'},True)
       # A CTE name may shadow an irrelevant model name without referring to that model.
       shadow='WITH marketing_customer_orders_000 AS (SELECT order_id FROM sales_orders) SELECT COUNT(*) AS n FROM marketing_customer_orders_000'
       await call('dry_plan',{'sql':shadow});await call('dry_run',{'sql':shadow});assert (await call('run_sql',{'sql':shadow}))['rows']==[{'n':6}]
       combined=await call('get_context',{'question':'比较2026年净收入按当前行业和下单时行业的分组'})
       assert combined['status']=='ready' and len(combined['model_names'])==4;report['combined_context']=combined
       ambiguous=await call('get_context',{'question':'净收入按行业统计'})
       assert ambiguous['status']=='needs_clarification' and ambiguous['model_names']==[]
       report['refusals']['ambiguous_clears_context']=await call('run_sql',{'sql':'SELECT COUNT(*) FROM sales_orders'},True)
       financial=await call('get_context',{'question':'财务确认收入是多少'})
       assert financial['status']=='unavailable' and financial['schema']=='';report['unsupported_metric']=financial
    # Deliberate broken drafts; published registry/project are never modified.
    original=yaml.safe_load(REGISTRY.read_text())
    with tempfile.TemporaryDirectory(prefix='bi008-s9-refusals-') as tmp:
      for case in ['missing_dependency','cycle','missing_model','stale_registry']:
       p=Path(tmp)/'topics.yaml';d=json.loads(json.dumps(original))
       if case=='missing_dependency':d['topics']['current_industry']['depends_on']=['missing']
       if case=='cycle':d['topics']['net_revenue']['depends_on']=['current_industry']
       if case=='missing_model':d['topics']['current_industry']['models']=['nonexistent_model']
       p.write_text(yaml.safe_dump(d,allow_unicode=True))
       try:
        topics=Topics(PROJECT,p)
        if case=='stale_registry':
         topics.context('净收入合计');p.write_text(p.read_text()+'\n# changed after startup\n');topics.check_sql('SELECT * FROM sales_orders')
       except ValueError as error:report['refusals'][case]=str(error)
       else:raise AssertionError('Invalid topic draft accepted: '+case)
    report['checks_passed']=True;save(ROOT/'evidence/s9-topics.json',report);print('S9 topic closure, exact amounts, ambiguity and version/scope refusals passed')


if __name__=='__main__':asyncio.run(main())

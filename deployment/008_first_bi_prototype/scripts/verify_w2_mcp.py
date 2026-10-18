"""Real stdio MCP regression: native tools, exact results and adapter refusals."""
import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


def check(ok, message):
    if not ok: raise AssertionError(message)


def payload(result):
    text = '\n'.join(c.text for c in result.content if c.type == 'text')
    if result.isError: return {'error':text}
    if result.structuredContent is not None:
        value = result.structuredContent
        return value['result'] if set(value)=={'result'} else value
    try: return json.loads(text)
    except json.JSONDecodeError: return text


async def verify_main():
    report = {'verified_at_utc':datetime.now(timezone.utc).isoformat(),'transport':'stdio','calls':{},'rejections':{}}
    audit = ROOT/'.local/w2-mcp-test-audit.ndjson'
    audit.unlink(missing_ok=True)
    with (ROOT/'.local/w2-mcp-test-server.log').open('w') as log:
        params = StdioServerParameters(command=str(ROOT/'scripts/wren_mcp.sh'),env={'BI008_MCP_AUDIT_PATH':str(audit)})
        async with stdio_client(params,errlog=log) as (read,write):
            async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=60)) as session:
                initialized = await session.initialize()
                report['server_info'] = initialized.serverInfo.model_dump(mode='json',exclude_none=True)
                tools = (await session.list_tools()).tools
                names = {t.name for t in tools}
                check(len(names)==18 and 'store_query' not in names, 'Unexpected native tool surface')
                (ROOT/'evidence/w2-mcp-tools.json').write_text(json.dumps([t.model_dump(mode='json',exclude_none=True) for t in tools],ensure_ascii=False,indent=2)+'\n')
                async def call(name,args=None):
                    result = await session.call_tool(name,args or {})
                    check(not result.isError,f'{name}: {payload(result)}')
                    value = payload(result)
                    report['calls'][name] = value
                    return value
                contract = await call('get_runtime_contract')
                check(contract['source_matches_startup'] and contract['strict_mode'] and not contract['allow_write'], 'Wrong runtime contract')
                models = await call('list_models')
                check({m['name'] for m in models['models']}=={'bi_orders','bi_customers','bi_refund_totals'},'Wrong model catalog')
                await call('describe_model',{'name':'bi_orders'})
                await call('list_cubes')
                await call('describe_cube',{'name':'net_revenue'})
                await call('get_data_source')
                rules = await call('get_instructions')
                check('当前行业' in rules['instructions'],'Business rules missing')
                context = await call('get_context',{'question':'净收入与行业'})
                check(context['strategy']=='full' and 'bi_orders' in context['schema'],'Expected no-embedding context fallback')
                await call('describe_schema')
                recall = await call('recall_queries',{'question':'行业'})
                check(recall['matches'],'Expected known-query recall')
                await call('list_stored_queries')
                await call('list_knowledge')
                await call('get_mdl')
                plan = await call('dry_plan',{'sql':'SELECT SUM(net_amount) AS net_revenue FROM bi_orders'})
                check('mysql.sales.refunds' in plan and 'GROUP BY' in plan,'Refund aggregation missing from plan')
                await call('dry_run',{'sql':'SELECT SUM(net_amount) AS net_revenue FROM bi_orders'})
                total = await call('run_sql',{'sql':'SELECT SUM(net_amount) AS net_revenue FROM bi_orders'})
                check(total['rows']==[{'net_revenue':'245.0000000000'}],'MCP total or money codec differs')
                cube = await call('query_cube',{'cube':'net_revenue','measures':['total'],'dimensions':['industry']})
                check({r['industry']:Decimal(r['total']) for r in cube['rows']}=={'软件':120,'零售':125},'Industry cube differs')
                day = await call('query_cube',{'cube':'net_revenue','measures':['total'], 'time_dimension':'order_date:day'})
                check(sorted(Decimal(r['total']) for r in day['rows'])==[120,125],'Daily cube differs')
                # Preserve named results when the same tool is used more than once.
                report['baseline']={'total':total,'industry':cube,'daily':day}

                date_sql = await call('run_sql',{'sql':"SELECT order_date,SUM(net_amount) AS net_revenue FROM bi_orders WHERE order_date >= DATE '2026-10-01' AND order_date < DATE '2026-11-01' GROUP BY order_date ORDER BY order_date"})
                check([Decimal(r['net_revenue']) for r in date_sql['rows']]==[120,125],'Typed DATE query failed')
                report['typed_date_sql']=date_sql
                range_result=await session.call_tool('query_cube',{'cube':'net_revenue','measures':['total'],'time_dimension':'order_date:day:2026-10-01,2026-10-03'})
                check(range_result.isError and 'BI008_CUBE_RANGE' in str(payload(range_result)),'Unsupported cube range not explicitly rejected')
                report['rejections']['cube date range capability']=payload(range_result)

                exact = await call('run_sql',{'sql':"SELECT CAST('9007199254740993.01' AS DECIMAL(20,2)) AS amount FROM bi_orders LIMIT 1"})
                check(exact['rows']==[{'amount':'9007199254740993.01'}],'Decimal was rounded across MCP')
                report['exact_decimal']=exact
                limited = await call('run_sql',{'sql':'SELECT order_id FROM bi_orders ORDER BY order_id','limit':1})
                check(limited['row_count']==1 and limited['truncated'] and limited['rows']==[{'order_id':1}],'Limit/truncation differs')
                report['truncation']=limited
                cte = await call('run_sql',{'sql':'WITH x AS (SELECT order_id FROM bi_orders) SELECT COUNT(*) AS n FROM x'})
                check(cte['rows']==[{'n':4}],'Visible CTE was rejected')
                for tool in ['run_sql','dry_run','dry_plan']:
                    for sql in ['SELECT * FROM mysql.sales.bi_orders','SELECT * FROM "mysql"."sales"."bi_orders"',
                                'SELECT * FROM mysql.sales.orders','SELECT * FROM crm.public.customers','SELECT * FROM sales.bi_orders',
                                'WITH bi_orders AS (SELECT * FROM mysql.sales.refunds) SELECT * FROM bi_orders',
                                'SELECT order_id FROM bi_orders UNION ALL SELECT order_id FROM mysql.sales.orders',
                                'SELECT 1; SELECT * FROM mysql.sales.orders','UPDATE bi_orders SET paid_amount=0 WHERE 1=0',
                                "SELECT * FROM TABLE(system.query(query => 'SELECT * FROM mysql.sales.orders'))"]:
                        before = sum(json.loads(line)['event']=='query_attempt' for line in audit.read_text().splitlines()) if audit.exists() else 0
                        result = await session.call_tool(tool,{'sql':sql})
                        check(result.isError and 'BI008_QUERY_SCOPE' in str(payload(result)),f'Adapter scope was bypassed: {tool} {sql}')
                        after = sum(json.loads(line)['event']=='query_attempt' for line in audit.read_text().splitlines())
                        check(before==after,'Rejected input reached the native query method')
                        report['rejections'][f'{tool}: {sql}']=payload(result)
                for name,args in [('run_sql',{'sql':'SELECT * FROM unknown_model'}),('query_cube',{'cube':'net_revenue','measures':['unknown_measure']})]:
                    result=await session.call_tool(name,args)
                    check(result.isError, 'Unknown semantic object accepted')
                    report['rejections'][name+': unknown semantic object']=payload(result)
                resources = await session.list_resources()
                report['resource_uris']=[str(r.uri) for r in resources.resources]
                mdl_resource=await session.read_resource('wren://mdl')
                check(json.loads(mdl_resource.contents[0].text)==report['calls']['get_mdl'],'Resource and tool manifest mismatch')
                rule_resource=await session.read_resource('wren://knowledge/rules/general.md')
                check('当前行业' in rule_resource.contents[0].text,'Business resource missing')
                try:
                    await session.read_resource('wren://knowledge/%2e%2e/runtime/config.json')
                except Exception as error: report['resource_escape']='rejected: '+type(error).__name__
                else:raise AssertionError('Knowledge resource escape accepted')
                prompt=await session.get_prompt('wren_workflow',{'question':'按行业看净收入'})
                check('store_query' not in str(prompt),'Read-only prompt advertises storage writes')
                report['prompt']='wren_workflow read back; no store step'
    report['audit_counts']=dict(Counter(json.loads(line)['event'] for line in audit.read_text().splitlines()))
    return report


async def verify_changed_sources():
    # Isolate the lifecycle check in a disposable copy; never edit the live models.
    with tempfile.TemporaryDirectory(prefix='bi008-w2-regression-') as temporary:
        fixture=Path(temporary)/'wren'
        shutil.copytree(ROOT/'wren',fixture)
        code=f"from pathlib import Path; import sys; sys.path.insert(0,{str(ROOT/'scripts')!r}); from guarded_wren_mcp import make_server; make_server(Path({str(fixture)!r})).run(transport='stdio')"
        env={'WREN_HOME':str(fixture/'runtime'),'WREN_PROJECT_HOME':str(fixture),'WREN_MEMORY_BACKEND':'grep',
             'BI008_MCP_AUDIT_PATH':str(ROOT/'.local/w2-revision-audit.ndjson')}
        with (ROOT/'.local/w2-revision-server.log').open('w') as log:
            async with stdio_client(StdioServerParameters(command=str(ROOT/'.local/wren-venv/bin/python'),args=['-c',code],env=env),errlog=log) as (read,write):
                async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=30)) as session:
                    await session.initialize()
                    before=payload(await session.call_tool('get_instructions',{}))
                    p=fixture/'knowledge/rules/general.md';p.write_text(p.read_text()+'\nW2 regression-only marker\n')
                    contract=payload(await session.call_tool('get_runtime_contract',{}))
                    check(not contract['source_matches_startup'],'Source drift was not reported')
                    after=payload(await session.call_tool('get_instructions',{}))
                    check(before==after and 'regression-only marker' not in str(after),'Metadata did not stay on startup snapshot')
                    refused=await session.call_tool('run_sql',{'sql':'SELECT COUNT(*) FROM bi_orders'})
                    check(refused.isError and 'BI008_MODEL_REVISION' in str(payload(refused)),'Stale-source query was allowed')
                    return {'source_drift_detected':True,'metadata_snapshot_preserved':True,'query_refused_before_native_execution':True}


async def main():
    report=await verify_main()
    report['revision_fixture']=await verify_changed_sources()
    report['not_verified']=['department authentication/isolation','production security audit','large-data costs',
        'HTTP transport','automatic Superset asset synchronization','semantic change publication (W3)']
    (ROOT/'evidence/w2-mcp-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('Real stdio MCP passed: 18 tools, 245 / 120+125, exact Decimal, 30 scope refusals, resources and source-revision guard')


if __name__=='__main__':asyncio.run(main())

"""Prepare an isolated S7 dashboard and a verified Wren↔BI fixture binding.

Native MCP creates the seed assets. REST edits only S7-owned fixture metadata to
simulate existing human choices; these are explicitly test setup, not Agent work.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from discover_s6 import ROOT, URL, payload
from s3_assets import API, BASE
from s6_assets import layout_ids
from verify_s4 import trino
from verify_w2_mcp import payload as wren_payload

SEED_NAME='008 S7 净收入（模拟人工复核）'
DASHBOARD_NAME='008 S7 Codex 语义与资产实验'


def digest(data):return hashlib.sha256(data).hexdigest()


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def canonical(rows):
    return sorted([{'order_id':r['order_id'],'dept_id':r['dept_id'],'industry':r['industry'],
                    'order_date':str(r['order_date']),'net_amount':format(Decimal(str(r['net_amount'])),'.2f')} for r in rows],key=lambda r:r['order_id'])


def validate_binding(binding,api=None):
    api=api or API()
    if digest((ROOT/'wren/target/mdl.json').read_bytes())!=binding['wren_mdl_sha256']:
        raise ValueError('BI008_SEMANTIC_BINDING: Wren model changed; re-review mapping before BI writes')
    from guarded_wren_mcp import revision
    if revision(ROOT/'wren')!=binding['wren_source_revision']:
        raise ValueError('BI008_SEMANTIC_BINDING: Wren knowledge/config revision changed; re-review binding')
    dataset=api.call('GET',f"/api/v1/dataset/{binding['dataset_id']}")['result']
    if dataset['uuid']!=binding['dataset_uuid'] or dataset['database']['id']!=binding['database_id']:
        raise ValueError('BI008_SEMANTIC_BINDING: dataset identity or source connection changed')
    if digest(dataset['sql'].strip().rstrip(';').encode())!=binding['dataset_sql_sha256']:
        raise ValueError('BI008_SEMANTIC_BINDING: BI dataset SQL changed; stop instead of silently mixing models')
    return True


async def main():
    api=API();fixture_path=ROOT/'evidence/s7-fixture.json'
    if fixture_path.exists():
        fixture=json.loads(fixture_path.read_text());validate_binding(json.loads((ROOT/'evidence/s7-binding.json').read_text()),api)
        assert api.call('GET',f"/api/v1/dashboard/{fixture['dashboard_id']}")['result']['dashboard_title']==DASHBOARD_NAME
        print('S7 fixture already exists; preserve it:',fixture['dashboard_url']);return
    s6=json.loads((ROOT/'evidence/s6-assets.json').read_text());dataset_id=s6['dataset']['id']
    dataset=api.call('GET',f'/api/v1/dataset/{dataset_id}')['result']
    sql=dataset['sql'].strip().rstrip(';')
    assert dataset_id==3 and sql==s6['dataset']['sql']
    with (ROOT/'.local/s7-wren-prepare.log').open('w') as log:
        async with stdio_client(StdioServerParameters(command=str(ROOT/'scripts/wren_mcp.sh')),errlog=log) as (read,write):
            async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=90)) as session:
                await session.initialize();contract=wren_payload(await session.call_tool('get_runtime_contract',{}))
                assert contract['metric']=='net_revenue@1' and contract['source_matches_startup']
                query="SELECT order_id,dept_id,industry,order_date,net_amount FROM bi_orders WHERE order_date >= DATE '2026-10-01' AND order_date < DATE '2026-11-01' ORDER BY order_id"
                for tool in ['dry_plan','dry_run']:assert not (await session.call_tool(tool,{'sql':query})).isError
                wr=wren_payload(await session.call_tool('run_sql',{'sql':query}))
                assert 'error' not in wr and not wr['truncated']
    raw=trino('SELECT order_id,dept_id,industry,order_date,net_amount FROM ('+sql+') s ORDER BY order_id')
    bi=[dict(zip(raw['columns'],row)) for row in raw['rows']]
    rows=canonical(wr['rows']);assert rows==canonical(bi) and len(rows)==4
    binding={'verified_at_utc':datetime.now(timezone.utc).isoformat(),'scope':'synthetic fixture; verified mapping, not enterprise semantic publication',
             'metric':'net_revenue@1','wren_mdl_sha256':contract['mdl_sha256'],'wren_source_revision':contract['source_revision'],
             'dataset_id':dataset_id,'dataset_uuid':dataset['uuid'],'database_id':2,'dataset_sql_sha256':digest(sql.encode()),
             'window':{'start_inclusive':'2026-10-01','end_exclusive':'2026-11-01'},'unit':'CNY_yuan',
             'metric_expression':'SUM(net_amount)','industry_semantics':'current','refund_time':'original_order_date',
             'comparison':{'wren_sql':query,'bi_query_id':raw['query_id'],'matching_order_rows':rows},
             'automatic_sync':False,'query_equivalence_scope':'four known fixture rows only; source hashes detect changes, not general SQL equivalence proof'}
    save(ROOT/'evidence/s7-binding.json',binding);validate_binding(binding,api)
    calls=[]
    async with httpx.AsyncClient(trust_env=False,timeout=120) as client:
        async with streamable_http_client(URL,http_client=client) as (read,write,_):
            async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=120)) as session:
                await session.initialize()
                async def call(name,request):
                    result=payload(await session.call_tool(name,{'request':request}));assert not result.get('error') and result.get('success') is not False,result
                    calls.append({'tool':name,'request':request,'result':result});return result
                cid=api.find('chart','slice_name',SEED_NAME)
                if cid is None:
                    result=await call('generate_chart',{'dataset_id':dataset_id,'chart_name':SEED_NAME,'save_chart':True,'generate_preview':False,
                        'config':{'chart_type':'big_number','metric':{'name':'net_amount','aggregate':'SUM','label':'net_revenue'},'y_axis_format':',.1f','subheader':'模拟人工备注：运营已核对，请保留'}})
                    cid=result['chart']['id']
                did=api.find('dashboard','dashboard_title',DASHBOARD_NAME)
                if did is None:
                    await call('generate_dashboard',{'chart_ids':[cid],'dashboard_title':DASHBOARD_NAME,
                        'description':'S7 dedicated synthetic-data scenario; preserve simulated human annotations.','published':True})
                    did=api.find('dashboard','dashboard_title',DASHBOARD_NAME)
    board=api.call('GET',f'/api/v1/dashboard/{did}')['result'];assert layout_ids(board)==[cid]
    position=json.loads(board['position_json'])
    grid=position['GRID_ID'];note_row='ROW-S7-MANUAL';note='MARKDOWN-S7-MANUAL'
    assert note not in position
    position[note_row]={'id':note_row,'type':'ROW','parents':['ROOT_ID','GRID_ID'],'children':[note],'meta':{'background':'BACKGROUND_TRANSPARENT'}}
    position[note]={'id':note,'type':'MARKDOWN','parents':['ROOT_ID','GRID_ID',note_row],'children':[],
                    'meta':{'width':12,'height':14,'code':'### 模拟人工备注\n本看板用于固定十月净收入核验；请保留此说明。'}}
    grid['children'].append(note_row)
    api.call('PUT',f'/api/v1/dashboard/{did}',{'position_json':json.dumps(position,ensure_ascii=False)})
    chart=api.call('GET',f'/api/v1/chart/{cid}')['result']
    fixture={'verified_at_utc':datetime.now(timezone.utc).isoformat(),'dashboard_id':did,'dashboard_url':BASE+f'/superset/dashboard/{did}/',
             'seed_chart_id':cid,'seed_chart_name':SEED_NAME,'seed_chart_params':json.loads(chart['params']),
             'manual_note_id':note,'preserved_nodes':{k:v for k,v in position.items() if k not in ['ROOT_ID','GRID_ID']},
             'setup_calls':calls,'human_edits':'simulated by setup, not claimed as an actual user session'}
    save(fixture_path,fixture)
    # Explicitly prove a stale proposed binding is refused without touching published files.
    stale=dict(binding,wren_mdl_sha256='0'*64)
    try:validate_binding(stale,api)
    except ValueError as error:save(ROOT/'evidence/s7-binding-refusal.json',{'rejected':str(error),'fixture_only':True,'bi_write_attempted':False})
    else:raise AssertionError('Stale binding accepted')
    print('S7 binding validated on four keyed orders; dedicated dashboard:',fixture['dashboard_url'])


if __name__=='__main__':asyncio.run(main())

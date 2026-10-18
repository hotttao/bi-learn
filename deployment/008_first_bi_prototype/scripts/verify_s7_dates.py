"""Date-range integration: real native MCP, Wren and changing live source rows.

Run with the prototype venv; --fixture is an operator-only sudo entrypoint.
No source change is left behind: original order values are restored in finally.
"""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from discover_s6 import ROOT, URL, payload
from prepare_s7 import save, digest, validate_binding
from s3_assets import API, BASE, query_context
from source_db import query
from verify_w2_mcp import payload as wren_payload

BINDING=ROOT/'evidence/s7-date-binding.json'
ASSETS=ROOT/'evidence/s7-date-assets.json'


def filters(start,end):
    return [{'column':'order_date','op':'>=','value':start},{'column':'order_date','op':'<','value':end}]


def config(start,end,kind='kpi'):
    common={'filters':filters(start,end)}
    if kind=='kpi':return dict(common,chart_type='big_number',metric={'name':'net_amount','aggregate':'SUM','label':'net_revenue'},y_axis_format=',.2f')
    return dict(common,chart_type='xy',kind='line' if kind=='trend' else 'bar',
                x={'name':'order_date' if kind=='trend' else 'industry'},
                y=[{'name':'net_amount','aggregate':'SUM','label':'net_revenue'}],
                **({'time_grain':'P1D'} if kind=='trend' else {}),y_axis={'format':',.2f'},row_limit=1000)


def fixture(mode):
    if mode=='apply':
        assert query('mysql',"SELECT CONCAT(order_date,':',paid_amount) FROM orders WHERE order_id=2")=='2026-10-02:80.00','Unexpected source state; do not overwrite'
        query('mysql',"UPDATE orders SET order_date='2026-09-30',paid_amount=90.00 WHERE order_id=2",'admin')
    else:
        assert query('mysql',"SELECT CONCAT(order_date,':',paid_amount) FROM orders WHERE order_id=2")=='2026-09-30:90.00','Fixture changed externally; inspect before restoring'
        query('mysql',"UPDATE orders SET order_date='2026-10-02',paid_amount=80.00 WHERE order_id=2",'admin')
    print('S7 date fixture',mode)


def operator_fixture(mode):
    # sudo authentication uses existing session credentials; no password in source.
    if os.geteuid()==0:fixture(mode)
    else:subprocess.run(['sudo','-n',sys.executable,str(Path(__file__).resolve()),'--fixture',mode],check=True)


async def main():
    api=API();old=json.loads((ROOT/'evidence/s7-binding.json').read_text());validate_binding(old,api)
    ds=api.call('GET','/api/v1/dataset/2')['result'];sql=ds['sql'].strip().rstrip(';')
    assert sql==(ROOT/'sql/trino/orders_enriched_v1.sql').read_text().strip().rstrip(';')
    binding=dict(old,dataset_id=2,dataset_uuid=ds['uuid'],dataset_sql_sha256=digest(sql.encode()),
                 window={'mode':'user_selected_half_open_business_dates','fixed_window':False},
                 temporal_column='order_date',date_filter_transport='validated >=/< YYYY-MM-DD converted to native TEMPORAL_RANGE')
    binding.pop('comparison',None);save(BINDING,binding);validate_binding(binding,api)
    report={'verified_at_utc':datetime.now(timezone.utc).isoformat(),'dataset_id':2,'native_calls':[],
            'wren_calls':[],'baseline_ranges':[],'changed_source_ranges':[],'source_restored':False}
    async with httpx.AsyncClient(trust_env=False,timeout=120) as client:
      async with streamable_http_client(URL,http_client=client) as (read,write,_):
       async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=120)) as bi:
        await bi.initialize()
        async def call(name,request):
            result=payload(await bi.call_tool(name,{'request':request}));report['native_calls'].append({'tool':name,'request':request,'result':result})
            assert not result.get('error') and result.get('success') is not False,result
            return result
        with (ROOT/'.local/s7-date-wren.log').open('w') as log:
         async with stdio_client(StdioServerParameters(command=str(ROOT/'scripts/wren_mcp.sh')),errlog=log) as (rd,wr):
          async with ClientSession(rd,wr,read_timeout_seconds=timedelta(seconds=120)) as wren:
           await wren.initialize()
           async def check(start,end,expected,phase):
            query_sql=f"SELECT SUM(net_amount) AS net_revenue,COUNT(*) AS order_count FROM bi_orders WHERE order_date >= DATE '{start}' AND order_date < DATE '{end}'"
            for tool in ['dry_plan','dry_run','run_sql']:
                result=wren_payload(await wren.call_tool(tool,{'sql':query_sql}));report['wren_calls'].append({'tool':tool,'sql':query_sql,'result':result});assert not (isinstance(result,dict) and result.get('error')),result
            rows=result['rows'];amount=rows[0]['net_revenue'];assert (None if amount is None else Decimal(amount))==expected,(start,end,rows,expected)
            preview=await call('generate_chart',{'dataset_id':2,'config':config(start,end),'save_chart':False,'generate_preview':True,'preview_formats':['url'],'force_refresh':True})
            data=await call('get_chart_data',{'form_data_key':preview['form_data_key'],'force_refresh':True})
            actual=data['data'][0]['net_revenue'];assert (None if actual is None else Decimal(str(actual)))==expected,(start,end,data)
            direct=api.call('POST','/api/v1/chart/data',query_context(2,time_range=start+' : '+end))['result'][0]
            assert f"DATE '{start}'" in direct['query'] and f"DATE '{end}'" in direct['query'],direct['query']
            report[phase].append({'start_inclusive':start,'end_exclusive':end,'expected':str(expected) if expected is not None else None,
               'wren_rows':rows,'bi_data':data['data'],'typed_sql':direct['query'],'explore_url':preview['explore_url']})
           for start,end,expected in [('2026-10-01','2026-10-02',120),('2026-10-02','2026-10-03',125),
               ('2026-09-01','2026-10-01',None),('2026-09-01','2026-11-01',245),
               ('2026-01-01','2027-01-01',245),('2026-11-01','2026-12-01',None)]:
            await check(start,end,expected,'baseline_ranges')
           # Verify fail-closed behavior for malformed or inverted ranges, no saved asset.
           report['invalid_ranges']=[]
           for start,end in [('2026-10-02','2026-10-01'),('2026-02-30','2026-03-01')]:
            raw=await bi.call_tool('generate_chart',{'request':{'dataset_id':2,'config':config(start,end),'save_chart':False}})
            result=payload(raw);assert raw.isError or result.get('success') is False or result.get('error'),result
            report['invalid_ranges'].append({'start':start,'end':end,'result':result})
           applied=False
           try:
            operator_fixture('apply');applied=True
            for start,end,expected in [('2026-09-01','2026-10-01',85),('2026-10-01','2026-11-01',170),('2026-09-01','2026-11-01',255)]:
             await check(start,end,expected,'changed_source_ranges')
           finally:
            if applied:operator_fixture('restore')
           await check('2026-10-01','2026-11-01',245,'baseline_ranges');report['source_restored']=True
        charts={}
        for kind in ['kpi','trend','industry']:
            name='008 S7 自选日期 '+kind;cid=api.find('chart','slice_name',name)
            if cid is None:
                result=await call('generate_chart',{'dataset_id':2,'config':config('2026-10-01','2026-11-01',kind),'chart_name':name,'save_chart':True,'generate_preview':False,'force_refresh':True});cid=result['chart']['id']
            charts[kind]=cid
        # A single-day line has one point; make it visible rather than an empty line.
        trend=api.call('GET',f"/api/v1/chart/{charts['trend']}")['result']
        trend_params=json.loads(trend['params']);trend_params.update(markerEnabled=True,markerSize=7)
        api.call('PUT',f"/api/v1/chart/{charts['trend']}",{'params':json.dumps(trend_params)})
        title='008 S7 自选日期与实时数据';did=api.find('dashboard','dashboard_title',title)
        if did is None:
            await call('generate_dashboard',{'dashboard_title':title,'chart_ids':list(charts.values()),'published':True})
            did=api.find('dashboard','dashboard_title',title)
        board=api.call('GET',f'/api/v1/dashboard/{did}')['result'];metadata=json.loads(board.get('json_metadata') or '{}')
        metadata['native_filter_configuration']=[{'id':'NATIVE_FILTER-S7-DATES','type':'NATIVE_FILTER','name':'业务日期（起含止不含）','filterType':'filter_time','targets':[{}],
            'defaultDataMask':{'extraFormData':{'time_range':'2026-10-01 : 2026-11-01'},'filterState':{'value':'2026-10-01 : 2026-11-01'}},
            'cascadeParentIds':[],'scope':{'rootPath':['ROOT_ID'],'excluded':[]},'controlValues':{}}]
        api.call('PUT',f'/api/v1/dashboard/{did}',{'json_metadata':json.dumps(metadata,ensure_ascii=False)})
        save(ASSETS,{'dashboard_id':did,'dashboard_url':BASE+f'/superset/dashboard/{did}/','dataset_id':2,'chart_ids':charts,'native_date_filter_id':'NATIVE_FILTER-S7-DATES',
            'setup_scope':'operator uses native MCP for charts/dashboard, REST for native filter metadata and one-point trend markers; analysis does not get dataset creation tools'})
    report['checks_passed']=True;save(ROOT/'evidence/s7-dates.json',report)
    print('Date ranges/live source regression passed; dashboard',did,flush=True)


if __name__=='__main__':
    from pathlib import Path
    parser=argparse.ArgumentParser();parser.add_argument('--fixture',choices=['apply','restore']);args=parser.parse_args()
    if args.fixture:fixture(args.fixture)
    else:asyncio.run(main())

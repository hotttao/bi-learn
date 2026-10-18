"""S6 fixed native MCP writes with local retry/reconciliation bookkeeping.

All asset mutations use official MCP tools. REST is used only for independent
readback and exact-name recovery. No department identity or Wren sync is implied.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from discover_s6 import ROOT, URL, payload
from s3_assets import API, BASE

DATASET_NAME='008_s6_orders_october_v1'
BAR_NAME='008 S6 行业净收入'
KPI_NAME='008 S6 净收入'
DASHBOARD_NAME='008 S6 MCP 固定调用实验'
BAR={'chart_type':'xy','kind':'bar','x':{'name':'industry'},
     'y':[{'name':'net_amount','aggregate':'SUM','label':'net_revenue'}],
     'orientation':'vertical','y_axis':{'format':',.2f'},'row_limit':1000}
KPI={'chart_type':'big_number','metric':{'name':'net_amount','aggregate':'SUM','label':'net_revenue'},
     'subheader':'人民币元 · 固定2026年10月 · 净收入v1','y_axis_format':',.2f'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def layout_ids(dashboard):
    return sorted(n['meta']['chartId'] for n in json.loads(dashboard['position_json']).values()
                  if isinstance(n,dict) and n.get('type')=='CHART')


async def main():
    api=API()
    original=api.call('GET','/api/v1/dashboard/1')['result']
    baseline={k:original[k] for k in ['position_json','json_metadata','dashboard_title']}
    journal_path=ROOT/'.local/s6-operations.json'
    journal=json.loads(journal_path.read_text()) if journal_path.exists() else {}
    report={'verified_at_utc':datetime.now(timezone.utc).isoformat(),'transport':'native Streamable HTTP MCP',
            'scope':'local admin development identity; department isolation deferred','calls':[],'reconciliation':[]}
    def checkpoint():
        save(journal_path,journal);save(ROOT/'.local/s6-progress.json',report)
    sql=(ROOT/'sql/trino/orders_enriched_v1.sql').read_text().strip().rstrip(';')
    scoped_sql="SELECT * FROM (\n"+sql+"\n) source_orders\nWHERE order_date >= DATE '2026-10-01' AND order_date < DATE '2026-11-01'"
    async with httpx.AsyncClient(trust_env=False,timeout=120) as client:
        async with streamable_http_client(URL,http_client=client) as (read,write,_):
            async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=120)) as session:
                await session.initialize()
                async def call(name,request=None,allow_error=False):
                    args={'request':request} if request is not None else {}
                    result=payload(await session.call_tool(name,args))
                    report['calls'].append({'tool':name,'arguments':args,'result':result});checkpoint()
                    if not allow_error and isinstance(result,dict) and (result.get('error') or result.get('success') is False):
                        raise RuntimeError(name+': '+json.dumps(result,ensure_ascii=False)[:2000])
                    return result
                report['health']=await call('health_check')
                instance=await call('get_instance_info')
                report['identity']=instance.get('current_user')
                dataset_id=api.find('dataset','table_name',DATASET_NAME)
                if dataset_id is None:
                    await call('create_virtual_dataset',{'database_id':2,'dataset_name':DATASET_NAME,'sql':scoped_sql,
                        'description':'S6 only: S4 net_revenue@1 SQL, fixed [2026-10-01,2026-11-01). Native DATE filter workaround, not Wren synchronization.'})
                    dataset_id=api.find('dataset','table_name',DATASET_NAME)
                assert dataset_id is not None
                dataset=api.call('GET',f'/api/v1/dataset/{dataset_id}')['result']
                assert dataset['sql'].strip().rstrip(';')==scoped_sql and dataset['database']['id']==2
                report['dataset']={'id':dataset_id,'name':DATASET_NAME,'source_dataset_id':2,'s4_sql_sha256':hashlib.sha256(sql.encode()).hexdigest(),
                    'scope_sql_sha256':hashlib.sha256(scoped_sql.encode()).hexdigest(),'sql':scoped_sql,'automatic_wren_sync':False}
                await call('get_dataset_info',{'identifier':dataset_id,'force_refresh':True})
                before_preview=api.find('chart','slice_name',BAR_NAME)
                preview=await call('generate_chart',{'dataset_id':dataset_id,'config':BAR,'chart_name':BAR_NAME,
                        'save_chart':False,'generate_preview':True,'preview_formats':['url','table','vega_lite'],'force_refresh':True})
                assert preview['chart'] is None and preview['explore_url'] and api.find('chart','slice_name',BAR_NAME)==before_preview
                report['preview']={'explore_url':preview['explore_url'],'form_data_key':preview['form_data_key'],
                                   'no_chart_created':True,'rendered_in_browser':False,'previews':preview['previews']}
                await call('get_chart_info',{'form_data_key':preview['form_data_key']})
                preview_data=await call('get_chart_data',{'form_data_key':preview['form_data_key'],'force_refresh':True})
                report['preview_data']=preview_data

                async def ensure_chart(name,config,simulate_lost_record=False):
                    request={'dataset_id':dataset_id,'config':config,'chart_name':name,'save_chart':True,
                             'generate_preview':False,'force_refresh':True}
                    request_hash=digest(request)
                    existing=journal.get(name)
                    if existing and existing['request_hash']!=request_hash:
                        raise RuntimeError('Request changed; review before replacing existing operation')
                    chart_id=api.find('chart','slice_name',name)
                    if chart_id is not None and not existing:
                        raise RuntimeError('Unowned same-name chart exists; inspect rather than overwrite')
                    if chart_id is None:
                        journal[name]={'request_hash':request_hash,'state':'pending'};checkpoint()
                        saved=await call('generate_chart',request)
                        chart_id=saved['chart']['id']
                        if simulate_lost_record:
                            # Success arrived, but pretend the client lost the ID before persisting.
                            assert journal[name]['state']=='pending'
                            recovered=api.find('chart','slice_name',name)
                            assert recovered==chart_id
                            report['reconciliation'].append({'name':name,'case':'save completed but local chart ID not persisted',
                                'recovered_id':recovered,'create_replayed':False,'real_network_timeout_simulated':False})
                            chart_id=recovered
                    chart=api.call('GET',f'/api/v1/chart/{chart_id}')['result']
                    assert chart['datasource_id']==dataset_id
                    params=json.loads(chart['params'])
                    if existing and existing.get('params_hash') and existing['params_hash']!=digest(params):
                        raise RuntimeError('Saved chart was edited; preserve it and review changes')
                    journal[name]={'request_hash':request_hash,'state':'saved','chart_id':chart_id,'params_hash':digest(params)}
                    checkpoint()
                    return chart_id

                kpi_id=await ensure_chart(KPI_NAME,KPI)
                bar_id=await ensure_chart(BAR_NAME,BAR,simulate_lost_record=True)
                dashboard_id=api.find('dashboard','dashboard_title',DASHBOARD_NAME)
                if dashboard_id is None:
                    await call('generate_dashboard',{'chart_ids':[kpi_id],'dashboard_title':DASHBOARD_NAME,
                        'description':'S6 official MCP experiment; CNY net_revenue@1; fixed October 2026; S4 semantic SQL baseline.',
                        'published':True})
                    dashboard_id=api.find('dashboard','dashboard_title',DASHBOARD_NAME)
                assert dashboard_id is not None
                dashboard=api.call('GET',f'/api/v1/dashboard/{dashboard_id}')['result']
                if bar_id not in layout_ids(dashboard):
                    await call('add_chart_to_existing_dashboard',{'dashboard_id':dashboard_id,'chart_id':bar_id})
                after=api.call('GET',f'/api/v1/dashboard/{dashboard_id}')['result']
                assert layout_ids(after)==sorted([kpi_id,bar_id])
                position_before=after['position_json']
                duplicate=await call('add_chart_to_existing_dashboard',{'dashboard_id':dashboard_id,'chart_id':bar_id},allow_error=True)
                assert duplicate.get('error') and 'already' in duplicate['error']
                assert api.call('GET',f'/api/v1/dashboard/{dashboard_id}')['result']['position_json']==position_before
                report['native_duplicate_add']={'returned_error':duplicate['error'],'layout_unchanged':True}
                await call('get_chart_info',{'identifier':bar_id})
                await call('get_dashboard_info',{'identifier':dashboard_id,'force_refresh':True})
                report['data']={}
                for label,cid in [('kpi',kpi_id),('industry',bar_id)]:
                    report['data'][label]=await call('get_chart_data',{'identifier':cid,'force_refresh':True})
                # Second ensure pass uses exact-name reconciliation; no creation is repeated.
                creates_before=sum(c['tool'] in ['generate_chart','generate_dashboard','create_virtual_dataset'] for c in report['calls'])
                assert await ensure_chart(KPI_NAME,KPI)==kpi_id and await ensure_chart(BAR_NAME,BAR)==bar_id
                creates_after=sum(c['tool'] in ['generate_chart','generate_dashboard','create_virtual_dataset'] for c in report['calls'])
                assert creates_before==creates_after
                report['repeat_ensure']={'same_chart_ids':True,'new_create_calls':0}
                report.update({'dashboard_id':dashboard_id,'chart_ids':{'kpi':kpi_id,'industry':bar_id},
                               'dashboard_url':BASE+f'/superset/dashboard/{dashboard_id}/'})
    unchanged=api.call('GET','/api/v1/dashboard/1')['result']
    assert all(unchanged[k]==v for k,v in baseline.items())
    report['baseline_dashboard_unchanged']=True
    report['not_verified']=['department isolation (deferred)','arbitrary date filter generation on Trino',
                            'Wren→Superset synchronization','production concurrency and true network failure recovery']
    checkpoint()
    save(ROOT/'evidence/s6-assets.json',report)
    print('S6 native MCP assets saved:',report['dashboard_url'],'chart ids',report['chart_ids'])


if __name__=='__main__':
    with (ROOT/'.local/s6-assets.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        asyncio.run(main())

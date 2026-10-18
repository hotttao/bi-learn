"""Read the native chart schema and create an unsaved, explicitly dated preview."""
import asyncio
from datetime import timedelta
import json

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from discover_s6 import ROOT, URL, payload

BAR = {'chart_type':'xy','kind':'bar','x':{'name':'industry'},
       'y':[{'name':'net_revenue','saved_metric':True}], 'orientation':'vertical',
       'filters':[{'column':'order_date','op':'>=','value':'2026-10-01'},
                  {'column':'order_date','op':'<','value':'2026-11-01'}],
       'y_axis':{'format':',.2f'},'row_limit':1000}


async def main():
    report={}
    async with httpx.AsyncClient(trust_env=False,timeout=120) as client:
        async with streamable_http_client(URL,http_client=client) as (read,write,_):
            async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=120)) as session:
                await session.initialize()
                for name,args in [('get_dataset_info',{'request':{'identifier':2,'force_refresh':True}}),
                                  ('get_chart_type_schema',{'chart_type':'xy'}),
                                  ('generate_chart',{'request':{'dataset_id':2,'config':BAR,'chart_name':'008 S6 行业净收入',
                                                               'save_chart':False,'preview_formats':['url','table','vega_lite'],'force_refresh':True}})]:
                    report[name]=payload(await session.call_tool(name,args))
                    (ROOT/'evidence/s6-probe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
                    print(name,'keys:',list(report[name]) if isinstance(report[name],dict) else type(report[name]).__name__)
                print(json.dumps(report['generate_chart'],ensure_ascii=False)[:3500])


if __name__=='__main__':asyncio.run(main())

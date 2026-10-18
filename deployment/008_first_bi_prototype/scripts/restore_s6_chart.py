"""Restore the S6-owned bar after the verified browser editing exercise via MCP."""
import asyncio
from datetime import timedelta
import json

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from discover_s6 import ROOT, URL, payload
from s3_assets import API
from s6_assets import BAR, BAR_NAME, digest


async def main():
    browser=json.loads((ROOT/'evidence/s6-browser.json').read_text())
    assert browser.get('ui_edit',{}).get('saved') and browser['ui_edit']['reopened']
    assets=json.loads((ROOT/'evidence/s6-assets.json').read_text())
    cid=assets['chart_ids']['industry'];api=API()
    before=api.call('GET',f'/api/v1/chart/{cid}')['result']
    assert before['slice_name']==BAR_NAME and before['datasource_id']==assets['dataset']['id']
    assert json.loads(before['params'])['orientation']=='horizontal','Only restore the known browser exercise state'
    report={'chart_id':cid,'before_orientation':'horizontal','calls':[]}
    async with httpx.AsyncClient(trust_env=False,timeout=120) as client:
        async with streamable_http_client(URL,http_client=client) as (read,write,_):
            async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=120)) as session:
                await session.initialize()
                for name,request in [('get_chart_info',{'identifier':cid}),
                    ('update_chart',{'identifier':cid,'config':BAR,'generate_preview':False}),
                    ('get_chart_data',{'identifier':cid,'force_refresh':True})]:
                    result=payload(await session.call_tool(name,{'request':request}))
                    assert not result.get('error') and result.get('success') is not False,result
                    report['calls'].append({'tool':name,'request':request,'result':result})
    after=api.call('GET',f'/api/v1/chart/{cid}')['result'];params=json.loads(after['params'])
    assert params['orientation']=='vertical' and after['datasource_id']==before['datasource_id']
    report['restored_orientation']='vertical'
    journal_path=ROOT/'.local/s6-operations.json';journal=json.loads(journal_path.read_text())
    journal[BAR_NAME]['params_hash']=digest(params)
    journal[BAR_NAME]['note']='Browser exercise saved horizontal; official update_chart restored verified original BAR config'
    journal_path.write_text(json.dumps(journal,ensure_ascii=False,indent=2)+'\n')
    (ROOT/'evidence/s6-ui-restore.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('S6 UI edit read back and restored through native update_chart')


if __name__=='__main__':asyncio.run(main())

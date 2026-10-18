"""Discover the native Superset 6.1.0 Streamable HTTP MCP contract."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[1]
URL = 'http://127.0.0.1:5008/mcp'


def payload(result):
    if result.isError:raise RuntimeError(str(result.content))
    if result.structuredContent is not None:return result.structuredContent
    text='\n'.join(c.text for c in result.content if c.type=='text')
    try:return json.loads(text)
    except json.JSONDecodeError:return text


async def main():
    async with httpx.AsyncClient(trust_env=False,timeout=120) as client:
        async with streamable_http_client(URL,http_client=client) as (read,write,_):
            async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=120)) as session:
                initialize=await session.initialize()
                tools=(await session.list_tools()).tools
                report={'verified_at_utc':datetime.now(timezone.utc).isoformat(),'endpoint':URL,
                        'server':initialize.serverInfo.model_dump(mode='json'),
                        'tools':[t.model_dump(mode='json',exclude_none=True) for t in tools],
                        'resources':[r.model_dump(mode='json') for r in (await session.list_resources()).resources],
                        'health':payload(await session.call_tool('health_check',{}))}
                (ROOT/'evidence/s6-discovery.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
                print('Native Superset MCP:',len(tools),'tools; health:',report['health'])


if __name__=='__main__':asyncio.run(main())

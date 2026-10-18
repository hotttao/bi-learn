"""Release consistency faults and actual query-only MCP discovery."""
import asyncio
from datetime import datetime,timezone
from pathlib import Path
import shutil
import tempfile
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from guarded_wren_mcp import ScopeError
from prepare_s7 import ROOT,save
from prepare_s9_wiki import RELEASE,verify_release


async def main():
    manifest=verify_release();faults={}
    for name,relative,operation in [
        ('missing_topic','wiki/relationships/current-industry.md','delete'),
        ('modified_native_yaml','wren/models/sales_orders/metadata.yml','modify'),
        ('modified_rule','wiki/metrics/net-revenue.md','modify'),
        ('mixed_execution_revision','release.json','revision')]:
        with tempfile.TemporaryDirectory(prefix='bi008-s9-fault-') as tmp:
            root=Path(tmp);shutil.copytree(RELEASE,root,dirs_exist_ok=True)
            path=root/relative
            if operation=='delete':path.unlink()
            elif operation=='modify':path.write_text(path.read_text()+'\n# unreviewed change\n')
            else:
                import json
                altered=json.loads(path.read_text());altered['wren_source_revision']='invalid-revision';save(path,altered)
            try:verify_release(root)
            except ScopeError as exc:faults[name]=str(exc)
            else:raise AssertionError(name+' was not rejected')
    with (ROOT/'.local/s9-wiki-fixed-mcp.log').open('w') as log:
        async with stdio_client(StdioServerParameters(command=str(ROOT/'scripts/s9_wiki_query_mcp.sh')),errlog=log) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize();names=sorted(t.name for t in (await session.list_tools()).tools)
                assert names==['dry_plan','dry_run','run_sql'],names
    assert verify_release()==manifest
    save(ROOT/'evidence/s9-wiki-controls.json',{'verified_at_utc':datetime.now(timezone.utc).isoformat(),
        'tools':names,'faults_rejected':faults,'published_release_unchanged':True,
        'limit':'Consistency checks, not signatures or authorization; faults injected into disposable copies only.'})
    print('Query-only discovery and four release consistency refusals passed')


if __name__=='__main__':asyncio.run(main())

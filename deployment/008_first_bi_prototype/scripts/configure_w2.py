"""Configure only this prototype's Codex MCP entry; preserve other config."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tomllib

ROOT=Path(__file__).resolve().parents[1]
TOOLS=['get_runtime_contract','list_models','describe_model','list_cubes','describe_cube','get_instructions',
       'get_context','recall_queries','dry_plan','dry_run','run_sql','query_cube']


def codex_path():
    if found:=shutil.which('codex'):return Path(found)
    candidates=list((Path.home()/'.codex/packages/app-server-daemon/releases').glob('*/bin/codex'))
    def key(path):
        match=re.match(r'(\d+)\.(\d+)\.(\d+)',path.parents[1].name)
        return tuple(map(int,match.groups())) if match else (0,0,0)
    candidates=[p for p in candidates if os.access(p,os.X_OK)]
    if not candidates:raise SystemExit('No installed Codex CLI found; install official Codex before this integration')
    return max(candidates,key=key)


def main():
    binary=codex_path()
    config=ROOT/'.codex/config.toml'
    expected={'command':str(ROOT/'scripts/wren_mcp.sh'),'cwd':str(ROOT/'wren'),
              'enabled_tools':TOOLS,'required':True,'startup_timeout_sec':30,'tool_timeout_sec':90,
              'default_tools_approval_mode':'approve'}
    block='\n[mcp_servers.bi008_wren]\n'+''.join(f'{k} = {json.dumps(v,ensure_ascii=False)}\n' for k,v in expected.items())
    config.parent.mkdir(parents=True,exist_ok=True)
    previous=config.read_text() if config.exists() else ''
    parsed=tomllib.loads(previous)
    old=parsed.get('mcp_servers',{}).get('bi008_wren')
    if old is None:config.write_text(previous+block)
    elif old!=expected:raise SystemExit('Existing bi008_wren entry differs; review it before replacing')
    local=ROOT/'.local/codex-bin'
    if local.is_symlink():local.unlink()
    elif local.exists():raise SystemExit('Unexpected regular file at .local/codex-bin')
    local.symlink_to(binary)
    result=subprocess.run([str(binary),'mcp','list','--json'],cwd=ROOT,capture_output=True,text=True,check=True)
    entries=json.loads(result.stdout)
    check=next((s for s in entries if s['name']=='bi008_wren'),None)
    if check is None:raise SystemExit('Codex did not load the project MCP entry: '+result.stderr)
    report={'codex_version':subprocess.check_output([str(binary),'--version'],text=True).strip(),
            'binary':str(binary),'config_scope':'prototype-local .codex/config.toml; user/global and repository-root config untouched',
            'entry':check,'enabled_tools':TOOLS,'transport':'stdio','model_session_invocation_verified':False}
    (ROOT/'evidence/w2-codex-config.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('Codex project MCP entry loaded:',check['name'])


if __name__=='__main__':main()

"""Run the installed Codex with file Wiki and the existing Wren query tools."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from prepare_s7 import ROOT
from prepare_s9_wiki import RELEASE,verify_release


def main():
    parser=argparse.ArgumentParser();parser.add_argument('question');args=parser.parse_args()
    verify_release()
    with tempfile.TemporaryDirectory(prefix='bi008-s9-wiki-user-') as tmp:
        shutil.copytree(RELEASE,tmp,dirs_exist_ok=True);verify_release(Path(tmp))
        command=[str(ROOT/'.local/codex-bin'),'exec','--ephemeral','--skip-git-repo-check','--disable','multi_agent',
            '-c','approval_policy="never"','-c','sandbox_mode="read-only"',
            '-c','mcp_servers.bi008_s9_query.command='+json.dumps(str(ROOT/'scripts/s9_wiki_query_mcp.sh')),
            '-c','mcp_servers.bi008_s9_query.enabled_tools='+json.dumps(['dry_plan','dry_run','run_sql']),
            '-c','mcp_servers.bi008_s9_query.default_tools_approval_mode="approve"',
            '-c','mcp_servers.bi008_s9_query.required=true',
            '--output-schema',str(ROOT/'s8/answer.schema.json'),args.question]
        subprocess.run(command,cwd=tmp,check=True)
        verify_release(Path(tmp))


if __name__=='__main__':main()

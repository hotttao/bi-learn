"""One user question via the existing Codex product and the dedicated topic MCP.

This launcher supplies context/config; it does not plan, infer, or run an Agent.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
from configure_w2 import TOOLS
from prepare_s7 import ROOT


def main():
    parser=argparse.ArgumentParser();parser.add_argument('question');args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='bi008-s9-user-') as tmp:
        Path(tmp,'AGENTS.md').write_text((ROOT/'s9/ANALYSIS.md').read_text())
        command=[str(ROOT/'.local/codex-bin'),'exec','--ephemeral','--skip-git-repo-check',
                 '--disable','shell_tool','--disable','unified_exec','--disable','multi_agent',
                 '-c','approval_policy="never"','-c','sandbox_mode="read-only"',
                 '-c','mcp_servers.bi008_s9.command='+json.dumps(str(ROOT/'scripts/s9_wren_mcp.sh')),
                 '-c','mcp_servers.bi008_s9.enabled_tools='+json.dumps(TOOLS+['list_topics']),
                 '-c','mcp_servers.bi008_s9.default_tools_approval_mode="approve"','-c','mcp_servers.bi008_s9.required=true','-c','mcp_servers.bi008_s9.tool_timeout_sec=90',
                 '--output-schema',str(ROOT/'s8/answer.schema.json'),args.question]
        subprocess.run(command,cwd=tmp,check=True)


if __name__=='__main__':main()

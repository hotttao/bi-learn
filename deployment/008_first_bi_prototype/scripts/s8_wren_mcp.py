"""Reuse the audited Wren MCP with an isolated S8 project; no new Agent."""
from pathlib import Path
import sys
from guarded_wren_mcp import make_server

if __name__=='__main__':make_server(Path(sys.argv[1])).run(transport='stdio')

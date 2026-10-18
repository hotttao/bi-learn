#!/bin/sh
# Use the existing Codex product with the local analysis context and MCP tools.
set -eu
BI008_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
"$BI008_ROOT/.local/wren-venv/bin/python" "$BI008_ROOT/scripts/configure_s7_dates.py"
exec "$BI008_ROOT/.local/codex-bin" -C "$BI008_ROOT/.local/s7-date-analysis" \
  -s read-only --disable shell_tool --disable unified_exec --disable multi_agent "$@"

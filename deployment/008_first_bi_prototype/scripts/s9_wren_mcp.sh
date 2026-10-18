#!/bin/sh
set -eu
BI008_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
export WREN_HOME="$BI008_ROOT/wren/runtime"
export WREN_MEMORY_BACKEND=grep
export WREN_PROJECT_HOME="$BI008_ROOT/.local/s8/catalogs/500/schema_and_rules"
exec "$BI008_ROOT/.local/wren-venv/bin/python" "$BI008_ROOT/scripts/topic_wren_mcp.py"

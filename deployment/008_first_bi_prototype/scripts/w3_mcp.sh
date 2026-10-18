#!/bin/sh
set -eu
BI008_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "$BI008_ROOT/.local/wren-venv/bin/python" "$BI008_ROOT/scripts/w3_mcp.py"

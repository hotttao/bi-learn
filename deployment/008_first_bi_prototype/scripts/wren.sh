#!/bin/sh
# Project-local CLI and strict configuration; no global profile changes.
set -eu
BI008_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
export WREN_HOME="$BI008_ROOT/wren/runtime"
export WREN_PROJECT_HOME="$BI008_ROOT/wren"
export WREN_MEMORY_BACKEND=grep
exec "$BI008_ROOT/.local/wren-venv/bin/wren" "$@"

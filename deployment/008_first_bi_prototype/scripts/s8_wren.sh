#!/bin/sh
# Explicit S8 project selector; never changes the normal Wren project/config.
set -eu
BI008_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BI008_SELECTION=${1:?Use 10000, 100000, 1000000, schema_only, or schema_and_rules}
shift
case "$BI008_SELECTION" in
  10000|100000|1000000) BI008_PROJECT="$BI008_ROOT/.local/s8/projects/$BI008_SELECTION" ;;
  schema_only|schema_and_rules) BI008_PROJECT="$BI008_ROOT/.local/s8/catalogs/500/$BI008_SELECTION" ;;
  *) echo 'Unknown S8 project' >&2; exit 2 ;;
esac
[ -f "$BI008_PROJECT/target/mdl.json" ] || { echo 'Run prepare_s8.py first' >&2; exit 2; }
export WREN_HOME="$BI008_ROOT/wren/runtime"
export WREN_PROJECT_HOME="$BI008_PROJECT"
export WREN_MEMORY_BACKEND=grep
exec "$BI008_ROOT/.local/wren-venv/bin/wren" "$@"

#!/usr/bin/env bash
# Run from any directory: sudo bash scripts/verify_s1.sh [--local-proxy]
# Downloads/builds images and runs disposable probes; does not initialize a DB.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

compose=(docker-compose -f compose.yaml)
case "${1:-}" in
  '') ;;
  --local-proxy) compose+=(-f host/compose.build-proxy.yaml) ;;
  *) echo 'Usage: verify_s1.sh [--local-proxy]' >&2; exit 2 ;;
esac

"${compose[@]}" config --quiet
docker version --format 'Docker server: {{.Server.Version}}'
docker-compose version

for ref in postgres:17.6-bookworm apache/superset:6.1.0; do
  # Keep an already downloaded fixed tag; record its actual digest below.
  if ! docker image inspect "$ref" >/dev/null 2>&1; then
    for attempt in 1 2 3; do
      if docker pull --quiet "$ref"; then break; fi
      if [[ "$attempt" == 3 ]]; then exit 1; fi
      echo "Pull failed for $ref; retrying ($attempt/3)." >&2
      sleep 3
    done
  fi
done

BUILDKIT_PROGRESS=plain "${compose[@]}" build superset
docker run --rm --network none postgres:17.6-bookworm postgres --version
"${compose[@]}" run --rm --no-deps --entrypoint python superset -c '
import os
import runpy
from importlib.metadata import version
import psycopg2
import superset
config = runpy.run_path(os.environ["SUPERSET_CONFIG_PATH"])
assert version("apache-superset") == "6.1.0"
assert version("psycopg2-binary") == "2.9.10"
assert config["DATA_CACHE_CONFIG"]["CACHE_TYPE"] == "NullCache"
print("PASS: Superset 6.1.0, psycopg2-binary 2.9.10, configuration loaded.")
'
docker image inspect postgres:17.6-bookworm apache/superset:6.1.0 bi008-superset:6.1.0 \
  --format '{{json .RepoTags}} {{.Id}} {{json .RepoDigests}}'
"${compose[@]}" ps --all
echo 'S1 image probes passed. No business data or BI application was initialized.'

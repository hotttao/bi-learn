"""Start S2 sources, seed twice, reconcile, restart and verify persistence.

Only the two synthetic business sources are started/restarted. Run after review
of the scripts using: sudo python3 scripts/verify_s2.py
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from source_db import COMPOSE, ROOT, check, query, redact, run


def command(args):
    result = run(args)
    if result.returncode:
        raise RuntimeError(redact(result.stdout + result.stderr))
    return result.stdout.strip()


def source(action):
    return command([sys.executable, str(ROOT / "scripts/source_db.py"), action])


def main():
    command(COMPOSE + ["config", "--quiet"])
    print("Starting mysql-source and crm-source; waiting for health checks...", flush=True)
    command(COMPOSE + ["up", "-d", "--wait", "--wait-timeout", "240", "mysql-source", "crm-source"])
    print(source("seed"), flush=True)
    first = json.loads(source("verify"))
    print(source("seed"), flush=True)
    second = json.loads(source("verify"))
    check(first == second, "Second initialization changed raw records or verification results")
    print("Repeated initialization passed. Restarting the two source containers...", flush=True)
    command(COMPOSE + ["stop", "mysql-source", "crm-source"])
    command(COMPOSE + ["up", "-d", "--wait", "--wait-timeout", "120", "mysql-source", "crm-source"])
    third = json.loads(source("verify"))
    check(first == third, "Source data or verification changed after restart")
    images = json.loads(command([
        "docker", "image", "inspect", "mysql:8.4.6", "postgres:17.6-bookworm"
    ]))
    report = {
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "versions": {
            "mysql": query("mysql", "SELECT VERSION();"),
            "postgres": query("crm", "SHOW server_version;"),
        },
        "images": [{"tags": i["RepoTags"], "id": i["Id"], "digests": i["RepoDigests"]} for i in images],
        "reconciliation": third,
        "repeat_initialization": "passed: same complete raw-record fingerprint",
        "stop_start_persistence": "passed: same complete raw-record fingerprint",
        "not_tested": ["Superset UI", "cross-source SQL", "department isolation", "MCP", "Wren"],
    }
    destination = ROOT / "evidence/s2-sources.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if os.geteuid() == 0 and "SUDO_UID" in os.environ:
        os.chown(destination, int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"]))
    print(command(COMPOSE + ["ps", "--all"]), flush=True)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    print("S2 passed. Business sources remain running; no Superset services started.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, AssertionError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)

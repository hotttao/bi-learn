"""S3 runtime, native BI assets and browser checks; requires Docker access.

Usage: sudo python3 scripts/deploy_s3.py --phase runtime|assets|browser|finish
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from source_db import COMPOSE, ROOT, redact


def execute(args, env=None):
    result = subprocess.run(args, cwd=ROOT, env=env, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(redact(result.stdout + result.stderr))
    print(redact(result.stdout[-4000:]), flush=True)


def runtime():
    execute(COMPOSE + ["config", "--quiet"])
    print("Building MySQL-capable Superset image...", flush=True)
    execute(["docker-compose", "-f", "compose.yaml", "-f", "host/compose.build-proxy.yaml", "build", "superset"])
    execute(COMPOSE + ["up", "-d", "--wait", "--wait-timeout", "180", "metadata", "mysql-source", "crm-source"])
    for cmd in (["superset", "db", "upgrade"], ["superset", "init"], ["python", "/app/pythonpath/bootstrap_admin.py"]):
        print("Initializing:", " ".join(cmd), flush=True)
        execute(COMPOSE + ["run", "--rm", "--no-deps", "superset-init"] + cmd)
    execute(COMPOSE + ["up", "-d", "--wait", "--wait-timeout", "180", "superset"])
    execute(COMPOSE + ["ps", "--all"])


def browser():
    env = os.environ.copy()
    env.update({
        "npm_config_proxy": "http://127.0.0.1:7897",
        "npm_config_https_proxy": "http://127.0.0.1:7897",
    })
    package = ROOT / ".local/browser/node_modules/playwright-core/package.json"
    if not package.exists():
        execute(["npm", "install", "--prefix", ".local/browser", "--no-audit", "--no-fund", "playwright-core@1.56.1"], env)
    execute(["node", "scripts/verify_s3_browser.mjs"])


def finish():
    import requests
    from importlib.metadata import version
    from s3_assets import BASE, save_json
    from source_db import check, query

    client = requests.Session()
    client.trust_env = False
    check(client.get(BASE + "/health", timeout=10).status_code == 200, "LAN health check failed")
    original = query("mysql", "SELECT paid_amount FROM orders WHERE order_id=2 AND dept_id='A';")
    check(original == "80.00", "Order 2 was not restored")
    inspect = subprocess.run(["docker", "inspect", "bi008-superset-1"], check=True, capture_output=True, text=True)
    container = json.loads(inspect.stdout)[0]
    report = {
        "url": BASE,
        "health": container["State"]["Health"]["Status"],
        "published_ports": container["NetworkSettings"]["Ports"],
        "image_id": container["Image"],
        "order_2_paid_amount": original,
        "browser": json.loads((ROOT / "evidence/s3-browser.json").read_text()),
        "host_requests_version": version("requests"),
        "headless_browser": subprocess.check_output(["google-chrome", "--version"], text=True).strip(),
        "not_verified": ["chart position drag in browser automation", "access from a second LAN device", "department isolation", "cross-source SQL", "MCP", "Wren"],
    }
    save_json(ROOT / "evidence/s3-final-state.json", report)
    execute(COMPOSE + ["ps", "--all"])
    print("S3 core checks complete; see evidence/s3-final-state.json for scope and remaining manual checks.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True, choices=["runtime", "assets", "browser", "finish"])
    phase = parser.parse_args().phase
    if phase == "runtime":
        runtime()
    elif phase == "assets":
        from s3_assets import main as assets
        assets()
    elif phase == "browser":
        browser()
    else:
        finish()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(redact(str(error)), file=sys.stderr)
        sys.exit(1)

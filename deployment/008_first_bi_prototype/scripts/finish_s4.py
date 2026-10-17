"""Record S4's final running state after query, native asset and browser checks."""
from datetime import datetime, timezone
import hashlib
import json

import requests

from source_db import ROOT, COMPOSE, check, query, run
from s3_assets import BASE, save_json
from verify_s4 import SQL, amounts, summary


def command(args):
    result = run(args)
    check(result.returncode == 0, 'Final state command failed: ' + result.stderr[:300])
    return result.stdout.strip()


def main():
    assets = json.loads((ROOT / 'evidence/s4-assets.json').read_text())
    checks = json.loads((ROOT / 'evidence/s4-queries.json').read_text())
    browser = json.loads((ROOT / 'evidence/s4-browser.json').read_text())
    sql_hash = hashlib.sha256(SQL.encode()).hexdigest()
    check(assets['sql_sha256'] == checks['sql_sha256'] == sql_hash, 'Evidence references a different SQL revision')
    check(browser['restored']['industry'] == {'软件': 120, '零售': 125}, 'Browser baseline not restored')
    check(query('mysql', "SELECT paid_amount FROM orders WHERE order_id=2") == '80.00', 'Payment baseline changed')
    check(query('crm', "SELECT industry FROM customers WHERE dept_id='A' AND customer_id=1") == '软件', 'CRM baseline changed')
    check(amounts(summary()) == {'软件': '120.00', '零售': '125.00'}, 'Final Trino query differs')
    s = requests.Session()
    s.trust_env = False
    response = s.get(BASE + '/health', timeout=10)
    check(response.ok, 'Superset is not healthy')
    containers = {}
    for service in ['trino', 'superset', 'metadata', 'mysql-source', 'crm-source']:
        info = json.loads(command(['docker', 'inspect', f'bi008-{service}-1']))[0]
        state = info['State']
        check(state['Running'] and state.get('Health', {}).get('Status') == 'healthy', f'{service} not healthy')
        containers[service] = {'image_id': info['Image'], 'status': state['Health']['Status'],
                               'memory_limit_bytes': info['HostConfig']['Memory'], 'ports': info['HostConfig']['PortBindings']}
    check(containers['trino']['image_id'] == 'sha256:1629098dd4c6234ffab1e179d8a6da20964942a37f392d378b08b85231cd1079',
          'Trino image differs from verified official image config')
    check(containers['trino']['ports']['8080/tcp'][0] == {'HostIp': '127.0.0.1', 'HostPort': '18080'}, 'Trino listener differs')
    check(containers['superset']['ports']['8088/tcp'][0]['HostIp'] == '192.168.2.46', 'Superset LAN listener changed')
    stats = [json.loads(line) for line in command(['docker', 'stats', '--no-stream', '--format', '{{json .}}']).splitlines()]
    versions = command(COMPOSE + ['exec', '-T', 'superset', 'python', '-c',
        "from importlib.metadata import version; print({p:version(p) for p in ['apache-superset','trino','SQLAlchemy']})"])
    report = {'verified_at_utc': datetime.now(timezone.utc).isoformat(), 'dashboard_url': assets['dashboard_url'],
              'sql_sha256': sql_hash, 'containers': containers, 'python_versions': versions,
              'memory_observation': [{'name': x['Name'], 'memory': x['MemUsage']} for x in stats if x['Name'].startswith('bi008-')],
              'source_restored': {'order_2_paid': '80.00', 'A_1_industry': '软件'},
              'browser': browser, 'not_verified': checks['not_verified'] + ['S3 chart drag/reorder remains manual']}
    save_json(ROOT / 'evidence/s4-final-state.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

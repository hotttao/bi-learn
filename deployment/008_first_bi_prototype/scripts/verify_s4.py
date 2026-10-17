"""S4 live cross-source reconciliation and isolated relationship fixtures.

Run as the deployment operator (Docker access required for the reversible CRM edit).
The Trino HTTP identity is a label, not authenticated department isolation.
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import contextlib
import io
import json
import sys
import time

import requests

from source_db import ROOT, check, query, verify
from s3_assets import save_json

SQL = (ROOT / 'sql/trino/orders_enriched_v1.sql').read_text().strip().rstrip(';')
SESSION = requests.Session()
SESSION.trust_env = False


def trino(sql):
    response = SESSION.post('http://127.0.0.1:18080/v1/statement', data=sql.encode(),
                            headers={'X-Trino-User': 'bi008-research'}, timeout=15)
    response.raise_for_status()
    result = response.json(parse_float=str)
    query_id = result['id']
    rows = []
    columns = []
    deadline = time.monotonic() + 90
    while True:
        if 'error' in result:
            raise RuntimeError(json.dumps(result['error'], ensure_ascii=False))
        columns = result.get('columns', columns)
        rows.extend(result.get('data', []))
        if 'nextUri' not in result:
            break
        if time.monotonic() >= deadline:
            SESSION.delete(result['nextUri'], timeout=10)
            raise TimeoutError(query_id)
        response = SESSION.get(result['nextUri'], timeout=15)
        response.raise_for_status()
        result = response.json(parse_float=str)
    return {'query_id': query_id, 'columns': [c['name'] for c in columns], 'rows': rows}


def summary(sql=SQL):
    return trino(f"SELECT industry, SUM(net_amount) AS net FROM ({sql}) x GROUP BY industry ORDER BY industry")


def amounts(result):
    return {r[0]: str(Decimal(str(r[1])).quantize(Decimal('.01'))) for r in result['rows']}


def validate_customer_keys(rows):
    seen = set()
    for dept, customer, _ in rows:
        key = (dept, customer)
        if key in seen:
            raise ValueError(f'Duplicate customer key {key}; refuse dataset publication')
        seen.add(key)



def quality():
    result = trino("""WITH refund_totals AS (
        SELECT dept_id, order_id, SUM(refund_amount) refund_total
        FROM mysql.sales.refunds GROUP BY dept_id,order_id
    )
    SELECT 'duplicate_order' rule, COUNT(*) failures FROM
      (SELECT order_id FROM mysql.sales.orders GROUP BY order_id HAVING COUNT(*)>1) d
    UNION ALL SELECT 'duplicate_customer', COUNT(*) FROM
      (SELECT dept_id,customer_id FROM crm.public.customers GROUP BY dept_id,customer_id HAVING COUNT(*)>1) d
    UNION ALL SELECT 'invalid_order', COUNT(*) FROM mysql.sales.orders
      WHERE order_id IS NULL OR dept_id IS NULL OR customer_id IS NULL OR order_date IS NULL
         OR paid_amount IS NULL OR paid_amount<0
    UNION ALL SELECT 'invalid_refund', COUNT(*) FROM mysql.sales.refunds
      WHERE refund_id IS NULL OR dept_id IS NULL OR order_id IS NULL OR refund_date IS NULL
         OR refund_amount IS NULL OR refund_amount<0
    UNION ALL SELECT 'invalid_customer', COUNT(*) FROM crm.public.customers
      WHERE dept_id IS NULL OR customer_id IS NULL OR industry IS NULL OR trim(industry)=''
    UNION ALL SELECT 'orphan_refund', COUNT(*) FROM mysql.sales.refunds r
      LEFT JOIN mysql.sales.orders o ON o.dept_id=r.dept_id AND o.order_id=r.order_id WHERE o.order_id IS NULL
    UNION ALL SELECT 'excess_refund', COUNT(*) FROM refund_totals r
      JOIN mysql.sales.orders o ON o.dept_id=r.dept_id AND o.order_id=r.order_id WHERE r.refund_total>o.paid_amount
    """)
    check(all(row[1] == 0 for row in result['rows']), 'Data quality failed; refuse publication: ' + str(result['rows']))
    return result


def main():
    check(query('crm', "SELECT industry FROM customers WHERE dept_id='A' AND customer_id=1") == '软件',
          'CRM baseline differs; review before running the reversible change')
    report = {'verified_at_utc': datetime.now(timezone.utc).isoformat(),
              'contract': 'net_revenue@1', 'sql_sha256': hashlib.sha256(SQL.encode()).hexdigest(),
              'version': trino('SELECT version()'), 'quality': quality(), 'baseline': {}, 'fixtures': {}}
    rows = trino(f'SELECT * FROM ({SQL}) x ORDER BY order_id')
    check(len(rows['rows']) == 4 and len({r[0] for r in rows['rows']}) == 4, 'Order grain changed')
    total = trino(f'SELECT SUM(net_amount) FROM ({SQL}) x')
    check(Decimal(str(total['rows'][0][0])) == 245, 'Total differs')
    industry = summary()
    check(amounts(industry) == {'软件': '120.00', '零售': '125.00'}, 'Industry differs')
    daily = trino(f'SELECT order_date, SUM(net_amount) FROM ({SQL}) x GROUP BY order_date ORDER BY order_date')
    check([Decimal(str(r[1])) for r in daily['rows']] == [120, 125], 'Daily differs')
    departments = trino(f'SELECT dept_id, SUM(net_amount) FROM ({SQL}) x GROUP BY dept_id ORDER BY dept_id')
    check(amounts(departments) == {'A': '145.00', 'B': '100.00'}, 'Departments differ')
    report['baseline'] = dict(orders=rows, total=total, industry=industry, daily=daily, departments=departments)
    # Deliberately wrong queries are counterexamples, never published as datasets.
    raw = trino("""SELECT SUM(o.paid_amount - COALESCE(r.refund_amount, DECIMAL '0.00'))
        FROM mysql.sales.orders o LEFT JOIN mysql.sales.refunds r
        ON o.dept_id=r.dept_id AND o.order_id=r.order_id""")
    check(Decimal(str(raw['rows'][0][0])) == 345, 'Raw-refund counterexample differs')
    wrong = trino(f"SELECT COUNT(*), SUM(net_amount) FROM ({SQL.replace('o.dept_id = c.dept_id AND ', '')}) x")
    check(wrong['rows'][0][0] == 8 and Decimal(str(wrong['rows'][0][1])) == 490, 'Missing department counterexample differs')
    report['fixtures']['raw_refund_join_wrong_345'] = raw
    report['fixtures']['customer_id_only_wrong_8_rows_490'] = wrong
    # Query-scoped fixtures leave all source tables unchanged.
    missing_sql = SQL.replace('crm.public.customers c',
        "(SELECT * FROM crm.public.customers WHERE NOT (dept_id='A' AND customer_id=1)) c")
    missing = summary(missing_sql)
    check(amounts(missing) == {'未知行业': '70.00', '软件': '50.00', '零售': '125.00'}, 'Missing customer was lost')
    report['fixtures']['missing_customer_preserved'] = missing
    duplicate = trino("SELECT dept_id, customer_id, industry FROM crm.public.customers UNION ALL SELECT dept_id, customer_id, industry FROM crm.public.customers WHERE dept_id='A' AND customer_id=1")
    try:
        validate_customer_keys(duplicate['rows'])
    except ValueError as error:
        report['fixtures']['duplicate_dimension_rejected'] = {'query_id': duplicate['query_id'], 'result': str(error)}
    else:
        raise AssertionError('Duplicate fixture not rejected')
    null_or_negative = trino("""WITH fixture(paid_amount) AS (VALUES (CAST(NULL AS DECIMAL(12,2))), (DECIMAL '-1.00'))
        SELECT COUNT(*) FROM fixture WHERE paid_amount IS NULL OR paid_amount < 0""")
    check(null_or_negative['rows'] == [[2]], 'Invalid amount detection differs')
    report['fixtures']['null_negative_amount_quality_failures'] = null_or_negative
    excess = trino("""WITH r AS (SELECT dept_id,order_id,refund_amount FROM mysql.sales.refunds
        UNION ALL SELECT 'A', BIGINT '1', DECIMAL '100.00')
        SELECT o.order_id FROM mysql.sales.orders o JOIN r ON o.dept_id=r.dept_id AND o.order_id=r.order_id
        GROUP BY o.order_id,o.paid_amount HAVING SUM(r.refund_amount)>o.paid_amount""")
    check(excess['rows'] == [[1]], 'Excess refund not detected')
    report['fixtures']['excess_refund_quality_failure'] = excess
    changed = False
    try:
        changed = True
        query('crm', "UPDATE customers SET industry='零售' WHERE dept_id='A' AND customer_id=1", 'admin')
        updated = summary()
        check(amounts(updated) == {'软件': '50.00', '零售': '195.00'}, 'Current industry change differs')
        report['current_industry_update'] = updated
        changed_total = trino(f'SELECT SUM(net_amount) FROM ({SQL}) x')
        check(Decimal(str(changed_total['rows'][0][0])) == 245, 'Industry edit changed the total')
        report['current_industry_total'] = changed_total
    finally:
        if changed:
            query('crm', "UPDATE customers SET industry='软件' WHERE dept_id='A' AND customer_id=1", 'admin')
    restored = summary()
    check(amounts(restored) == {'软件': '120.00', '零售': '125.00'}, 'CRM restoration failed')
    report['restored'] = restored
    # Also checks all S2 constraints and the independent raw-record fingerprint.
    capture = io.StringIO()
    with contextlib.redirect_stdout(capture):
        verify()
    report['source_verification'] = json.loads(capture.getvalue())
    report['not_verified'] = ['department authentication/isolation (S5)', 'cross-source atomic snapshot',
                              'large-data performance (S8)', 'Wren MDL/MCP', 'production deployment']
    save_json(ROOT / 'evidence/s4-queries.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    if sys.argv[1:] == ['--query']:
        print(json.dumps(trino(sys.stdin.read()), ensure_ascii=False, indent=2))
    elif not sys.argv[1:]:
        main()
    else:
        raise SystemExit('usage: verify_s4.py [--query]')

"""Read live source types without replacing reviewed Wren models."""
from datetime import datetime, timezone
import json
from pathlib import Path
from trino.dbapi import connect
from wren.type_mapping import parse_type

ROOT = Path(__file__).resolve().parents[1]

def main():
    connection = connect(host='127.0.0.1', port=18080, user='bi008_wren_discovery', catalog='mysql', schema='sales')
    discovered = {}
    for catalog, schema, table in [('mysql','sales','orders'),('mysql','sales','refunds'),('crm','public','customers')]:
        cur = connection.cursor()
        cur.execute(f'SHOW COLUMNS FROM {catalog}.{schema}.{table}')
        discovered[table] = [{'name': row[0], 'raw_type': row[1], 'type': parse_type(row[1], 'trino')} for row in cur.fetchall()]
    cur = connection.cursor()
    cur.execute('SELECT dept_id,order_id,SUM(refund_amount) AS refund_total FROM mysql.sales.refunds GROUP BY dept_id,order_id')
    totals_types = {c[0]: parse_type(c[1], 'trino') for c in cur.description}
    report = {'discovered_at_utc': datetime.now(timezone.utc).isoformat(),
              'source': 'live Trino SHOW COLUMNS + aggregate result metadata',
              'tables': discovered, 'aggregate_types': totals_types,
              'business_rules_source': 'S2 enforced keys and S4 reviewed net_revenue@1; not auto-inferred from names',
              'mdl_decimal_adapter': 'generic DECIMAL; source precision retained in properties.source_type',
              'relationship_key_aliases': 'distinct target aliases avoid nullable-side key projection',
              'raw_refund_model_exposed': False}
    (ROOT / 'evidence/w1-discovery.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    connection.close()
    print('Live schema recorded; existing models left untouched')

if __name__ == '__main__': main()

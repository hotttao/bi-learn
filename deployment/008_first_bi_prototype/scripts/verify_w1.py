"""W1 live MDL regression. Read-only; fixtures modify in-memory manifests only."""
import base64
import copy
from datetime import datetime, timezone
from decimal import Decimal
from importlib.metadata import version
import hashlib
import json
from pathlib import Path
import subprocess

from wren.config import load_config
from wren.engine import WrenEngine
from wren.model.error import WrenError
from wren_core import cube_query_to_sql

from verify_s4 import quality, trino

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / 'wren'
MDL = PROJECT / 'target/mdl.json'
CONNECTION = json.loads((PROJECT / 'runtime/trino-connection.json').read_text())
CONFIG = load_config(PROJECT / 'runtime')


def engine(manifest):
    return WrenEngine(base64.b64encode(json.dumps(manifest).encode()).decode(), 'trino',
                      CONNECTION, config=CONFIG, fallback=False)


def check(ok, message):
    if not ok: raise AssertionError(message)


def run_query(e, sql):
    plan = e.dry_plan(sql)
    table = e.query(sql)
    return {'sql': sql, 'planned_sql': plan, 'arrow_schema': str(table.schema), 'rows': table.to_pylist()}


def main():
    check(CONFIG.strict_mode, 'Runtime strict_mode must be enabled')
    manifest = json.loads(MDL.read_text())
    report = {'verified_at_utc': datetime.now(timezone.utc).isoformat(),
        'versions': {p: version(p) for p in ['wrenai','wren-core-py','trino','pyarrow','sqlglot']},
        'mdl_sha256': hashlib.sha256(MDL.read_bytes()).hexdigest(),
        'strict_mode': True, 'sdk_fallback': False, 'quality': quality(), 'queries': {}, 'rejections': {}, 'fixtures': {}, 'known_issues': {}}
    e = engine(manifest)
    queries = {
        'orders': 'SELECT order_id,dept_id,customer_id,order_date,industry,paid_amount,refund_total,net_amount FROM bi_orders ORDER BY order_id',
        'total': 'SELECT SUM(net_amount) AS net_revenue FROM bi_orders',
        'industry': 'SELECT industry,SUM(net_amount) AS net_revenue FROM bi_orders GROUP BY industry ORDER BY industry',
        'department': 'SELECT dept_id,SUM(net_amount) AS net_revenue FROM bi_orders GROUP BY dept_id ORDER BY dept_id',
        'daily': 'SELECT order_date,SUM(net_amount) AS net_revenue FROM bi_orders GROUP BY order_date ORDER BY order_date',
        'one_day': "SELECT SUM(net_amount) AS net_revenue FROM bi_orders WHERE order_date >= DATE '2026-10-01' AND order_date < DATE '2026-10-02'",
        'empty': "SELECT COUNT(*) AS n FROM bi_orders WHERE order_date >= DATE '2026-11-01'",
    }
    for name, sql in queries.items(): report['queries'][name] = run_query(e, sql)
    q=report['queries']
    check([r['order_id'] for r in q['orders']['rows']]==[1,2,3,4], 'Order keys/grain changed')
    check(all(r['dept_id'] in ['A','B'] and r['customer_id'] in [1,2] for r in q['orders']['rows']), 'Nullable-side keys leaked')
    check([r['net_amount'] for r in q['orders']['rows']]==list(map(Decimal,['70','75','50','50'])), 'Order amounts differ')
    check(q['total']['rows']==[{'net_revenue':Decimal(245)}], 'Total differs')
    check({r['industry']:r['net_revenue'] for r in q['industry']['rows']}=={'软件':120,'零售':125}, 'Industry differs')
    check({r['dept_id']:r['net_revenue'] for r in q['department']['rows']}=={'A':145,'B':100}, 'Department differs')
    check([r['net_revenue'] for r in q['daily']['rows']]==[120,125], 'Daily differs')
    check(q['one_day']['rows']==[{'net_revenue':Decimal(120)}] and q['empty']['rows']==[{'n':0}], 'Date/empty differs')
    check(all(isinstance(r['net_amount'],Decimal) for r in q['orders']['rows']), 'Money decoded as float')
    cube_sql=cube_query_to_sql(json.dumps({'cube':'net_revenue','measures':['total'],'dimensions':['industry']}),MDL.read_text())
    report['cube']=run_query(e,cube_sql)
    check({r['industry']:r['total'] for r in report['cube']['rows']}=={'软件':120,'零售':125}, 'Cube differs')
    for sql in ['SELECT * FROM mysql.sales.orders','SELECT * FROM mysql.sales.refunds','SELECT * FROM crm.public.customers',
                'SELECT * FROM not_a_model','WITH x AS (SELECT * FROM mysql.sales.refunds) SELECT * FROM x',
                'UPDATE bi_orders SET paid_amount=0 WHERE 1=0','CREATE TABLE x AS SELECT * FROM bi_orders']:
        try:e.dry_plan(sql)
        except WrenError as error:
            check('SQL_POLICY_CHECK' in str(error), 'Rejected outside policy phase: '+str(error))
            report['rejections'][sql]=str(error)
        else:raise AssertionError('Unexpected policy acceptance: '+sql)
    # A query-scoped missing customer must retain all four orders and their keys.
    missing=copy.deepcopy(manifest)
    customer=next(m for m in missing['models'] if m['name']=='bi_customers')
    customer['refSql'] += " WHERE NOT (dept_id='A' AND customer_id=1)"
    with engine(missing) as fixture:
        result=run_query(fixture,queries['orders'])
        check([r['order_id'] for r in result['rows']]==[1,2,3,4], 'Missing-customer fixture lost keys/rows')
        check(result['rows'][0]['industry']=='未知行业' and sum(r['net_amount'] for r in result['rows'])==245, 'Missing customer handling differs')
        report['fixtures']['missing_customer']=result
    cents=copy.deepcopy(manifest)
    order_model=next(m for m in cents['models'] if m['name']=='bi_orders')
    order_model.pop('tableReference')
    order_model['refSql']="SELECT order_id,dept_id,customer_id,order_date,paid_amount + DECIMAL '0.01' AS paid_amount FROM mysql.sales.orders"
    with engine(cents) as fixture:
        literal_result=run_query(fixture,queries['total'])
        check(literal_result['rows']==[{'net_revenue':Decimal('245.00')}], 'Decimal-literal behavior changed; re-audit workaround')
        check("CAST('0.01' AS DECIMAL)" in literal_result['planned_sql'], 'Expected literal rewrite not observed')
        report['known_issues']['decimal_literal_scale_loss']=literal_result
    order_model['refSql']=order_model['refSql'].replace("DECIMAL '0.01'", "CAST('0.01' AS DECIMAL(12,2))")
    with engine(cents) as fixture:
        result=run_query(fixture,queries['total'])
        check(result['rows']==[{'net_revenue':Decimal('245.04')}], 'Explicit cent precision lost')
        check(isinstance(result['rows'][0]['net_revenue'],Decimal), 'Cent fixture decoded as float')
        report['fixtures']['cent_precision']=result
    # Preserve the observed qualifier issue as a planning-only regression probe.
    qualified='SELECT * FROM mysql.sales.bi_orders'
    plan=e.dry_plan(qualified)
    check(plan.rstrip().endswith(qualified), 'Qualifier behavior changed; re-audit workaround')
    report['known_issues']['qualified_model_name']={'input':qualified,'plan':plan,
        'status':'NOT_FIXED; planning only, not executed; bi_ naming avoids current physical-table collisions, not a general authorization fix'}
    # Reproduce the DECIMAL normalization mismatch without touching live sources.
    decimal_fixture={'catalog':'wren','schema':'public','models':[{'name':'probe','tableReference':{'catalog':'mysql','schema':'sales','table':'orders'},
        'columns':[{'name':'paid_amount','type':'DECIMAL(12, 2)'},{'name':'delta','type':'DECIMAL','isCalculated':True,'expression':'paid_amount-1'}]}]}
    with engine(decimal_fixture) as fixture:
        try:fixture.dry_plan('SELECT delta FROM probe')
        except WrenError as error:
            check('Utf8' in str(error), 'DECIMAL behavior changed; re-audit adapter')
            report['known_issues']['parameterized_decimal']=str(error)
        else:raise AssertionError('DECIMAL adapter may no longer be required; review')
    # Verify the actual CLI wrapper inherits strict configuration, not only SDK tests.
    cli = ROOT/'scripts/wren.sh'
    for sql in [queries['total'],'SELECT * FROM mysql.sales.refunds']:
        result=subprocess.run([str(cli),'query','--sql',sql,'--output','json'],capture_output=True,text=True)
        if sql==queries['total']:
            check(result.returncode==0 and '245.0000000000' in result.stdout,'CLI total failed')
        else:check(result.returncode!=0 and 'MODEL_NOT_FOUND' in result.stderr+result.stdout,'CLI strict config missing')
    raw=trino('SELECT order_id,dept_id,customer_id,paid_amount FROM mysql.sales.orders ORDER BY order_id')
    customers=trino('SELECT dept_id,customer_id,industry FROM crm.public.customers ORDER BY dept_id,customer_id')
    check(raw['rows']==[[1,'A',1,'100.00'],[2,'A',2,'80.00'],[3,'B',1,'50.00'],[4,'B',2,'60.00']], 'Source orders changed')
    check(customers['rows']==[['A',1,'软件'],['A',2,'零售'],['B',1,'软件'],['B',2,'零售']], 'Source CRM changed')
    report['source_baseline']={'orders':raw,'customers':customers}
    report['not_verified']=['MCP/Codex tool connection (W2)','semantic change release (W3)','department isolation (S5)',
        'generic strict-mode qualifier protection','vector indexing/recall','automatic Superset synchronization','production/scale capacity']
    e.close()
    (ROOT/'evidence/w1-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+'\n')
    print('W1 verified: 4 keyed orders, 245 total, 120/125 industry, cube, missing customer, 7 policy rejections; known limitations recorded')


if __name__=='__main__':main()

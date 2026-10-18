"""W3 synthetic semantic change experiment against real Trino and stdio MCP.

No source writes. Every candidate is an isolated Wren project compiled from YAML.
Publication is a local fixture rehearsal, not business approval or production CD.
"""
import asyncio
import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from wren import context
from verify_s4 import trino
from verify_w1 import engine, run_query
from verify_w2_mcp import payload
from w3_release import ROOT, STORE, activate, digest, publish, resolve, verify_bundle

FIXTURE = ROOT/'w3/fixtures/business.json'
DATA = json.loads(FIXTURE.read_text())
REVIEW = {'scope':'synthetic_fixture','reviewer':'regression_harness','decision':'approve'}
TOTAL = 'SELECT SUM(net_amount) AS amount FROM bi_orders'
INDUSTRY = 'SELECT industry,SUM(net_amount) AS amount FROM bi_orders GROUP BY industry'
DAILY = 'SELECT order_date,SUM(net_amount) AS amount FROM bi_orders GROUP BY order_date'


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+'\n')


def changed_data():
    data = copy.deepcopy(DATA)
    for name in ['orders','customers']:
        for row in data[name]:
            if row['dept_id']=='B':row['customer_id'] = data['change']['B_customer_id_mapping'][str(row['customer_id'])]
    return data


def literal(value, name):
    if name.endswith('amount'):return "CAST('"+value+"' AS DECIMAL(12,2))"
    if name=='order_date':return "DATE '"+value+"'"
    if isinstance(value,int):return str(value)
    return "'"+value.replace("'","''")+"'"


def source_sql(data, name):
    rows = data[name]
    columns = list(rows[0])
    values = ',\n'.join('('+','.join(literal(r[c],c) for c in columns)+')' for r in rows)
    return 'SELECT * FROM (VALUES '+values+') AS fixture('+','.join(columns)+')'


def oracle(data, policy):
    # Independent integer cents calculation; no tested SQL or MDL is reused.
    refund = {}
    for r in data['refunds']:
        key = (r['dept_id'],r['order_id'])
        refund[key] = refund.get(key,0)+int(Decimal(r['refund_amount'])*100)
    customers = {(r['dept_id'],r['customer_id']):r['industry'] for r in data['customers']}
    total = 0; industry = {}; daily = {}; count = 0
    for r in data['orders']:
        if r['status'] not in data['policies'][policy]:continue
        amount = int(Decimal(r['paid_amount'])*100)-refund.get((r['dept_id'],r['order_id']),0)
        label = customers.get((r['dept_id'],r['customer_id']),'未知行业')
        total += amount; count += 1
        industry[label] = industry.get(label,0)+amount
        daily[r['order_date']] = daily.get(r['order_date'],0)+amount
    money = lambda n:format(Decimal(n)/100,'.2f')
    return {'total':money(total),'industry':{k:money(v) for k,v in industry.items()},
            'daily':{k:money(v) for k,v in daily.items()},'orders':count}


def quality(data, composite):
    sql = source_sql(data,'customers')
    keys = 'dept_id,customer_id' if composite else 'customer_id'
    result = trino(f'SELECT COUNT(*) AS duplicate_keys FROM (SELECT {keys} FROM ({sql}) c GROUP BY {keys} HAVING COUNT(*)>1) d')
    return {'customer_key':keys,'duplicate_key_groups':result['rows'][0][0],
            'query_id':result['query_id'],'passed':result['rows']==[[0]]}


def schema(data):
    from trino.dbapi import connect
    connection = connect(host='127.0.0.1',port=18080,user='bi008_w3_schema')
    result = {}
    try:
        for name in ['orders','customers','refunds']:
            cur = connection.cursor();cur.execute('SELECT * FROM ('+source_sql(data,name)+') s LIMIT 0')
            result[name] = [(c[0],str(c[1])) for c in cur.description]
    finally:connection.close()
    return result


def build(work, name, data, composite, policy):
    project = work/name
    shutil.copytree(ROOT/'wren',project,ignore=shutil.ignore_patterns('target','requirements.*','knowledge'))
    model_path = project/'models/bi_orders/metadata.yml'
    model = yaml.safe_load(model_path.read_text());model.pop('table_reference')
    model['properties']['description'] = f'W3 fixture: one row per eligible order; net_revenue@{1 if policy=="v1" else 2}'
    model['columns'][2]['properties']['description'] = 'W3 customer identifier: '+('department-local' if composite else 'globally unique')
    next(c for c in model['columns'] if c['name']=='customer')['properties']['description'] = 'W3 customer relation uses '+('department and customer ID' if composite else 'global customer ID')
    model['columns'].insert(5,{'name':'status','type':'VARCHAR','properties':{'description':'fixture order state'}})
    model_path.write_text(yaml.safe_dump(model,allow_unicode=True,sort_keys=False))
    states = ','.join("'"+s+"'" for s in data['policies'][policy])
    (model_path.parent/'ref_sql.sql').write_text(f"SELECT * FROM ({source_sql(data,'orders')}) s WHERE status IN ({states})\n")
    customer = project/'models/bi_customers'
    model = yaml.safe_load((customer/'metadata.yml').read_text())
    model['primary_key'] = ['customer_dept_id','customer_local_id'] if composite else 'customer_local_id'
    next(c for c in model['columns'] if c['name']=='customer_local_id')['properties']['description'] = 'W3 customer identifier: '+('department-local' if composite else 'globally unique')
    model['properties']['description'] = 'W3 fixture: customer key '+('department + local ID' if composite else 'global ID')
    (customer/'metadata.yml').write_text(yaml.safe_dump(model,allow_unicode=True,sort_keys=False))
    (customer/'ref_sql.sql').write_text('SELECT dept_id AS customer_dept_id,customer_id AS customer_local_id,industry FROM ('+source_sql(data,'customers')+') c\n')
    (project/'models/bi_refund_totals/ref_sql.sql').write_text('SELECT dept_id AS refund_dept_id,order_id AS refund_order_id,SUM(refund_amount) AS refund_total FROM ('+source_sql(data,'refunds')+') r GROUP BY dept_id,order_id\n')
    relationships = yaml.safe_load((project/'relationships.yml').read_text())
    rel = next(r for r in relationships['relationships'] if r['name']=='orders_customers')
    rel['condition'] = ('bi_orders.dept_id = bi_customers.customer_dept_id AND ' if composite else '')+'bi_orders.customer_id = bi_customers.customer_local_id'
    (project/'relationships.yml').write_text(yaml.safe_dump(relationships,sort_keys=False))
    metric = 'net_revenue@'+('1' if policy=='v1' else '2')
    cube_path = project/'cubes/net_revenue/metadata.yml'
    cube = yaml.safe_load(cube_path.read_text())
    cube['properties']['description'] = f'{metric}; W3 synthetic fixture; eligible statuses: {states}; CNY; current industry; refunds assigned to order date'
    cube_path.write_text(yaml.safe_dump(cube,allow_unicode=True,sort_keys=False))
    configuration = yaml.safe_load((project/'wren_project.yml').read_text())
    configuration['name'] = 'bi008_w3_fixture';configuration['version'] = name
    (project/'wren_project.yml').write_text(yaml.safe_dump(configuration,sort_keys=False))
    (project/'AGENTS.md').write_text(f'# W3 fixture {name}\nUse only the supplied MCP. Read runtime contract and rules before planning/querying.\nMetric {metric}; statuses: {states}; synthetic data, no company approval.\n')
    rules = project/'knowledge/rules/general.md';rules.parent.mkdir(parents=True)
    rules.write_text(f'# W3 {name}\n指标：{metric}，受控别名净销售额；仅含订单状态 {states}。\n净额为实付减已记录退款，人民币元；退款回溯原订单日，行业为客户当前行业。\n客户键：'+('部门与客户 ID 的复合键。' if composite else '全局客户 ID。')+'\n只用模型名，不用物理表；先 dry_plan/dry_run 再查询；金额为精确字符串。\n这是独立合成实验，不是 S2 基准，不是企业发布；收入含糊时澄清。\n')
    example = project/'knowledge/sql/verified.md';example.parent.mkdir(parents=True)
    header = {'nl':f'{name} {metric} 按当前行业比较净销售额','sql':INDUSTRY,'source':'verified_fixture_'+policy,'datasource':'trino'}
    example.write_text('---\n'+yaml.safe_dump(header,allow_unicode=True,sort_keys=False)+'---\n'+metric+'; eligible statuses: '+states+'\n')
    save(project/'semantic-contract.json',{'metric':metric,'customer_key':['dept_id','customer_id'] if composite else ['customer_id'],
          'order_grain':'one row per eligible order','eligible_statuses':data['policies'][policy],
          'history':'current industry; refund belongs to order date','scope':'synthetic_fixture',
          'source_schema':schema(data),'source_snapshot_sha256':digest(json.dumps(data,sort_keys=True).encode())})
    env = dict(os.environ,WREN_HOME=str(project/'runtime'),WREN_PROJECT_HOME=str(project),WREN_MEMORY_BACKEND='grep')
    logs = []
    for args in [['context','validate','--strict'],['context','build'],['memory','index']]:
        result = subprocess.run([str(ROOT/'.local/wren-venv/bin/wren'),*args],env=env,capture_output=True,text=True,check=True)
        logs.append({'command':['wren',*args],'stdout':result.stdout,'stderr':result.stderr})
    return project, metric, logs


def regress(project, expected):
    manifest = json.loads((project/'target/mdl.json').read_text())
    with engine(manifest) as e:
        result = {key:run_query(e,sql) for key,sql in [('total',TOTAL),('industry',INDUSTRY),('daily',DAILY),('orders','SELECT order_id,industry,net_amount FROM bi_orders ORDER BY order_id')]}
    assert Decimal(result['total']['rows'][0]['amount'])==Decimal(expected['total'])
    assert {r['industry']:r['amount'] for r in result['industry']['rows']}=={k:Decimal(v) for k,v in expected['industry'].items()}
    assert {str(r['order_date']):r['amount'] for r in result['daily']['rows']}=={k:Decimal(v) for k,v in expected['daily'].items()}
    assert len(result['orders']['rows'])==expected['orders']
    assert [r['order_id'] for r in result['orders']['rows']]==([1,2,3,4] if expected['orders']==4 else [1,2,4])
    return result


async def mcp_check(expected_metric, expected_amount, transition=None):
    with (ROOT/'.local/w3-mcp-server.log').open('a') as log:
        parameters = StdioServerParameters(command=str(ROOT/'scripts/w3_mcp.sh'))
        async with stdio_client(parameters,errlog=log) as (read,write):
            async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=60)) as session:
                await session.initialize()
                async def call(name,args=None):
                    r = await session.call_tool(name,args or {})
                    assert not r.isError,payload(r)
                    return payload(r)
                contract = await call('get_runtime_contract')
                assert contract['metric']==expected_metric and contract['active_release_matches_session']
                rules = await call('get_instructions')
                assert expected_metric in rules['instructions']
                recall = await call('recall_queries',{'question':'净销售额'})
                assert recall['matches'] and expected_metric in str(recall)
                result = await call('run_sql',{'sql':TOTAL})
                assert Decimal(result['rows'][0]['amount'])==Decimal(expected_amount)
                cube = await call('query_cube',{'cube':'net_revenue','measures':['total'],'dimensions':['industry']})
                assert sum(Decimal(r['total']) for r in cube['rows'])==Decimal(expected_amount)
                evidence = {'contract':contract,'instructions':rules,'recall':recall,'total':result,'cube':cube}
                if transition:
                    transition()
                    after = await call('get_runtime_contract')
                    assert not after['active_release_matches_session']
                    refused = await session.call_tool('run_sql',{'sql':TOTAL})
                    assert refused.isError and 'BI008_RELEASE_REVISION' in str(payload(refused))
                    old_rules = await call('get_instructions')
                    assert old_rules==rules
                    evidence['old_session_after_publication'] = {'contract':after,'refusal':payload(refused),'old_context_retained':True}
                return evidence


def impact(project):
    mdl = json.loads((project/'target/mdl.json').read_text())
    relationship = next(r for r in mdl['relationships'] if r['name']=='orders_customers')
    direct = []
    models = set()
    for model in mdl['models']:
        for column in model['columns']:
            if column.get('relationship')=='orders_customers':
                direct.append(model['name']+'.'+column['name']);models.add(model['name'])
    cubes = [c['name'] for c in mdl['cubes'] if c['baseObject'] in models]
    return {'derived_from_compiled_mdl':{'relationship':relationship,'direct_model_fields':direct,
            'affected_models_conservative':sorted(models),'affected_cubes_conservative':cubes},
            'manually_registered_consumers':['knowledge/rules/general.md','knowledge/sql/verified.md','Codex MCP session'],
            'superset':{'dataset_id':2,'chart_ids':[3],'actual_fixture_dependency':False,
                        'note':'S4 dataset uses independent SQL. Only if promoting the equivalent real rule must this manually mapped consumer be reviewed; no automatic sync performed.'}}


async def main():
    run_id = datetime.now(timezone.utc).strftime('w3-%Y%m%dT%H%M%S')
    work = STORE/'candidates'/run_id;work.mkdir(parents=True)
    report = {'verified_at_utc':datetime.now(timezone.utc).isoformat(),'scope':'synthetic fixture, no S2 writes','run_id':run_id,'builds':{}}
    before = DATA;after = changed_data()
    for policy in ['v1','v2']:assert oracle(after,policy)==DATA['independent_expected'][policy]
    assert schema(before)==schema(after)
    report['source_schema_unchanged_by_key_drift'] = True
    old,metric,logs = build(work,'initial',before,False,'v1');report['builds']['initial']=logs
    expected = oracle(before,'v1')
    report['initial_regression']=regress(old,expected)
    report['initial_quality']=quality(before,False);assert report['initial_quality']['passed']
    r0 = publish(old,run_id+'-v1',metric,{'quality':True,'regression':True,'context':True},REVIEW)
    pointer_before = (STORE/'current.json').read_bytes()
    broken,_,logs = build(work,'broken-relationship',after,False,'v1');report['builds']['broken']=logs
    failed = quality(after,False);assert not failed['passed']
    with engine(json.loads((broken/'target/mdl.json').read_text())) as e:
        wrong_total = run_query(e,TOTAL)
        wrong_rows = run_query(e,'SELECT order_id,industry,net_amount FROM bi_orders ORDER BY order_id')
        wrong_industry = run_query(e,INDUSTRY)
    assert wrong_total['rows'][0]['amount']==Decimal('245') and len(wrong_rows['rows'])==8
    assert sum(r['amount'] for r in wrong_industry['rows'])==Decimal('490')
    try:publish(broken,run_id+'-must-not-publish','net_revenue@1',{'quality':False,'regression':False},REVIEW)
    except ValueError as error:refusal=str(error)
    else:raise AssertionError('Broken relationship published')
    assert pointer_before==(STORE/'current.json').read_bytes()
    report['relationship_drift']={'quality':failed,'misleading_correct_total':wrong_total,'wrong_industry':wrong_industry,'wrong_details':wrong_rows,
                                  'publication_refused':refusal,'previous_pointer_retained':True}
    fixed,metric,logs = build(work,'relationship-v2',after,True,'v1');report['builds']['relationship-v2']=logs
    report['repaired_quality']=quality(after,True);assert report['repaired_quality']['passed']
    report['relationship_regression']=regress(fixed,expected)
    report['impact']=impact(fixed)
    release_ids = {'initial':r0.name}
    def publish_fixed():
        release_ids['relationship-v2']=publish(fixed,run_id+'-relationship-v2',metric,{'quality':True,'regression':True,'context':True},REVIEW).name
    report['mcp_initial_to_relationship']=await mcp_check('net_revenue@1','245',publish_fixed)
    report['mcp_relationship']=await mcp_check('net_revenue@1','245')
    policy_project,metric,logs = build(work,'metric-v2',after,True,'v2');report['builds']['metric-v2']=logs
    expected_v2 = oracle(after,'v2');report['metric_regression']=regress(policy_project,expected_v2)
    # Schema/data snapshot are identical between relationship-v2 and metric-v2.
    a = json.loads((fixed/'semantic-contract.json').read_text());b = json.loads((policy_project/'semantic-contract.json').read_text())
    assert a['source_schema']==b['source_schema'] and a['source_snapshot_sha256']==b['source_snapshot_sha256']
    report['metric_drift']={'schema_unchanged':True,'data_unchanged':True,'old_statuses':a['eligible_statuses'],
                           'new_statuses':b['eligible_statuses'],'old_metric':'net_revenue@1','new_metric':metric,
                           'independent_expected':expected_v2,'business_decision_source':'explicit fixture policy; not inferred from schema'}
    # Reproduce mixed publication without mutating a real release.
    mixed = work/'mixed-context';shutil.copytree(policy_project,mixed)
    shutil.copyfile(fixed/'knowledge/rules/general.md',mixed/'knowledge/rules/general.md')
    mismatched = 'net_revenue@2' not in (mixed/'knowledge/rules/general.md').read_text()
    assert mismatched
    try:publish(mixed,run_id+'-mixed',metric,{'quality':True,'regression':True,'context':not mismatched},REVIEW)
    except ValueError as error:report['mixed_context_publication']={'refused':str(error),'detected_before_activation':True}
    else:raise AssertionError('Mixed context published')
    def publish_metric():
        release_ids['metric-v2']=publish(policy_project,run_id+'-metric-v2',metric,{'quality':True,'regression':True,'context':True},REVIEW).name
    report['mcp_relationship_to_metric']=await mcp_check('net_revenue@1','245',publish_metric)
    report['mcp_metric']=await mcp_check('net_revenue@2','195')
    # A post-build edit is detected by integrity, even if someone bypasses context gates.
    tampered = work/'tampered';shutil.copytree(resolve()[0],tampered)
    with (tampered/'knowledge/rules/general.md').open('a') as handle:handle.write('\nUnreviewed change\n')
    try:verify_bundle(tampered)
    except ValueError as error:report['tampered_bundle']=str(error)
    else:raise AssertionError('Tampered release accepted')
    activate(release_ids['relationship-v2'])
    report['rollback']=await mcp_check('net_revenue@1','245')
    activate(release_ids['metric-v2'])
    report['recovery']=await mcp_check('net_revenue@2','195')
    report['release_ids']=release_ids
    report['active_release']=resolve()[1]
    report['not_verified']=['real company semantics or business approval','automatic schema/quality polling','production atomic rollout',
        'vector index publication (grep index is no-op)','automatic Superset sync','schema-breaking database rollback','department isolation (deferred)']
    # Reviewable source/compiled snapshots exclude local connection/profile files.
    for label,release_id in release_ids.items():
        source=STORE/'releases'/release_id
        destination=ROOT/'evidence/w3-models'/label
        if destination.exists():shutil.rmtree(destination)
        shutil.copytree(source,destination,ignore=shutil.ignore_patterns('runtime','release.json','.gitignore'))
        # Repository-wide target/ is ignored; expose the verified compiled review copy.
        (destination/'compiled-mdl.json').write_bytes((source/'target/mdl.json').read_bytes())
        shutil.rmtree(destination/'target')
    save(ROOT/'evidence/w3-verification.json',report)
    print('W3 passed: hidden key drift: total stays 245 but industry adds to 490, detected, fixed 245; unchanged-schema policy v2 195; stale sessions/mixed contexts refused; rollback 245 and recovery 195.')


if __name__=='__main__':asyncio.run(main())

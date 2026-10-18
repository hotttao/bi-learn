"""Operator setup for S8: separate PostgreSQL schemas, deterministic cents ledger,
and independent Wren projects. Never writes the four-order baseline or main MDL.
"""
from collections import defaultdict
from datetime import date,timedelta,datetime,timezone
import json
from pathlib import Path
import shutil
import sys
import time

import yaml
from wren import context
from source_db import ROOT,query
from prepare_s7 import save,digest

LOCAL=ROOT/'.local/s8';PLAN=json.loads((ROOT/'s8/plan.json').read_text());SEED=PLAN['seed']
INDUSTRIES=['软件','零售','制造','服务']


def expected(n):
    total=0;industry=defaultdict(int);daily=defaultdict(int)
    for i in range(1,n+1):
        customer=i%10000+1
        paid=1000+(i*37+SEED)%99001
        refund=(150+i%100) if i%4==0 else 0
        net=paid-refund;day=date(2026,1,1)+timedelta(days=(i+SEED)%365)
        total+=net;industry[INDUSTRIES[customer%4]]+=net
        if day.month==10:daily[day.isoformat()]+=net
    money=lambda x:format(__import__('decimal').Decimal(x)/100,'.2f')
    return {'order_count':n,'total':money(total),'industry':{k:money(v) for k,v in sorted(industry.items())},'october_daily':{k:money(v) for k,v in sorted(daily.items())}}


def scale(n):
    schema='bi008_s8_'+str(n)
    exists=query('crm',f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='{schema}' AND table_name='orders'",'admin')
    if exists=='1':
        assert query('crm',f'SELECT COUNT(*) FROM {schema}.orders','admin')==str(n),'Existing S8 data differs; inspect instead of overwriting'
        return schema
    query('crm',f"""
BEGIN;
CREATE SCHEMA {schema};
CREATE TABLE {schema}.customers(dept_id varchar(1),customer_id bigint,industry varchar(30),PRIMARY KEY(dept_id,customer_id));
INSERT INTO {schema}.customers
SELECT d,i,(ARRAY['软件','零售','制造','服务'])[i%4+1] FROM generate_series(1,10000) i CROSS JOIN (VALUES('A'),('B')) a(d);
CREATE TABLE {schema}.orders(order_id bigint PRIMARY KEY,dept_id varchar(1),customer_id bigint,order_date date,paid_amount numeric(12,2));
INSERT INTO {schema}.orders
SELECT i,CASE WHEN i%10<8 THEN 'A' ELSE 'B' END,i%10000+1,
DATE '2026-01-01'+((i+{SEED})%365)::int,(1000+(i*37+{SEED})%99001)::numeric/100
FROM generate_series(1,{n}) i;
CREATE INDEX ON {schema}.orders(order_date);
CREATE TABLE {schema}.refunds(refund_id bigint PRIMARY KEY,dept_id varchar(1),order_id bigint,refund_amount numeric(12,2));
INSERT INTO {schema}.refunds
SELECT i*2+j,CASE WHEN i%10<8 THEN 'A' ELSE 'B' END,i,CASE WHEN j=0 THEN (100+i%100)::numeric/100 ELSE 0.50 END
FROM generate_series(1,{n}) i CROSS JOIN generate_series(0,1) j WHERE i%4=0;
CREATE INDEX ON {schema}.refunds(dept_id,order_id);
GRANT USAGE ON SCHEMA {schema} TO crm_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO crm_reader;
COMMIT;
ANALYZE {schema}.orders;ANALYZE {schema}.refunds;ANALYZE {schema}.customers;
""",'admin')
    return schema


def build_scale_project(n,schema):
    project=LOCAL/'projects'/str(n)
    if not project.exists():
        shutil.copytree(ROOT/'wren',project,ignore=shutil.ignore_patterns('runtime','.wren','target','requirements.*','AGENTS.md'))
        p=project/'models/bi_orders/metadata.yml';d=yaml.safe_load(p.read_text());d['table_reference'].update(catalog='crm',schema=schema);p.write_text(yaml.safe_dump(d,allow_unicode=True,sort_keys=False))
        for name in ['bi_refund_totals','bi_customers']:
            p=project/f'models/{name}/ref_sql.sql';s=p.read_text().replace('mysql.sales.refunds',f'crm.{schema}.refunds').replace('crm.public.customers',f'crm.{schema}.customers');p.write_text(s)
        (project/'target').mkdir();save(project/'target/mdl.json',context.build_json(project))
    return project


def relations():
    schema='bi008_s8_relations'
    if query('crm',f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='{schema}' AND table_name='orders'",'admin')=='1':return
    query('crm',f"""
BEGIN;CREATE SCHEMA {schema};
CREATE TABLE {schema}.orders(order_id bigint PRIMARY KEY,dept_id varchar(1),customer_id bigint,order_date date,paid_amount numeric(12,2));
INSERT INTO {schema}.orders VALUES(101,'A',1,'2026-03-01',200),(102,'A',2,'2026-03-02',150),(103,'B',1,'2026-03-01',300),(104,'B',2,'2026-03-02',120),(105,'A',3,'2026-03-02',80),(106,'A',1,'2026-08-01',100);
CREATE TABLE {schema}.refunds(refund_id bigint PRIMARY KEY,dept_id varchar(1),order_id bigint,refund_amount numeric(12,2));
INSERT INTO {schema}.refunds VALUES(1,'A',101,20),(2,'A',101,30),(3,'A',102,10),(4,'B',104,20),(5,'A',106,5);
CREATE TABLE {schema}.customers(dept_id varchar(1),customer_id bigint,industry varchar(30),PRIMARY KEY(dept_id,customer_id));
INSERT INTO {schema}.customers VALUES('A',1,'零售'),('A',2,'制造'),('B',1,'软件'),('B',2,'服务');
CREATE TABLE {schema}.industry_history(dept_id varchar(1),customer_id bigint,valid_from date,valid_to date,industry varchar(30),PRIMARY KEY(dept_id,customer_id,valid_from),CHECK(valid_from<valid_to));
INSERT INTO {schema}.industry_history VALUES('A',1,'2026-01-01','2026-07-01','软件'),('A',1,'2026-07-01','2027-01-01','零售'),('A',2,'2026-01-01','2027-01-01','制造'),('B',1,'2026-01-01','2027-01-01','软件'),('B',2,'2026-01-01','2027-01-01','服务');
CREATE TABLE {schema}.customer_tags(dept_id varchar(1),customer_id bigint,tag varchar(30),PRIMARY KEY(dept_id,customer_id,tag));
INSERT INTO {schema}.customer_tags VALUES('A',1,'VIP'),('A',1,'战略'),('A',2,'VIP'),('B',1,'战略'),('B',2,'普通');
CREATE TABLE {schema}.order_lines(line_id bigint PRIMARY KEY,dept_id varchar(1),order_id bigint,line_amount numeric(12,2));
INSERT INTO {schema}.order_lines SELECT order_id*2+j,dept_id,order_id,paid_amount/2 FROM {schema}.orders CROSS JOIN generate_series(0,1) j;
CREATE TABLE {schema}.revenue_entries(entry_id bigint PRIMARY KEY,order_id bigint,recognized_amount numeric(12,2));
INSERT INTO {schema}.revenue_entries VALUES(1,101,5555);
CREATE TABLE {schema}.catalog_stub(id bigint,customer_id bigint,order_id bigint,status varchar(30),amount numeric(12,2));
GRANT USAGE ON SCHEMA {schema} TO crm_reader;GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO crm_reader;
COMMIT;
""",'admin')


RAW={
 'sales_orders':('orders',{'order_id':'BIGINT','dept_id':'VARCHAR','customer_id':'BIGINT','order_date':'DATE','paid_amount':'DECIMAL'}),
 'sales_refunds':('refunds',{'refund_id':'BIGINT','dept_id':'VARCHAR','order_id':'BIGINT','refund_amount':'DECIMAL'}),
 'crm_customers':('customers',{'dept_id':'VARCHAR','customer_id':'BIGINT','industry':'VARCHAR'}),
 'crm_industry_history':('industry_history',{'dept_id':'VARCHAR','customer_id':'BIGINT','valid_from':'DATE','valid_to':'DATE','industry':'VARCHAR'}),
 'crm_customer_tags':('customer_tags',{'dept_id':'VARCHAR','customer_id':'BIGINT','tag':'VARCHAR'}),
 'sales_order_lines':('order_lines',{'line_id':'BIGINT','dept_id':'VARCHAR','order_id':'BIGINT','line_amount':'DECIMAL'}),
 'finance_revenue_entries':('revenue_entries',{'entry_id':'BIGINT','order_id':'BIGINT','recognized_amount':'DECIMAL'})}
RULES=(ROOT/'s8/rules.md').read_text()


def catalogs():
    output=[]
    for count in PLAN['catalog_model_counts']:
      for condition in ['schema_only','schema_and_rules']:
        p=LOCAL/'catalogs'/str(count)/condition;p.mkdir(parents=True,exist_ok=True)
        save_yaml=lambda f,v:f.write_text(yaml.safe_dump(v,allow_unicode=True,sort_keys=False))
        save_yaml(p/'wren_project.yml',{'schema_version':5,'name':'s8_catalog','version':'1.0','catalog':'wren','schema':'public','data_source':'trino','profile':'bi008'})
        definitions=dict(RAW)
        for i in range(count-len(RAW)):
            name=f"{['marketing','support','finance','inventory','archive'][i%5]}_customer_orders_{i:03}"
            definitions[name]=('catalog_stub',{'id':'BIGINT','customer_id':'BIGINT','order_id':'BIGINT','status':'VARCHAR','amount':'DECIMAL'})
        for name,(table,cols) in definitions.items():
            d=p/'models'/name;d.mkdir(parents=True,exist_ok=True)
            save_yaml(d/'metadata.yml',{'name':name,'table_reference':{'catalog':'crm','schema':'bi008_s8_relations','table':table},'columns':[{'name':k,'type':v} for k,v in cols.items()]})
        rules=p/'knowledge/rules';rules.mkdir(parents=True,exist_ok=True)
        (rules/'general.md').write_text(RULES if condition=='schema_and_rules' else '# Schema only\n本项目仅提供表、字段和类型，没有审核过的业务规则。\n')
        t=time.perf_counter();mdl=context.build_json(p);(p/'target').mkdir(exist_ok=True);save(p/'target/mdl.json',mdl)
        output.append({'count':count,'condition':condition,'build_ms':round((time.perf_counter()-t)*1000,2),'mdl_bytes':len(json.dumps(mdl,ensure_ascii=False).encode()),
                       'project':str(p.relative_to(ROOT)),'note':'7 real business tables plus synthetic catalog noise aliases sharing an empty stub; not 500 physical source tables'})
    return output


def main():
    LOCAL.mkdir(parents=True,exist_ok=True)
    golden={str(n):expected(n) for n in PLAN['order_counts']}
    # The answers are computed from integers before DB seed/query, not from tested SQL.
    save(ROOT/'s8/expected-scale.json',golden)
    start=time.monotonic();schemas=[]
    for n in PLAN['order_counts']:
        schema=scale(n);build_scale_project(n,schema);schemas.append(schema);print('Prepared scale',n,flush=True)
    relations();catalog=catalogs()
    expected_rel={'total':'865.00','current_industry':{'制造':'140.00','未知行业':'80.00','服务':'100.00','软件':'300.00','零售':'245.00'},
                  'historical_industry':{'制造':'140.00','未知行业':'80.00','服务':'100.00','软件':'450.00','零售':'95.00'},
                  'overlapping_tags':{'VIP':'385.00','战略':'545.00','普通':'100.00','无标签':'80.00'},'tag_group_sum':'1110.00'}
    save(ROOT/'s8/expected-relations.json',expected_rel)
    save(ROOT/'evidence/s8-preparation.json',{'verified_at_utc':datetime.now(timezone.utc).isoformat(),'schemas':schemas+['bi008_s8_relations'],'catalogs':catalog,'elapsed_seconds':round(time.monotonic()-start,2),'baseline_touched':False,'seed':SEED})
    print('S8 setup complete',flush=True)


if __name__=='__main__':main()

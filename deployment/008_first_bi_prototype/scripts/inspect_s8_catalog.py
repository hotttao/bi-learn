"""Measure actual MCP context growth and demonstrate incorrect relationship paths."""
import asyncio
from datetime import datetime,timezone,timedelta
from decimal import Decimal
import json
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from prepare_s7 import ROOT,save
from verify_w1 import engine
from verify_w2_mcp import payload

NET="""WITH r AS (SELECT dept_id,order_id,SUM(refund_amount) AS refund FROM sales_refunds GROUP BY dept_id,order_id),
n AS (SELECT o.order_id,o.dept_id,o.customer_id,o.order_date,o.paid_amount-COALESCE(r.refund,0) AS net_amount FROM sales_orders o LEFT JOIN r ON o.dept_id=r.dept_id AND o.order_id=r.order_id)
"""
SQL={
'current_industry':NET+"SELECT COALESCE(c.industry,'未知行业') AS label,SUM(n.net_amount) AS amount FROM n LEFT JOIN crm_customers c ON n.dept_id=c.dept_id AND n.customer_id=c.customer_id GROUP BY COALESCE(c.industry,'未知行业')",
'historical_industry':NET+"SELECT COALESCE(h.industry,'未知行业') AS label,SUM(n.net_amount) AS amount FROM n LEFT JOIN crm_industry_history h ON n.dept_id=h.dept_id AND n.customer_id=h.customer_id AND n.order_date>=h.valid_from AND n.order_date<h.valid_to GROUP BY COALESCE(h.industry,'未知行业')",
'overlapping_tags':NET+"SELECT COALESCE(t.tag,'无标签') AS label,SUM(n.net_amount) AS amount FROM n LEFT JOIN crm_customer_tags t ON n.dept_id=t.dept_id AND n.customer_id=t.customer_id GROUP BY COALESCE(t.tag,'无标签')"}


async def main():
    report={'verified_at_utc':datetime.now(timezone.utc).isoformat(),'contexts':[],'relationship_controls':{}}
    for count in [10,100,500]:
     for condition in ['schema_only','schema_and_rules']:
      project=ROOT/f'.local/s8/catalogs/{count}/{condition}'
      with (ROOT/f'.local/s8-context-{count}-{condition}.log').open('w') as log:
       params=StdioServerParameters(command=str(ROOT/'scripts/s8_wren_mcp.sh'),args=[str(project)])
       async with stdio_client(params,errlog=log) as (read,write):
        async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=120)) as session:
         await session.initialize()
         result=payload(await session.call_tool('get_context',{'question':'净销售额 当前行业 下单时行业 标签'}))
         assert result['strategy']=='full',result
         instructions=payload(await session.call_tool('get_instructions',{}))
         report['contexts'].append({'models':count,'condition':condition,'strategy':result['strategy'],
            'schema_utf8_bytes':len(result['schema'].encode()),'schema_characters':len(result['schema']),
            'instructions_utf8_bytes':len(instructions['instructions'].encode()),'selected_domain_subset':False})
    golden=json.loads((ROOT/'s8/expected-relations.json').read_text())
    mdl=json.loads((ROOT/'.local/s8/catalogs/500/schema_and_rules/target/mdl.json').read_text());e=engine(mdl)
    try:
        for kind,sql in SQL.items():
            rows=e.query(sql).to_pylist();actual={r['label']:format(Decimal(r['amount']),'.2f') for r in rows};assert actual==golden[kind],(kind,actual)
            report['relationship_controls'][kind]={'sql':sql,'amounts':actual,'correct':True}
        bad={
         'refund_fanout':"SELECT SUM(o.paid_amount-COALESCE(r.refund_amount,0)) AS amount FROM sales_orders o LEFT JOIN sales_refunds r ON o.dept_id=r.dept_id AND o.order_id=r.order_id",
         'customer_id_only':NET+"SELECT SUM(n.net_amount) AS amount FROM n LEFT JOIN crm_customers c ON n.customer_id=c.customer_id",
         'tag_sum_as_total':NET+"SELECT SUM(n.net_amount) AS amount FROM n LEFT JOIN crm_customer_tags t ON n.dept_id=t.dept_id AND n.customer_id=t.customer_id"}
        for kind,sql in bad.items():
            rows=e.query(sql).to_pylist();amount=format(Decimal(rows[0]['amount']),'.2f');assert amount!=golden['total']
            report['relationship_controls'][kind]={'sql':sql,'observed_wrong_total':amount,'correct_total':golden['total'],'caught_by_golden':True}
        overlaps=e.query("SELECT COUNT(*) AS overlaps FROM crm_industry_history a JOIN crm_industry_history b ON a.dept_id=b.dept_id AND a.customer_id=b.customer_id AND a.valid_from<b.valid_from AND a.valid_to>b.valid_from").to_pylist()
        assert overlaps[0]['overlaps']==0;report['history_overlap_check']=overlaps
    finally:e.close()
    report['scope']='Synthetic model catalog context stress; no vector retrieval. Relations are read models, not inferred transaction aggregates.'
    save(ROOT/'evidence/s8-catalog.json',report);print('Actual MCP context growth and relationship controls verified')


if __name__=='__main__':asyncio.run(main())

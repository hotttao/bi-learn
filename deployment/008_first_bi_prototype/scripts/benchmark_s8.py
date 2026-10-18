"""Measure semantic execution on three isolated cardinalities; exact answers first."""
from datetime import datetime,timezone
from decimal import Decimal
import json
import statistics
import time

import requests
from prepare_s7 import ROOT,save
from verify_w1 import engine

PLAN=json.loads((ROOT/'s8/plan.json').read_text());GOLD=json.loads((ROOT/'s8/expected-scale.json').read_text())
QUERIES={'total':'SELECT COUNT(*) AS order_count,SUM(net_amount) AS net_revenue FROM bi_orders',
         'current_industry':'SELECT industry,SUM(net_amount) AS net_revenue FROM bi_orders GROUP BY industry ORDER BY industry',
         'october_daily':"SELECT order_date,SUM(net_amount) AS net_revenue FROM bi_orders WHERE order_date >= DATE '2026-10-01' AND order_date < DATE '2026-11-01' GROUP BY order_date ORDER BY order_date"}


def money(v):return format(Decimal(str(v)),'.2f')


def check(rows,kind,golden):
    if kind=='total':assert len(rows)==1 and rows[0]['order_count']==golden['order_count'] and money(rows[0]['net_revenue'])==golden['total'],rows
    else:
        key='industry' if kind=='current_industry' else 'order_date'
        result={str(r[key]):money(r['net_revenue']) for r in rows}
        assert result==golden['industry' if kind=='current_industry' else kind],result


def query_stats(sql):
    s=requests.Session();s.trust_env=False
    r=s.post('http://127.0.0.1:18080/v1/statement',data=sql.encode(),headers={'X-Trino-User':'bi008-s8-benchmark'},timeout=30);r.raise_for_status();d=r.json();qid=d['id']
    while True:
        assert 'error' not in d,d.get('error')
        if 'nextUri' not in d:break
        r=s.get(d['nextUri'],timeout=90);r.raise_for_status();d=r.json()
    report={'query_id':qid,'statement_stats':d.get('stats',{})}
    r=s.get('http://127.0.0.1:18080/v1/query/'+qid,timeout=15)
    if r.ok:
        stats=r.json().get('queryStats',{});report['query_stats']={k:stats[k] for k in ['elapsedTime','executionTime','totalCpuTime','processedInputPositions','processedInputDataSize','physicalInputDataSize','peakUserMemoryReservation','peakTotalMemoryReservation'] if k in stats}
    else:report['query_stats_unavailable']=r.status_code
    return report


def main():
    report={'verified_at_utc':datetime.now(timezone.utc).isoformat(),'concurrency':1,'results':[],
       'caches':'not evicted; first observed run separate; subsequent 5-run median/range, no reliable P95 claim',
       'latency_scope':'Wren Python SDK plan+execute+fetch, no Codex or chart rendering',
       'source_layout':'isolated PostgreSQL schemas on same host through existing Trino, not a cross-DB production benchmark'}
    for n in PLAN['order_counts']:
        mdl=json.loads((ROOT/f'.local/s8/projects/{n}/target/mdl.json').read_text());e=engine(mdl)
        try:
          for kind,sql in QUERIES.items():
            plan=e.dry_plan(sql);times=[]
            for i in range(PLAN['query_repeats_after_first']+1):
                t=time.perf_counter();rows=e.query(sql).to_pylist();duration=(time.perf_counter()-t)*1000;check(rows,kind,GOLD[str(n)]);times.append(round(duration,2))
            median=round(statistics.median(times[1:]),2)
            item={'orders':n,'query':kind,'sql':sql,'expanded_sql':plan,'first_ms':times[0],'subsequent_ms':times[1:],'median_ms':median,
                  'min_ms':min(times[1:]),'max_ms':max(times[1:]),'correct':True,'exploratory_latency_goal_ms':PLAN['exploratory_latency_goal_ms'],
                  'latency_goal_met':median<=PLAN['exploratory_latency_goal_ms'],'rows':[{k:(format(v,'f') if isinstance(v,Decimal) else str(v) if hasattr(v,'isoformat') else v) for k,v in row.items()} for row in rows],
                  'separate_plan_execution_stats':query_stats(plan)}
            report['results'].append(item);save(ROOT/'evidence/s8-benchmark.json',report)
            print(n,kind,'median',median,'ms; correct; goal',item['latency_goal_met'],flush=True)
        finally:e.close()
    report['all_correct']=all(x['correct'] for x in report['results']);report['all_latency_goals_met']=all(x['latency_goal_met'] for x in report['results']);save(ROOT/'evidence/s8-benchmark.json',report)


if __name__=='__main__':main()

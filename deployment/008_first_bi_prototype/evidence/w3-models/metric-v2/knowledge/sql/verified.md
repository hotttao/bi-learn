---
nl: metric-v2 net_revenue@2 按当前行业比较净销售额
sql: SELECT industry,SUM(net_amount) AS amount FROM bi_orders GROUP BY industry
source: verified_fixture_v2
datasource: trino
---
net_revenue@2; eligible statuses: 'paid'

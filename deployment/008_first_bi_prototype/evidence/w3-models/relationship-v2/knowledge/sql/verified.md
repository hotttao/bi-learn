---
nl: relationship-v2 net_revenue@1 按当前行业比较净销售额
sql: SELECT industry,SUM(net_amount) AS amount FROM bi_orders GROUP BY industry
source: verified_fixture_v1
datasource: trino
---
net_revenue@1; eligible statuses: 'paid','settled'

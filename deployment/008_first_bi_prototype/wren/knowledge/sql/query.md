---
nl: 全量合成数据按当前行业比较净收入
sql: SELECT industry,SUM(net_amount) AS net_revenue FROM bi_orders GROUP BY industry
  ORDER BY industry
source: verified_experiment
datasource: trino
---

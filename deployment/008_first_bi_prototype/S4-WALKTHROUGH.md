# S4：亲手理解跨库关系与语义维护

## 1. 先认清数据路径

浏览器 → Superset → Trino → MySQL 订单/退款＋PostgreSQL 客户。Trino 的 `mysql`、`crm` 是 catalog 名；`sales`、`public` 是各自 schema。它让 SQL 同时读两源，不会自动知道正确业务关系，也不把三表永久复制成一张表。

原 S3 两图继续直连 MySQL，作为独立基准；新增行业图走 Trino。三图共享订单日期筛选，用同一口径对账。

Trino 固定 481、Python 驱动固定 0.337.0。Trino HTTP 只发布宿主 `127.0.0.1:18080`；Superset 容器通过 `trino:8080` 访问。S4 HTTP 用户名只是标签，不是认证，不得作为部门隔离依据。底层两个连接使用 S2 只读账号。

## 2. 在 SQL Lab 看正确结果

打开 Superset → SQL → SQL Lab，选择 `008 Trino federation (source readers)`。先执行：

```sql
SHOW CATALOGS;
SELECT * FROM mysql.sales.orders ORDER BY order_id;
SELECT * FROM crm.public.customers ORDER BY dept_id, customer_id;
```

也可在宿主用只读查询入口，不需要 sudo：

```bash
python3 scripts/verify_s4.py --query < sql/trino/orders_enriched_v1.sql
```

再复制 [orders_enriched_v1.sql](sql/trino/orders_enriched_v1.sql)，检查恰好 4 行：净收入为 70、75、50、50，软件两单、零售两单。不要先看总额；行粒度错误时总额正确也可能只是巧合。

## 3. 故意漏掉退款聚合

```sql
SELECT SUM(o.paid_amount - COALESCE(r.refund_amount, DECIMAL '0.00')) AS wrong_net
FROM mysql.sales.orders o
LEFT JOIN mysql.sales.refunds r
  ON o.dept_id = r.dept_id AND o.order_id = r.order_id;
```

错误结果应为 345。订单 1 有两笔退款，实付 100 被算了两次。改成先按部门、订单汇总退款，才能恢复 245。`DISTINCT` 不是通用修复方法。

## 4. 故意漏掉部门关联

在正确 SQL 最后一行中，只保留 `o.customer_id = c.customer_id`，去掉 `o.dept_id = c.dept_id AND`。应得到 8 行，求和 490。A/1 和 B/1 虽然客户数字相同，却不是同一客户。数据库 DDL 不足以表达跨系统的全部业务语义。

## 5. 看当前行业如何影响历史订单

管理员实验脚本会把 A/1 从软件改为零售，查询后在 `finally` 中恢复。改变期间软件变成 50、零售变成 195，合计仍为 245。

这不是净收入公式变化，而是客户属性值变化。如果业务要保留“下单时行业”，需要行业历史的生效/失效时间以及不重叠约束，且采用订单时点关联；给当前行业字段改名不能补回历史。

需要完整复现时，在本目录执行：

```bash
sudo python3 scripts/verify_s4.py
```

脚本要求当前基准符合预期，否则先报错。完整过程含短暂 CRM 编辑和恢复，请不要与另一个写入实验同时运行。异常中断后先核查 A/1 行业，不要直接覆盖有意修改的业务值。

## 6. 对照看板的三张图

打开原看板，观察净收入 245、日趋势 120/125、行业软件 120/零售 125。把订单日期结束设为 `2026-10-02` 并 Apply / Apply filters；三图应缩小为 120，趋势只剩第一天，行业只剩软件。恢复结束日 `2026-11-01`，离开再重开。

查看新增数据集 `008_orders_enriched_v1`：保存的是跨库 SQL 与指标定义；行业图保存的是分组和展示配置。保存看板不会冻结源数据。

## 7. 关系增加以后怎么维护

本步 [net_revenue.v1.json](semantic/net_revenue.v1.json) 是内部契约，记录指标、粒度、复合键、基数、缺失处理、时间与质量约束；它不是 Wren MDL，Codex 也不会凭文件存在就自动加载。

目前的维护链路是：源 schema/数据变化 → 执行质量及指标回归 → 核对契约与 SQL → 更新实际 BI 数据集 → 读回与页面对账 → review。脚本拒绝发布重复客户键；缺失客户通过查询内 fixture 保留“未知行业”。没有后台监控或自动同步服务。

表快速增加时，先按业务域维护实体、负责人、允许关联路径与指标，基于字段变更和血缘选受影响的回归；数据量快速增加时，另看扫描量、索引、汇总和查询代价。二者不能用增加提示词长度解决。这个规模暂不需要引入 OWL/图数据库；W1～W3 将继续检验 Wren 能把多少手工契约、查询与变更维护变成产品能力。

亲手练习：解释软件 120 是哪两笔订单；说明 A/1 行业变化为什么不改变总额；指出如果只给 Agent 三张表的字段名，还缺哪三条关系规则。

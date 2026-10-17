# S2 数据字典与动手说明

这三张业务表使用合成数据，与 Superset 元数据库完全分开。固定业务窗口为 2026 年 10 月，金额为人民币元，不代表真实财务收入确认。

## 1. 一行到底表示什么

| 位置 | 表 | 一行的含义 | 唯一标识 |
| --- | --- | --- | --- |
| MySQL `sales` | `orders` | 一个已实付订单 | 全局 `order_id` |
| MySQL `sales` | `refunds` | 一笔已记录退款 | 全局 `refund_id` |
| PostgreSQL `crm.public` | `customers` | 一个部门内的客户 | `(dept_id, customer_id)` |

**表中的行不代表相同的业务粒度。** 订单 1 有两笔退款，直接关联后会有两行；如果在这两行上合计实付，100 元会被算两次。正确 SQL 见 [sql/mysql/order_net.sql](sql/mysql/order_net.sql)：先汇总退款，再关联订单。

## 2. 字段说明

| 表 | 字段 | 类型 | 业务含义与规则 |
| --- | --- | --- | --- |
| orders | order_id | BIGINT | 主键，全局唯一 |
| orders | dept_id | CHAR(1) | 订单归属部门；实验限定 A/B；参与退款和客户关联 |
| orders | customer_id | BIGINT | 客户在本部门的 ID；不能脱离 dept_id 关联 |
| orders | order_date | DATE | Asia/Shanghai 的业务日期；不是时刻，不做 UTC 换算 |
| orders | paid_amount | DECIMAL(12,2) | 非负实付金额，人民币元 |
| refunds | refund_id | BIGINT | 主键，全局唯一 |
| refunds | dept_id、order_id | CHAR(1)、BIGINT | 联合外键，必须存在同部门订单 |
| refunds | refund_date | DATE | 退款发生日；本实验净收入仍归原订单日 |
| refunds | refund_amount | DECIMAL(12,2) | 单笔非负退款金额，人民币元 |
| customers | dept_id、customer_id | CHAR(1)、BIGINT | 联合主键，A/1 与 B/1 是不同客户 |
| customers | industry | TEXT | 当前行业，非空且非空白；更新后历史订单按当前行业分组 |

所有字段均为 NOT NULL。没有订单状态、税、币种换算或历史行业字段，不能推断支持这些口径。S2 只要求基准客户引用完整；缺失客户归为未知行业等 edge 实验在后续步骤独立完成。

## 3. 哪些规则由谁执行

| 规则 | 执行位置 | 边界 |
| --- | --- | --- |
| ID 唯一、字段非空、金额非负 | 数据库主键/NOT NULL/CHECK | MySQL、PostgreSQL 各自执行 |
| 退款属于同部门订单 | MySQL 复合外键 | `orders` 另有 `(dept_id, order_id)` 唯一约束，便于被引用 |
| 客户复合键唯一 | PostgreSQL 主键 | 不允许重复 A/1，但允许同时存在 A/1 和 B/1 |
| 订单客户在 CRM 存在 | `source_db.py verify` | 跨库无法用本实验的本地外键强制；不是数据库级保证 |
| 累计退款不超过实付 | `source_db.py verify` | 涉及多行累计，当前没有实现触发器或并发写入控制 |
| 净收入计算不放大 | 独立 Python 对账＋逐订单 SQL 核对 | 不是仅比较一个总额 |
| 普通分析身份不能写入 | 两个库的 SELECT 授权及拒绝测试 | 当前 reader 能读 A/B 全量；S5 才验证部门隔离 |

这张表是后续语义层的起点：SQL schema 只表达部分规则，完整业务语义还包含聚合顺序、跨库逻辑关系和时间口径。不要把“字段都有外键”当作 Agent 已经理解业务。

## 4. 基准与只读练习

工作目录为 `deployment/008_first_bi_prototype`。默认查询角色是 reader，密码从容器环境传给客户端，不需要复制到命令行。宿主没有 Docker socket 权限时使用 sudo。

查看订单和客户：

```bash
printf 'SELECT * FROM orders ORDER BY order_id;\n' | sudo python3 scripts/source_db.py query --source mysql
printf 'SELECT * FROM customers ORDER BY dept_id,customer_id;\n' | sudo python3 scripts/source_db.py query --source crm
```

执行正确的订单净收入查询：

```bash
sudo python3 scripts/source_db.py query --source mysql < sql/mysql/order_net.sql
```

预期四笔订单净收入依次为 **70、75、50、50**，合计 **245**。独立人工基准文件是 [fixtures/expected-baseline.json](fixtures/expected-baseline.json)。

动手比较错误关联：

```sql
SELECT SUM(o.paid_amount) AS duplicated_paid,
       SUM(o.paid_amount - COALESCE(r.refund_amount, 0)) AS wrong_net
FROM orders o
LEFT JOIN refunds r ON o.dept_id = r.dept_id AND o.order_id = r.order_id;
```

基准下应出现实付 390、错误净收入 345，而不是正确的 290、245。多出的 100 正是订单 1 的实付被重复累计。可以用 `source_db.py query --source mysql` 从标准输入执行这段查询。

再查看客户重复 ID：

```sql
SELECT customer_id, COUNT(*) FROM customers GROUP BY customer_id ORDER BY customer_id;
```

每个 customer_id 都有两条记录，因此只按 customer_id JOIN 会跨部门错配。S2 暂由 Python 读取两源后对账；通过单条跨库 SQL 验证仍是 S4 的任务。

## 5. 初始化与持续维护

`seed` 只插入不存在的基准 ID，不覆盖已存在的金额或行业，也不删除额外记录。重复运行不会产生重复行；如果手工修改后再次 seed，修改不会被悄悄重置，`verify` 会报告与固定基准的差异。

建表使用 `CREATE TABLE IF NOT EXISTS`，它不是 schema 迁移工具。将来修改字段或主键，应编写显式迁移并运行回归，不可假定再次 seed 会自动同步已有表结构。

MySQL 与 PostgreSQL 的初始化不是跨库原子事务。一个源成功、另一个源失败时，先修复失败原因再重跑；两源及对账全部通过后才记录整体成功。环境变量只在数据卷首次初始化时设置管理员密码，删除 `.env` 并重新生成不会自动修改已有卷内的密码。

## 6. 后续 BI 连接信息

| 项目 | MySQL 订单源 | PostgreSQL 客户源 |
| --- | --- | --- |
| Compose 网络内地址 | `mysql-source:3306` | `crm-source:5432` |
| 数据库/schema | `sales` | `crm` / `public` |
| 只读用户 | `bi_reader` | `crm_reader` |
| 本地密码变量 | `MYSQL_SOURCE_READER_PASSWORD` | `CRM_READER_PASSWORD` |
| 持久卷 | `bi008_mysql_source` | `bi008_crm_source` |

这两个地址是容器网络内 DNS 名称，不能直接在宿主浏览器打开。源库没有发布宿主端口，本步通过容器内数据库客户端访问。后续 Superset/Trino 连接时使用同一 Compose 网络；不使用管理员账号作为分析身份。

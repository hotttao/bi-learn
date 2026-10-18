# W1：把业务规则变成 Wren 可执行模型

## 1. 这一步和 S4 有什么不同

S4 直接维护跨库 SQL，Superset 使用它做展示。W1 则让 Wren 根据模型和关系展开 SQL，再经同一个 Trino 查询同一组源数据。用户查询逻辑模型 `bi_orders`，不用每次重写两个 JOIN。

这里使用开源 Wren CLI 0.16.0、Rust 引擎 wren-core-py 0.8.1，没有启动旧版聊天 UI，没有开发 Agent。MCP 与 Codex 工具接入属于 W2，本步没有启动 MCP 服务。

从 `deployment/008_first_bi_prototype/` 执行以下命令。

## 2. 安装与连接

已经安装好的本机，可直接跳到第 3 节。新环境使用：

```bash
python3 scripts/install_w1.py
```

脚本针对本次实测的 Linux amd64，要求 Python >=3.11，本机是 3.13.5。它把 uv 0.9.9 和 Wren 虚拟环境放进忽略的 `.local/`，按 `wren/requirements.lock` 安装固定版本，并将**本项目**的 bi008 profile 配回样例 Trino 连接；不改全局 Python 或全局 Wren profile。重复安装会恢复这份实验 profile，已有自定义连接时先阅读脚本。

前置条件是 S4 服务已经运行。宿主重启后，容器需要按当前配置手动恢复：

```bash
sudo docker-compose --profile sources --profile federation up -d --wait mysql-source crm-source metadata trino superset
```

安装输出必须出现 `Connection validated`；Wren 的 profile 命令可能只打印连接警告仍成功退出，不能只看退出码。接着运行：

```bash
scripts/wren.sh version
scripts/wren.sh profile debug
```

入口脚本固定项目、WREN_HOME、grep 知识后端。连接到 `127.0.0.1:18080`，`bi008_wren` 是未认证的 Trino 查询标签，不是部门身份。源端使用的仍是 S2 只读账号。

## 3. 先看模型，而不是先问自然语言

```bash
scripts/wren.sh context show
scripts/wren.sh context instructions
scripts/wren.sh memory describe
scripts/wren.sh memory recall -q '行业' --output json
```

三种文件各有用途：

| 文件 | 用途 |
| --- | --- |
| `wren/models/`、`relationships.yml`、`cubes/` | 引擎实际展开查询的定义 |
| `wren/knowledge/rules/`、`knowledge/sql/` | 给 Agent 的业务说明和已确认查询示例，不是强制权限 |
| `wren/target/mdl.json` | 构建产物；运行时消费它，不手改 |

三模型：`bi_orders` 一行一订单；`bi_refund_totals` 先按部门和订单汇总退款；`bi_customers` 一行一部门内客户。客户和退款汇总的主键都是两列，MDL 使用原生复合主键数组。

目标键使用别名 `customer_dept_id/customer_local_id`、`refund_dept_id/refund_order_id`，映射的仍是原来的部门、客户、订单 ID；这是当前引擎外关联同名键投影问题的规避，不是业务身份发生变化。

## 4. 构建，再看引擎实际写出的 SQL

```bash
scripts/wren.sh context validate --strict
scripts/wren.sh context build
scripts/wren.sh memory index
scripts/wren.sh dry-plan --sql 'SELECT SUM(net_amount) AS net_revenue FROM bi_orders'
scripts/wren.sh --sql 'SELECT SUM(net_amount) AS net_revenue FROM bi_orders' --output json
```

结果应为 `245.0000000000`，展示时可按人民币元保留两位。`dry-plan` 中应看到退款先汇总，再通过两字段关联；生成 SQL 可能使用 RIGHT OUTER JOIN，只要保留侧是订单，效果对应业务约定的订单 LEFT JOIN。

`context validate --strict` 表示“警告也视为错误”；`runtime/config.json` 中的 `strict_mode: true` 表示“运行时模型引用约束”，它们不是同一个开关。YAML 校验通过也不能证明查询的计算类型或业务结果正确。

当前未安装 memory 向量扩展。`memory index` 实测为 grep 后端 no-op，`memory describe` 和文本 `recall` 可用，`memory fetch` 会报缺少扩展；不要在本步声称已建向量索引。

## 5. 比较明细、行业和日期

```bash
scripts/wren.sh --sql 'SELECT order_id,dept_id,customer_id,industry,net_amount FROM bi_orders ORDER BY order_id' --output json
scripts/wren.sh cube query --cube net_revenue --measures total --dimensions industry --output json
scripts/wren.sh --sql "SELECT SUM(net_amount) AS net_revenue FROM bi_orders WHERE order_date >= DATE '2026-10-01' AND order_date < DATE '2026-10-02'" --output json
```

应看到订单 1～4，净额 70、75、50、50；行业软件 120、零售 125；第一天合计 120。这里 cube 是 Wren 的聚合定义，并没有部署另一款 Cube 产品。它把 `SUM(net_amount)` 注册为命名度量 total，Agent 可选择度量与维度，减少重复编写聚合 SQL。

## 6. 必须亲眼看一次拒绝与已知缺口

```bash
scripts/wren.sh dry-plan --sql 'SELECT * FROM mysql.sales.refunds'
```

预期 MODEL_NOT_FOUND。当前三张原始表、未知模型、嵌套 CTE 原始表和写入语句的 7 个用例均被拒绝。

但**不要把这一结果理解成完整授权**：本版本按表的短名检查模型，限定名可能留下未展开的物理查询。模型改用 bi_ 前缀，避免与当前物理表名碰撞，只是当前实验的规避。回归保留 `mysql.sales.bi_orders` 的规划探针（不执行），证明通用限定名缺口还存在；未来增加同名物理表时需要重新审计。本步不作为团队服务开放，不以提示词或命名约定替代权限。

## 7. 为什么金额还有两种 DECIMAL 写法

| 位置 | 当前实测与处理 |
| --- | --- |
| MDL 列类型 | 参数化 `DECIMAL(12, 2)` 在计算字段规划中被当作 Utf8，减法失败；使用通用 `DECIMAL`，原精度保留在 `properties.source_type` |
| SQL 金额常量 | `DECIMAL '0.01'` 被改写成无标度 `CAST('0.01' AS DECIMAL)`，在 Trino 中变为 0；使用显式 `CAST('0.01' AS DECIMAL(12,2))` |

两者属于不同处理环节，不矛盾。回归中有两个只在内存 MDL 中存在的 fixture：错误写法总额 245.00；显式写法四单各加一分，总额 245.04。返回值实际为 Arrow Decimal / Python Decimal，没有用浮点数比较金额。这个实验不证明任意精度和极大金额均正确。

## 8. 一键回归与复习

```bash
.local/wren-venv/bin/python scripts/verify_w1.py
```

脚本只读源数据；缺失客户和一分钱变更在临时内存模型中模拟，不写业务表。检查订单键/粒度、金额、行业、部门、日期、空集、cube、缺失客户、分级精度、运行时拒绝，以及已知问题是否仍需规避。依据见 [w1-verification.json](evidence/w1-verification.json)。

思考三个问题：关系声明为何不能代替唯一性检查？为什么只对 245 不能发现错误行业归属？Wren YAML 改动后，Superset 的 S4 数据集为什么不会自动改变？

目前模型构建和检查仍由人触发，Superset 保持 S4 资产。W2 才接 MCP，W3 才实验变更发现、发布与版本一致性。不要直接修改主模型练习变更发布，本步先 review 当前实现与限制。

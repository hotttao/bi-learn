# W3：业务关系与口径变化后，语义层怎样维护

2026-10-18，实验实现完成，待 review，未提交 W3。W2 已确认并提交为 `35db4e5`。本步沿用原计划，暂缓部门隔离，不新增公司数据库连接。

## 1. 这一步研究的核心

W1 解决“已知正确的关系怎样执行”，W2 解决“Codex 怎样消费它”，W3 解决“原来正确的关系和口径变了，怎样发现、修复并同步”。

先看实验中最关键的反例：旧客户关联失效后，总额查询仍是 245，但按行业查询是软件 240、零售 250，合计 490；订单明细从 4 行变成 8 行。只核对首页 KPI，会漏掉关系错误。

这不是让 Agent 更认真地写 SQL 就能解决的问题。关系必须有明确的键、粒度、基数、缺失处理和验证规则；变更后模型、业务规则、检索示例、执行入口必须使用同一个发布版本。

## 2. 业务语义该怎样组织

本次用一个分析主题组织模型：**订单净销售额分析**。不要直接按表数量划业务边界，也不要把每个外键都开放成可自由拼接的分析路径。

| 层次 | 本实验内容 | 维护位置与用途 |
| --- | --- | --- |
| 业务术语和适用范围 | 净销售额＝本实验净收入；不同于财务收入确认 | knowledge/rules；帮助 Codex 澄清含义 |
| 实体与事实粒度 | 订单每行一单；客户身份；退款每行一笔 | 模型主键、字段说明与源映射 |
| 允许的分析关系 | 客户按正确身份键关联；退款先汇总 | relationships 与 ref_sql；引擎实际执行 |
| 指标和时间 | 适用订单状态、净额公式、人民币元、退款回溯订单日、当前行业 | Wren 模型与 cube 执行；规则补充解释 |
| 证据与约束 | 候选键唯一性、关联后行数、明细和分组对账 | 源检查、独立账本与回归证据 |
| 维护与发布 | 变更说明、影响对象、版本与审核状态 | semantic-contract、release 描述与实际检查记录 |

这里的“分析关系”不是声明所有对象必须在一个事务内修改，也不意味着必须重构业务系统。当前只做查询模型，不新建业务聚合或跨服务写流程。

一张最小关系卡可以读成：

```yaml
# 说明格式，非新增产品 DSL；对应已验证的 relationship-v2 实验。
relationship: orders_customers
business_role: order_customer
source_grain: one_row_per_eligible_order
source_key: [dept_id, customer_id]
target_key: [customer_dept_id, customer_local_id]
expected_cardinality: many_to_zero_or_one
missing: retain_order_as_unknown_industry
time_semantics: current_customer_industry
checks:
  - target_composite_key_unique
  - order_rows_unchanged_after_join
  - industry_totals_reconcile_with_total
evidence: evidence/w3-verification.json
review_scope: synthetic_fixture_only
```

真正接公司业务时，必须补权威来源、业务角色、代码/报表依据、负责人和确认记录。例如“下单客户”和“付款客户”即使都能关联客户表，也应是两条不同角色的关系。不能由 Agent 仅凭字段同名替业务确定。

**计算定义继续以 Wren 模型为准**，不另维护一套手写公式再让 Agent 二选一。关系卡和发布说明用于解释依据、追踪变更；它们不能替代模型执行。各版本源码和编译结果可从 [review 快照](../evidence/w3-models) 比较。

## 3. 先准备独立实验，再制造关系变化

工作目录均为 `deployment/008_first_bi_prototype`。输入见 [business.json](../w3/fixtures/business.json)，它是合成 fixture，不是 S2 数据快照，也不代表公司的现有业务。

初始状态：客户 ID 1、2、3、4 全局唯一；订单按全局客户 ID 关联。退款与原实验保持同样的金额分布，净收入仍 245。

变化状态：B 部门的客户 3、4 改成部门内的 1、2，订单引用同步改变。字段名和类型完全不变，但全局唯一规则不再成立；此时旧模型仍按 customer_id 关联。

```bash
.local/wren-venv/bin/python scripts/verify_w3.py
```

脚本为本轮生成独立 Wren 项目，用 VALUES 查询作为源，实际在现有 Trino 上执行。不会向 MySQL/PostgreSQL 插入或修改业务记录，不会覆盖正常 `wren/` 项目。每次运行会产生新的时间戳 release ID，保留旧 release，不删除数据库卷。

每个候选都从 YAML/ref_sql 开始，运行 `context validate --strict`、`context build`、`memory index`。这不是只改内存 MDL 来模拟构建。grep 后端的 index 实际仍为 no-op，不能写成已验证向量索引发布。

## 4. 观察错误，再修复关系

| 状态 | 模型构建 | 客户键检查 | 总额 | 按行业 | 带行业订单明细 |
| --- | --- | --- | ---: | --- | ---: |
| 初始全局 ID＋全局键关联 | 通过 | 全局唯一通过 | 245 | 120 / 125 | 4 行 |
| ID 变成部门内唯一＋旧关联 | 仍通过 | 2 组重复键，失败 | 仍 245 | 240 / 250，合计 490 | 8 行 |
| 改为部门＋客户复合键 | 通过 | 复合唯一通过 | 245 | 120 / 125 | 4 行 |

总额查询不需要行业字段，生成计划裁掉了客户关联；行业和明细查询需要该关联，重复匹配才暴露出来。失败计划、实际返回和 Trino 质量检查 query ID 都保留在 [W3 证据](../evidence/w3-verification.json) 的 relationship_drift 下。

失败候选被阻止发布，当前版本指针不变。修正关系后，依次核对行数、订单键、行业和日期分组，再发布关系版本。独立预期答案由整数分账本计算，并与 fixture 中手写基准交叉核对，不使用被测 MDL 自证。

影响分析的一部分来自编译 MDL：`orders_customers` → `bi_orders.customer` → `bi_orders` → `net_revenue` cube。规则、示例和 Codex 消费入口目前是人工登记；Superset 数据集 2/图表 3 的对应业务映射也是人工登记。没有声称已经自动恢复跨系统完整血缘。

## 5. schema 不变，指标口径仍会变化

第二个实验固定关系修正后的所有数据，只调整明确写入 fixture 的业务政策：

| 版本 | 适用订单状态 | 总额 | 软件 / 零售 | 订单数 |
| --- | --- | ---: | --- | ---: |
| net_revenue@1 | paid、settled | 245 | 120 / 125 | 4 |
| net_revenue@2 | 仅 paid | 195 | 70 / 125 | 3 |

订单 3 的状态为 settled，本来净额 50，新版本将它排除；该字段从初始实验就存在。schema 和源快照均相同，只改变语义政策，证据见 metric_drift。

实际变更源于明确的实验规则，不是从数据推断业务决定。公司场景中，这个输入必须来自需求、应用规则或业务负责人；DDL 差异检测不能替代它。

## 6. 怎样保证模型与 Agent 同步

```mermaid
flowchart LR
  S[源与业务规则变化] --> Q[质量检查和差异定位]
  Q --> C[模型 规则 示例修改草稿]
  C --> R[构建和独立回归]
  R --> V[审核记录]
  V --> B[生成包含文件指纹的发布包]
  B --> P[切换本地版本指针]
  P --> M[新 MCP 会话加载同一发布包]
  M --> A[Codex 读取版本并查询]
```

[本地发布演练脚本](../scripts/w3_release.py) 保存源码、编译 MDL、规则、示例与语义契约的哈希清单，通过检查后生成独立目录，再替换 `current.json`。不会修改已经发布的目录来假装新版本。

这个发布器只接受 `synthetic_fixture / regression_harness` 的显式实验审核记录。它不是业务人员已批准，也不是企业发布平台。回归调用方负责质量与查询检查；发布器还独立核对源/MDL 和指标在契约、cube、规则、示例中的版本。

已实际验证：

1. 旧 MCP 会话在版本指针变化后仍保留旧知识，但拒绝继续查询，返回 `BI008_RELEASE_REVISION`；新会话才能使用新包。
2. 将新模型配旧规则的候选拒绝发布；即使调用方把检查布尔值全填 true，独立版本检查仍拒绝，当前指针不变。
3. 发布目录内规则被改动时，完整性检查拒绝。
4. 回退到关系修正版，得到 245；再激活指标 v2，恢复 195。

这是本地单指针的实验，不提供跨进程分布式事务、并发发布协调或数据库迁移回滚。底层 schema 已不兼容时，退回旧模型可能无法恢复服务，本实验没有覆盖这种情况。

## 7. 再让真实 Codex 使用新版本

```bash
python3 scripts/verify_w3_codex.py
.local/wren-venv/bin/python scripts/finish_w3.py
```

前一个脚本仅为一次 CLI 会话把 bi008_wren 命令覆盖为 `w3_mcp.sh`，不改正常 Codex 配置。Codex 必须读取当前契约、规则和模型，三条查询各自先检查计划和 dry-run，再查询。

实测 18 次 MCP 调用，新版本 `net_revenue@2` 返回 195，行业 70/125，日趋势 70/125。答案含实际 release ID 和 MDL 指纹，没有沿用旧答案 245。[Codex 调用证据](../evidence/w3-codex-verification.json)

一个限制也保留了：固定短关键词“净销售额”能 recall 命中，新 Codex 用很长的问题检索时返回空列表；它转而读取完整规则和 schema，正确完成查询。grep 检索不等于语义检索，也未给 Agent 提供所有历史版本的解释接口，因此不能声称已验证任意检索或完整历史追问。

你可先比较 [初始模型](../evidence/w3-models/initial/relationships.yml) 与 [修正版](../evidence/w3-models/relationship-v2/relationships.yml)，再比较两个版本的订单 ref_sql，最后对照 MCP 运行契约与实际答案。先看“为什么要改”和“会影响什么”，再看语法。

## 8. 日常维护到底应该做什么

| 变化 | 如何发现 | 应更新什么 |
| --- | --- | --- |
| 字段/类型/表结构改变 | schema 快照和迁移差异 | 源映射、依赖模型和回归 |
| 键或关系失效 | 唯一性、空值、关联后粒度和对账 | 身份键、关联路径、缺失规则与上下文 |
| 业务口径改变 | 需求、代码规则和业务确认 | 指标版本、状态/时间/金额政策、示例和迁移说明 |
| 新业务域/表增多 | 接入清单、概念角色与权威来源审核 | 按分析主题登记模型，显式新增允许关系；不自动开放所有路径 |

本步已经验证单次发现—修改—回归—发布—消费—回退的演练，尚未部署定时采集器或企业审核工作流。真正的公司语义层仍要从一个业务问题、少量现有表、已有报表 SQL/代码证据和业务确认开始，方法见 [存量业务接入](../../../docs/wiki/09-existing-business-semantic-onboarding.md)。不能把这次合成实验当作已恢复公司关系。

## 9. 停止、恢复与 review

实验包位于忽略目录 `.local/w3/releases/`，指针在 `.local/w3/current.json`。退出 Codex 后 stdio 服务结束；已有源库和 Superset 不需要因本实验停止。重新运行 verify_w3.py 会建立新一轮版本；需要重跑 Codex 验证和 finish_w3，使证据对应同一轮。

源验证命令需要 Docker 权限：

```bash
sudo python3 scripts/source_db.py verify > evidence/w3-source-baseline.json
```

它包含源只读账号拒绝和事务回滚约束检查。S2 源指纹仍相同，正常 Wren 项目和 Superset 仍使用 245 基线；W3 的 195 仅属于独立发布实验。Superset 不会自动切到新指标，若将公司口径正式推广到 BI，必须将其消费配置纳入审核与验收。

本步完成后等待 review，不提交、不进入下一阶段。部门隔离暂缓；存量公司的真实语义恢复、大规模目录、向量检索和生产自动发布仍未验证。

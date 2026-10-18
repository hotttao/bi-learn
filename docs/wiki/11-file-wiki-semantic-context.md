# 用文件 Wiki 与 Skill 向 Agent 提供业务语义

日期：2026-10-18。承接 [语义维护工具取舍](10-semantic-maintenance-tool-selection.md)；本地实现见 [009 · S9 操作教程](../../deployment/008_first_bi_prototype/walkthroughs/009_S9-WALKTHROUGH.md)。本步已验证、待 review，未提交。

## 1. 为什么可以绕过元数据 MCP

通用 Agent 可以直接阅读文件。将已审核的 Wren 原生模型与业务说明作为一个版本化目录提供给 Codex，通过 Skill 规定读取流程，就能采用：

**用户问题 → Skill → 业务域/主题索引 → 所需指标和关系页 → 相关 Wren YAML → 规划 SQL → 查询 MCP。**

不需要先做向量检索，也不必经 Wren MCP 的 `get_context/list_models/describe_model` 获取同一份模型。文件命令仍是工具调用，查询执行也仍需要受控入口；不能将“没有元数据 MCP”理解为“没有工具开销”。

参考 [LLM Wiki 原始思路](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) 中的来源、整理页和索引组织方式，以及 [OpenAI 官方 Skills](https://developers.openai.com/plugins/concepts/skills) 的按需读取方式。本项目不引入名为 LLM Wiki 的新服务，也不让查询 Agent 自动发布正式指标。

## 2. 文件怎样组织

```text
发布包/
├── .agents/skills/bi008-semantic-context/SKILL.md
├── wiki/
│   ├── index.md
│   ├── metrics/net-revenue.md
│   ├── relationships/current-industry.md
│   ├── relationships/historical-industry.md
│   ├── relationships/overlapping-tags.md
│   ├── SCHEMA.md
│   └── log.md
├── raw/reviewed-sales-rules.md
├── wren/models/<model>/metadata.yml
└── release.json
```

Wren 原生 YAML 保存字段、结构和已经实现的执行定义；Wiki 链接 YAML，解释业务目的与关系角色；Skill 规定如何导航和遇到冲突时怎么办。不要在三处复制字段、公式与 JOIN 规则。

关系按业务问题组织，而非只写“订单表关联客户表”：当前行业与历史行业采用不同时间语义；退款先聚合回订单粒度；标签是可重叠成员，分组金额不能相加作为总额。主题页应明确身份键、基数、粒度、时间边界、缺失处理、可加性及审核来源。跨域增长时增加分层索引和共享实体入口，避免穷举任意两表关系。

不需要一开始上正式本体。先统一实体、关系角色和指标；出现跨域身份对齐、多跳规则推导等明确需求后，再评估本体及推理工具。文件链接可表达关系，但不自动提供本体推理。

## 3. 与全量 MCP 上下文的实测比较

S9 用现有 Codex CLI 在隔离工作区运行三种问题各两次，与先前 S8 的全量 schema＋规则组作历史对照。原生目录相同，包含 500 模型，其中多数是空表别名；不是 500 张生产业务表。S9 开放文件读取，S8 不开放，时间与调用差异不能只归因于索引方式。

| 指标 | S8 全量 schema＋规则 | S9 文件 Wiki＋Skill |
| --- | ---: | ---: |
| 数值正确 | 6/6 | 6/6 |
| 工具调用 | 69 次 MCP | 24 次文件＋31 次查询 MCP |
| 元数据 MCP | 使用 | 0 次 |
| 单题实际读取模型 | 全量目录上下文 | 3 个相关原生模型 |
| 六次总耗时 | 340.626 秒 | 380.619 秒 |
| 累计 input tokens | 1,381,510 | 1,317,025 |
| 其中 cached input tokens | 1,144,704 | 1,129,088 |
| output tokens | 8,130 | 7,944 |

本次确认了按需读取和结果可行性，**没有证实更快**。计数是 CLI 的累计使用量，缓存输入是输入的子集，不能再次相加或当作单次上下文大小；小样本不能推出稳定成本收益或生产正确率。实际调用与结果见 [S9 汇总证据](../../deployment/008_first_bi_prototype/evidence/s9-wiki-comparison.json)。

## 4. 最关键的取舍：维护责任仍然存在

| 问题 | 文件方案能做什么 | 仍需要什么 |
| --- | --- | --- |
| 怎么找到相关语义 | 人可审查的索引、链接和 Skill，按需阅读 | 正确的主题划分、别名与歧义处理 |
| schema 改了怎么办 | 校验已发布文件与 Wren source revision，一致性失败则拒绝 | 结构变更采集、影响分析、审核和重新发布 |
| 业务口径变了怎么办 | 在来源、规则页和变更记录中表达候选变化 | 业务负责人确认；DDL 无法证明业务含义 |
| 怎么保证计算一致 | Wren 执行定义和固定问数回归可约束结果 | 将重要文字规则落成可执行指标，不能只靠提示词 |
| 多人或多机器使用 | 发布一致的只读目录/版本包 | 分发、回退、可信来源及真正权限控制 |

S9 的文件指纹预检拒绝缺页、YAML/规则被修改和执行版本不一致，**不是自动同步或原子发布协议**。修改需产生候选版本，检查依赖与冲突，构建并运行回归，审核后发布一致目录；查询 Agent 只读已发布版本。不要原地让它边查边改正式规则。

本轮为隔离读取方式使用 S8 的原始模型，部分规则仍由 Codex 根据文本生成 SQL；Wren 并未强制实现全部业务口径。主 W1 的可执行语义实验继续保留。只要重要规则还只在 Markdown 中，少了一层元数据服务也不会自动消除错误 JOIN 或口径漂移。

文件读取适合当前可分发、可审核的独立原型。若日后需要跨机器动态发现、按身份裁剪元数据、统一服务多种客户端，元数据 MCP/API 仍有价值；向量检索可以是后续补充，不是当前前置条件。部门隔离、Wren→Superset 自动语义同步、并发 BI 编辑保护仍按用户要求暂缓。

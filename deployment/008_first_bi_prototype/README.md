# 008：跟着实验理解 BI，再让 Codex 接入 MCP

## 当前状态与协作方式

2026-10-17：**S3 已确认并提交（`4f2c973`），Superset 入口仍为 `192.168.2.46:8088`。S4 已获用户确认：Trino 跨库对账、关系反例、语义契约和行业图均通过实测。S3 图表换位仍保留未验证项。MCP 与 Wren 尚未进入。**

依据：[首个原型方案](../../docs/wiki/08-first-bi-prototype.md)、[工作流](../../docs/wiki/workflow.md)。按用户指定使用单数 `deployment/`；仓库已有的空目录 `deployments/` 保留。

本实验先用 Superset 建立可核对、可编辑的 BI 基线，再以同一组数据实验 Codex＋Wren 新版。Wren 重点验证关系建模、查询及语义变更发布，Superset 保留作基线和展示入口；不假定两者已经自动同步。跨源查询由 Trino 阶段验证，首版不开发聊天应用或 Agent 框架。Superset 固定 6.1.0；S1 先准备元数据库与 Web 配置，其他组件在进入对应步骤时锁定版本，不使用 `latest`。

每个 S 编号都是一次独立 review 边界：

1. 开始前讲清这一步解决什么问题、需要理解什么、将改哪些文件。
2. 完成当前步骤的实现、必要验证和操作记录；解释结果，并提供可亲手重复的操作。
3. 展示变更、实际结果、失败与限制，停在“待 review”，不执行 Git 提交，也不进入下一步。
4. 用户确认后，只提交本步相关文件，再进入下一步；如果用户提出修改，先修改并重新交付 review。确认规划不等于提前确认后续全部实现。

当前阅读顺序：本文件 → [业务语义与增长实验](semantic-design.md) → [Codex 工作指令](agent.md)。[AGENTS.md](AGENTS.md) 是本目录的 Codex 指令入口。

## 场景：两个部门的销售运营分析

公司 A、B 两个销售部门使用 MySQL 记录实付订单和退款，CRM 使用 PostgreSQL 记录客户当前行业。运营希望看 2026 年 10 月净收入、按日趋势、行业比较，并定位订单。部门员工只看本部门，实验管理身份可核对全量。

先约定口径：人民币元，金额用定点数或整数分，展示到两位小数；订单日期采用 Asia/Shanghai 的业务日期；退款归属原订单日。净收入是实付减已记录退款，不代表财务收入确认。行业按客户**当前行业**，不承诺还原订单发生时行业。基准时间固定为 `[2026-10-01, 2026-11-01)`，回归测试不使用会随日期改变的“本月”。

| 订单 ID | 部门 | 客户 ID | 订单日 | 实付 | 退款明细 | 净收入 |
| --- | --- | --- | --- | ---: | --- | ---: |
| 1 | A | 1 | 2026-10-01 | 100 | 10、20 | 70 |
| 2 | A | 2 | 2026-10-02 | 80 | 5 | 75 |
| 3 | B | 1 | 2026-10-01 | 50 | 无 | 50 |
| 4 | B | 2 | 2026-10-02 | 60 | 10 | 50 |

客户共 4 条：A/1、B/1 是软件行业，A/2、B/2 是零售行业。退款共 4 条，每条有独立退款 ID 和所属部门。订单 ID 全局唯一，客户 ID 仅部门内唯一，客户键必须是 `(dept_id, customer_id)`。

人工答案：实付 290，退款 45，净收入 **245**；A **145**、B **100**；软件 **120**、零售 **125**；10 月 1 日 **120**、10 月 2 日 **125**。结果数据集必须恰好一行一订单，共 4 行。

### 三组数据分开运行

| 数据组 | 用途 | 初始化与核验计划 |
| --- | --- | --- |
| baseline | 人能算清的正确性基准 | S2 写建表和固定 seed，重复执行不重复插入；独立答案文件核对行数及金额 |
| edge | 暴露建模错误 | S4 加入缺失客户、重复客户维度、跨部门同 ID、跨日退款、空值/非法金额等；每个 case 独立 schema 或可恢复 fixture，单独列预期结果/拒绝原因 |
| scale | 性能与目录增长 | S8 用固定随机种子生成，参数化订单量、客户量、退款比例、日期跨度与部门倾斜；在独立库/schema 运行，不污染 245 基准 |

重复维度、孤儿退款、超额退款等作为数据质量实验：记录“拒绝写入”或“导入前校验失败”，不能依靠随意 DISTINCT 或静默删除使答案好看。缺失客户保留订单并归为“未知行业”；详细规则见语义设计。

## 分步计划与验收

| 步骤 | 本步实现与交付 | 带你理解 | 通过条件与 review 边界 |
| --- | --- | --- | --- |
| S0 规划（已提交） | README、agent.md、指令入口、语义与实验设计 | 组件职责、实验顺序、固定答案 | 文档自查完成，用户 review；不部署 |
| S1 环境与版本 | 核查宿主/沙箱、容器运行时、Compose、CPU/内存/磁盘、端口和访问方式；核对本步官方版本，指定镜像/驱动，写环境记录及配置骨架；构建成功后补镜像摘要 | 容器、业务库与 BI 元数据库的区别 | 运行时可用，配置校验通过；版本、端口、资源预算、持久卷、密钥注入路径明确；必要安装在本步实施并记录 |
| S2 数据源与造数 | 启动 MySQL 和业务 PostgreSQL；建表、seed、只读账号、数据字典、独立预期答案、重复初始化检查 | 主键、粒度、外键与跨源逻辑关系 | 两源 baseline 行数与金额正确；重复 seed 不增量；提供查询、连接方法、停止/重启和恢复方法 |
| S3 手工 BI（第一关） | Superset＋独立持久元数据库，先直连 MySQL；订单退款数据集、指标卡、日趋势、日期筛选、看板 | 数据集/指标/图表/看板分别保存什么 | 245；筛选 10-01 得 120；布局重开保留；订单 2 实付 80→90 后刷新得 255，恢复后得 245；记录缓存策略 |
| S4 跨库与关系（第二关） | 单节点 Trino、两个 catalog、正确/错误 JOIN 对照、跨源虚拟数据集、行业柱状图、edge 用例、可执行语义契约 | 基数、扇出、当前/历史维度、数据质量 | 245/120/125 对账；订单粒度不变；更新 A/1 行业为零售后软件 50、零售 195，总额 245，恢复后还原；异常用例按契约处理 |
| W1 Wren 建模与查询 | S4 后固定新版 Wren 版本，建立独立语义项目，复用同一测试源；核验 MDL、严格模型范围与固定查询 | 文档语义如何变成可执行模型 | 245 基准与 Superset 对账；退款和客户关联正确；记录模型版本与允许入口 |
| W2 Codex＋Wren MCP | 先固定工具调用，再用现成 Codex 发现模型并问数，不开发 Agent | 上下文、工具和执行模型的区别 | 同义问法、歧义和异常 fixture 通过；未验证的部门边界不作为团队服务开放 |
| W3 语义变更维护 | 独立 fixture 制造客户键和业务口径变化；发现、审核、构建、索引更新、发布与回归 | schema 不变也可能需要语义升级 | 能发现错误关系、核对版本一致性并恢复正确结果；明确 Superset 资产是否需要人工同步 |
| S5 部门隔离（第三关） | 可信 Trino 认证、A/B 受限身份和访问策略、各自 BI connection/dataset/普通用户 | 身份、连接权限、行列与资产权限的区别 | A 145、B 100；伪造用户名、猜资产 ID、导出、直接查询、缓存和撤权路径均验证；失败不得宣称隔离通过 |
| S6 官方 MCP 固定调用 | 核对版本实际工具清单、启动 MCP、固定参数完成预览→保存→加入指定看板；写调用与读回证据 | MCP 是工具协议；保存涉及外部副作用 | 原生资产可编辑、重开/刷新有效；重复调用不重复加图；中断恢复与 A/B 权限通过；不足时先报告最小适配范围 |
| S7 Codex 接入（第四关） | 将已验证 MCP 接给 Codex；加载语义上下文；运行中文问题集，覆盖 KPI/趋势/行业/明细、筛选和多轮修改 | 从业务意图到受控工具；区分模型理解与服务端校验 | 三句原型对话通过；保留人工改动；结果可追溯到身份、数据集和指标版本；同义问法、歧义、失败路径有记录 |
| S8 增长与语义演进 | 目录增长、数据量增长分开测；比较仅 schema 与语义契约；记录性能、错误、成本和下一步选型 | 何时需要汇总、语义服务、本体 | 先声明实验规模与可接受目标，再记录实测；明确是否需要独立语义层或分析仓库，不能从 4 条数据推出生产容量 |

执行顺序调整为 **S1 → S2 → S3 → S4 → W1 → W2 → W3 → S5 → S6 → S7 → S8**。保留原 S 编号便于对应历史记录；新增 W 步骤体现已确认的研究重点。Superset MCP 与 Wren MCP 是不同工具入口，后续 S6/S7 仍独立验收资产操作；W 阶段先验证语义，不宣称已打通图表保存。每一步继续单独 review。

S1 只固定本步配置目标，Trino/Wren 等进入相应步骤时再固定版本和依赖，只启动当前步骤需要的服务。S2 的业务 PostgreSQL 与 S3 的元数据库分开，减少误操作混淆。S3 是第一个可以亲手使用的看板；不等待 MCP 才交付。

### 每一步怎么教、怎么复现

每步 README 记录包含：目的与概念 → 前置状态 → 工作目录和完整命令 → 配置解释 → 实际输出摘要 → 浏览器操作路径 → 验收证据 → 故障处理 → 停止/恢复方法 → review 状态。对密码只写变量名和生成方式，不粘贴实际值。

手工 BI 步骤先示范，再让你亲手做一次创建、拖动、保存和刷新。自动化脚本用于复现配置与核验，不能替代你理解产品行为。没有浏览器操作能力时明确哪些已自动验证、哪些等你在 UI 验证，不能把 API 成功写成 UI 已通过。

每步结束回答三个问题：这一步数据经过哪些组件？什么事实证明结果正确？如果结果不对，先检查哪里？

S1 已新增 `compose.yaml`、`.env.example`、`scripts/prepare_env.py`、`superset/` 和本地忽略规则；这些是待运行验证的配置骨架。后续按需新增 `sql/`、`semantic/`、`evidence/`。只提交去敏后的配置和证据，不提交实际密钥、数据卷或原始会话凭据；实验服务默认限本机访问。

## S0 已完成操作记录

以下是实际发生的操作，不是后续部署命令。

| 顺序 | 操作 | 实际结果 |
| --- | --- | --- |
| 1 | 在仓库根执行 `pwd`、文件清单搜索、`git status --short` | 工作目录为本仓库；开始时工作区无变更；没有发现仓库 AGENTS.md |
| 2 | 阅读 `docs/wiki/08-first-bi-prototype.md` 和 `docs/wiki/workflow.md` | 确认四关顺序、245 基准、部门隔离与原生资产要求 |
| 3 | 阅读 `docs/wiki/agent-semantic-research.md`，查看根目录、deployments、harness | 原研究支持先用轻量语义；现有 deployments 为空，本任务按要求另建 deployment |
| 4 | 执行 `command -v codex docker` 和 `codex --help` | PATH 未找到两命令；后者报 command not found。尚未检查宿主运行时，不把它写成“宿主未安装” |
| 5 | 查阅 OpenAI 官方 AGENTS.md 与 MCP 文档、W3C OWL 概览 | 核实指令文件入口、MCP 接入边界与本体概念；链接见 agent.md、semantic-design.md |
| 6 | 创建本目录四份 Markdown 文档 | 完成分阶段计划、场景、语义设计和工作指令；未创建数据库或运行部署 |

交付自查：使用 Python 标准库检查 4 份 Markdown 的 5 个本地链接、代码围栏配对及行尾空格，全部通过；独立核算 290/45/245、部门 145/100、行业 120/125，以及刷新实验 255、行业变更 50/195，全部一致。执行 `git diff --check` 无错误；由于文件尚未跟踪，新增文件的空白检查由上述脚本覆盖。`git status --short --untracked-files=all` 显示仅本目录的四份新增文档。未运行部署、数据库或 UI 测试，未执行 Git 提交。后续每步追加同样的实际记录，不覆盖历史失败。

### 后续步骤记录模板

```text
步骤/日期：
状态：执行中 / 待 review / 已确认待提交 / 已提交 / 阻塞
目标与本次讲解：
前置条件、版本、工作目录：
实际命令（去敏）和文件变更：
实际结果与证据路径：
用户亲手操作与观察：
验收：预期 / 实际 / 通过或失败或未测
遇到的问题、原因、处理：
停止与恢复操作（破坏性操作单独说明）：
review 反馈与确认：
提交：提交标题；哈希在后续记录中引用，未提交时不虚构
下一步（仅确认并提交后开始）：
```

## 当前 review 范围

S1 `a0b9333`、S2 `1183f69`、S3 `4f2c973` 已提交，没有 push。当前工作为 S4，交付范围见本文末尾；教程见 [S4-WALKTHROUGH.md](S4-WALKTHROUGH.md)。S3 图表拖动换位未验证的限制继续保留。以下保留各步当时的记录。

## S1 操作记录：环境与配置准备

### 1. 这一步先理解什么

Docker Engine 运行容器；Compose 描述服务、网络、持久卷和依赖。镜像是可重建的程序环境，卷是需要保留的数据。本步的 `metadata` PostgreSQL 专门保存 Superset 用户、数据集和看板配置，不存放订单实验数据。删除元数据库卷会丢失 BI 配置，因此日常停止使用 `stop`，不使用带 `-v` 的清理命令。

`superset-init` 是后续迁移元数据库的短任务，不是第二个常驻 BI；当前默认命令仅迁移 schema，尚不创建管理员或初始化角色。S3 将按固定版本完成整套初始化并验证，不要现在直接启动 Web 并认为安装已经完成。

### 2. 已实际执行的环境检查

日期：2026-10-16。在仓库根执行：

```bash
command -v docker podman codex sudo
id
nproc
systemctl is-active docker
ls -l /var/run/docker.sock
ss -ltn
free -h
df -h .
sudo -n true
apt-cache policy docker.io docker-compose-v2 docker-compose
```

沙箱内未发现运行时，随后经系统授权在宿主重复检查。实际结果：Debian 13 amd64，4 CPU，约 7.7 GiB 内存、5.2 GiB 可用，8 GiB swap，工作盘约 936 GiB 可用；Docker/Podman/Codex CLI 不在 PATH，Docker socket 不存在，服务 inactive。8088 端口未占用。

`sudo -n true` 返回 `sudo: a password is required`。当前执行权限允许检查宿主，但不会自动赋予 root，无法非交互安装系统软件。没有尝试收集或代输用户密码。

APT 缓存中 Docker 包候选为 `docker.io 26.1.5+dfsg1-9+deb13u1`、`docker-compose 2.26.1-4`，均未安装；`docker-compose-v2` 无候选。采用 Debian 软件源包作为本地实验运行时，不额外增加 Docker 软件源；实际安装版本在更新索引后再次记录。

网络检查：

```bash
curl -I --connect-timeout 10 --max-time 20 https://registry-1.docker.io/v2/
```

沙箱因无法连接本机代理 `127.0.0.1:7897` 失败；经授权在宿主重试，收到 registry 的 HTTP 401 认证挑战，证明注册表端点可达，不代表镜像拉取已经成功。Docker daemon 的代理配置独立于终端，实际 pull 若失败需继续诊断，不能直接套用终端网络结论。

### 3. 配置骨架与版本依据

| 文件/对象 | 本步内容 | 验证状态 |
| --- | --- | --- |
| `compose.yaml` | 独立项目 `bi008`；元数据库、Web 和按需 init 服务；Web 仅绑定 `127.0.0.1:8088`；元数据库不发布宿主端口 | `docker-compose config --quiet` 已通过 |
| `superset/Dockerfile` | `apache/superset:6.1.0`＋`psycopg2-binary==2.9.10`，使用镜像内虚拟环境安装 | 派生镜像构建、Superset 与驱动导入通过 |
| 元数据库镜像 | `postgres:17.6-bookworm`，作为固定实验候选，不代表最新补丁或生产推荐版本 | 镜像拉取和 PostgreSQL 版本检查通过，真实数据库连接留待 S3 |
| `superset/superset_config.py` | PostgreSQL URI、强制本地密钥、关闭首轮查询结果缓存、同步查询，保留 CSRF | 本地及容器内配置加载通过；Web 启动留待 S3 |
| `scripts/prepare_env.py` | 生成随机本地凭据，文件权限 0600，拒绝覆盖已有文件 | 首次生成和再次执行保留原文件的路径均已运行 |
| `.gitignore`、`.env.example` | 忽略真实凭据，只共享变量名 | `git check-ignore .env` 已通过 |

Superset **6.1.0** 默认镜像是 lean，不能根据 Next 文档认为其自带数据库驱动。[6.1.0 镜像文档](https://superset.apache.org/admin-docs/6.1.0/installation/docker-builds/)、[固定版本 Dockerfile](https://github.com/apache/superset/blob/6.1.0/Dockerfile)。MySQL、Trino 驱动在相应步骤加入并实测；MCP 依赖也不假定已安装。

配置为元数据库限制 512 MiB、Web 限制 1536 MiB、初始化任务限制 1536 MiB。它们是本机小数据实验预算，不是测得的容量保证；后续 Trino/Wren 采用分阶段启动，先测资源再调整。当前没有常驻实验服务。

### 4. 本地验证的实际操作

在本实验目录执行：

```bash
python3 scripts/prepare_env.py
python3 scripts/prepare_env.py
git check-ignore .env
git diff --check
```

首次生成 `.env`；再次执行提示已有文件且不覆盖。真实密码未输出，未提交。另用 Python 标准库完成语法检查、0600 权限和随机值长度检查、数据库密码特殊字符编码检查、占位密钥拒绝检查和缓存配置检查，均通过。这些检查不替代镜像构建、连接测试或 Compose 校验。

环境未安装 PyYAML，因此没有以第三方 YAML 解析替代 Compose 验证；运行时安装后已使用真实 Compose 完成配置验证。没有进行数据库初始化、业务造数或 UI 操作。

### 5. Docker 安装（已完成）

最初因 sudo 密码交互无法继续；随后用户明确授权使用其凭据，由 Agent 在交互终端完成安装。密码不写入任何项目文件或本文。实际执行：

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose
sudo systemctl enable --now docker
```

安装后实际验证：

```bash
sudo docker version
docker-compose version
```

这里使用 Debian 包提供的 `docker-compose` 命令。暂不将用户加入 docker 组；后续使用 sudo 操作 daemon，权限问题与安装完成情况分别处理。

实际安装 Docker Engine `26.1.5+dfsg1`（Debian 包 `26.1.5+dfsg1-9+deb13u1`），Compose `2.26.1-4`，Docker 服务 active 且已 enable。`docker-compose config --quiet` 已返回成功，`sudo docker image ls` 初始为空。继续同一个 S1 的镜像与驱动验证，不进入 S2。

本步新增配置尚未得到 review，未提交。上一轮规划与 wiki 的提交为 `d61faeb`，没有 push。验证结束没有运行中的实验容器；Compose 创建的空网络和元数据库卷保留。不要删除现有 `.env`，以免后续误换元数据库密码和 Superset 密钥。


### 6. Docker daemon 网络诊断与修复（2026-10-17）

首次 `sudo docker pull postgres:17.6-bookworm` 直连 Docker Hub 超时；终端经代理测试得到预期的 HTTP 401 认证挑战。检查时 `/etc/systemd/system/docker.service.d` 不存在，因此新增独立 drop-in，没有覆盖其他配置。

依据 [Docker daemon 代理配置](https://docs.docker.com/engine/daemon/proxy/)，使用本机已有代理地址，为 Docker 服务配置 HTTP/HTTPS 代理。源文件是 `host/docker-proxy.conf`，本机安装位置为 `/etc/systemd/system/docker.service.d/bi008-proxy.conf`。这是本机网络配置，换机器需调整代理地址；Docker 镜像拉取现在依赖该代理进程可用。

在实验目录实际执行：

```bash
sudo install -D -m 644 host/docker-proxy.conf /etc/systemd/system/docker.service.d/bi008-proxy.conf
sudo systemctl daemon-reload
sudo systemctl restart docker
sudo docker pull postgres:17.6-bookworm
sudo docker pull apache/superset:6.1.0
```

重启时没有已有业务容器。第一次经代理请求镜像清单遇到 EOF，重试后开始下载镜像层。最终拉取与构建结果见后续记录，不能仅凭开始下载视为通过。

如需回退这项本机代理配置，可将该 drop-in 移出 `.conf` 后缀并执行 `daemon-reload`、重启 Docker；无需删除镜像或数据卷。回退后直连是否可用取决于本机网络。未来存在业务容器时，重启 Docker 需要考虑服务中断。


### 7. 可重复的 S1 验证入口

新增 `scripts/verify_s1.sh`，从实验目录执行：

```bash
sudo bash scripts/verify_s1.sh --local-proxy
```

脚本依次校验 Compose、检查运行时、下载缺失的固定镜像（失败最多重试三次）、构建 Superset、用一次性容器检查 PostgreSQL 版本、Superset/驱动导入和配置加载，再打印镜像 ID 与摘要。它不会执行元数据库迁移、创建管理员或造数。一次性容器结束后删除，本次 Compose 实际创建并保留了 `bi008_default` 网络和空的 `bi008_metadata` 卷，未启动数据库。

`--local-proxy` 使用 `host/compose.build-proxy.yaml`，仅构建阶段使用宿主网络和本机代理；运行中的 Web 和数据库仍使用 Compose 网络。其他机器不需要此代理时省略该参数。不要通过 `docker-compose config` 打印完整配置，因为它会展开本地凭据；只使用 `config --quiet` 校验。

已通过：`bash -n scripts/verify_s1.sh`、基础 Compose 与代理构建覆盖文件的 `config --quiet`。一次检查误在仓库根运行、未指定配置路径，提示 `no configuration file provided`，切换实验目录后通过。

下载期间曾遇到仓库清单 EOF，随后本机代理连接短暂被拒绝，正在下载的镜像因而失败；再次检查代理恢复了 HTTP 401 认证挑战，已通过验证脚本重试。没有修改代理软件本身或使用第三方镜像源。


### 8. S1 最终验收（2026-10-17，待 review）

`sudo bash scripts/verify_s1.sh --local-proxy` 最终退出码为 0。两次 EOF 重试后完成下载，派生镜像中的驱动安装约 22 秒。实际关键输出：

```text
Docker server: 26.1.5+dfsg1
Docker Compose version 2.26.1-4
postgres (PostgreSQL) 17.6 (Debian 17.6-2.pgdg12+1)
PASS: Superset 6.1.0, psycopg2-binary 2.9.10, configuration loaded.
S1 image probes passed. No business data or BI application was initialized.
```

镜像 ID、仓库摘要、检查范围和未测项已保存为 [evidence/s1-runtime.json](evidence/s1-runtime.json)。本地派生镜像未推送仓库，所以没有 RepoDigest，记录本地 Image ID；基础镜像记录实际 RepoDigest。当前配置使用固定版本 tag，摘要是本次构建证据而非自动锁文件；跨机器要求完全相同基础镜像时，需使用证据中的 digest 引用，并对重建结果再次检查。

`docker-compose ps --all` 仅输出表头，证明一次性检查后没有项目容器残留。空网络与空元数据库卷保留，不执行破坏性清理。没有启动 Superset Web、迁移元数据库、创建管理员、初始化业务数据或验证浏览器操作，因此当前没有可访问的 BI 页面。

本步可以回答：

- **哪些组件已准备好？** Docker 运行时、PostgreSQL 基础镜像、带 PostgreSQL 驱动的 Superset 镜像和本地配置。
- **什么证明它们可执行？** 真正启动过一次性容器，读取 PostgreSQL 版本并导入 Superset/驱动、加载配置；不是仅检查文件存在。
- **失败时先检查什么？** 镜像下载看 daemon 代理；构建依赖看构建代理；配置加载看 `.env` 是否存在与变量是否齐全；数据库连接要在后续初始化后单独验证。

review 文件：README、agent.md 的路线更新，以及 compose、Dockerfile、配置、凭据生成脚本、S1 验证脚本、host 代理配置和运行证据。真实 `.env` 被忽略；`.codex/` 是用户的配置与技能文件，不纳入本步提交。S1 尚未提交，下一步 S2 的数据源与造数仍未开始。


## S2 操作记录：业务数据源、造数与独立对账

日期：2026-10-17。**已完成、待 review、未提交。** 前置 S1 提交为 `a0b9333`。当前两个业务源持续运行，Superset 的元数据库和 Web 没有启动。

### 1. 场景与本步讲解

场景仍为 A/B 两个销售部门：MySQL `sales.orders/refunds` 保存已实付订单和退款；独立 PostgreSQL `crm.public.customers` 保存客户当前行业。每张表各 4 条固定记录，不使用公司真实数据。数据字典、粒度、关系和练习见 [DATA-DICTIONARY.md](DATA-DICTIONARY.md)。

这一步最重要的是区分：**一行订单、一行退款、一行部门内客户是三个不同粒度。** 退款在同一个数据库中，可以用 `(dept_id, order_id)` 外键约束；客户位于另一个数据库，本步先由程序检查跨库逻辑引用。后续 Trino/Wren 的工作，是把这些已确认的关系交给统一查询和语义模型。

金额使用 `DECIMAL(12,2)`，Python 使用 Decimal；日期为 Asia/Shanghai 业务日期。退款发生日期晚于订单日，但本例净收入仍归属原订单日。种子数据和独立答案分开存放。

### 2. 文件与配置

| 文件 | 作用 |
| --- | --- |
| `compose.yaml` 的 `sources` profile | `mysql-source` 与 `crm-source` 两个独立实例；无宿主端口映射，各有独立持久卷 |
| `sql/mysql/001_schema.sql`、`002_seed.sql` | 订单/退款表、约束及固定记录 |
| `sql/postgres/001_schema.sql`、`002_seed.sql` | 客户复合主键、行业说明及固定记录 |
| `sql/mysql/order_net.sql` | 退款先汇总，一行一订单的净收入查询；S3 的数据集起点 |
| `fixtures/expected-baseline.json` | 固定人工答案，独立于被测 SQL |
| `scripts/prepare_env.py` | 为 S2 追加 4 个缺失密码，保留 S1 已有密钥与密码；重复运行不覆盖 |
| `scripts/source_db.py` | seed、只读查询、原始记录对账、约束与拒绝测试；无需宿主安装数据库驱动 |
| `scripts/verify_s2.py` | 启动源、seed 两次、核验数据指纹、停止再启动、生成最终证据 |
| `evidence/s2-sources.json` | 实测版本、镜像摘要、金额、拒绝结果、初始化与重启验证 |

MySQL 使用固定实验版本 `8.4.6`；业务 PostgreSQL 复用 S1 已下载的 `17.6-bookworm`，以不同容器和卷运行。版本是实验基线，不代表最新版本。后续源连接器兼容性仍需实际验证。参考官方 [MySQL 镜像入口](https://github.com/docker-library/mysql/blob/master/8.4/docker-entrypoint.sh)、[PostgreSQL 镜像入口](https://github.com/docker-library/postgres/blob/master/docker-entrypoint.sh) 和 [MySQL 外键说明](https://dev.mysql.com/doc/refman/8.4/en/create-table-foreign-keys.html)；实际版本和镜像以证据文件为准。

MySQL 内存上限 768 MiB、InnoDB buffer pool 128 MiB；业务 PostgreSQL 上限 384 MiB。它们是小数据实验预算，尚未测生产容量。

### 3. 实际执行过程与故障修复

工作目录为 `deployment/008_first_bi_prototype`。实际执行的主要命令：

```bash
sudo docker pull mysql:8.4.6
python3 scripts/prepare_env.py
python3 scripts/prepare_env.py
docker-compose --profile sources config --quiet
sudo python3 scripts/verify_s2.py
```

镜像下载成功。凭据生成第一次输出新增 4 项，第二次输出新增 0 项，原有值保留；`.env` 没有被提交。

第一次验证在启动等待时失败：PostgreSQL healthy，MySQL unhealthy。通过以下命令读取日志和健康检查结果：

```bash
sudo docker logs --tail 80 bi008-mysql-source-1
sudo docker inspect --format '{{json .State.Health}}' bi008-mysql-source-1
```

日志显示 MySQL 已就绪，健康检查却持续返回 `ERROR 1130: Host '127.0.0.1' is not allowed to connect`。原因是配置将 root 限定为 localhost，检查却显式使用 TCP 地址。将健康检查改为 `--protocol=socket -uroot` 后重跑原验证脚本；Compose 重建 MySQL 容器并复用原卷，没有删除或清空数据。

修复后实际执行顺序：

1. Compose 只启动两个源并等待 healthy，不启动 metadata 或 Superset。
2. 管理员通过本地连接执行建表/seed；配置 MySQL `bi_reader` 和 PostgreSQL `crm_reader`。
3. 用 reader 经 TCP 和服务 DNS 读取三张原始表，Python 独立计算，比较固定答案；再核对 MySQL 逐订单 SQL。
4. 验证 reader 写入和建表被拒绝；验证错误部门退款、负金额、重复客户键被拒绝。
5. 再执行一次 seed 与全部验证，比较完整原始记录 SHA-256，确认无重复、无静默覆盖。
6. `stop` 两个源，再 `up -d --wait`，重新验证；数据指纹和全部结果保持一致。
7. 写入去敏证据，两个源保持运行，供下一步使用。

seed 不会覆盖已有记录。如果未来人工更改了金额，重跑 seed 不会把它偷偷改回基准；需要按实验记录显式恢复后再对账。`CREATE TABLE IF NOT EXISTS` 也不是迁移机制，schema 变化不能靠重跑 seed 同步。两源初始化不具备跨数据库原子事务，失败时修复后重跑并完成整体核验。

### 4. 验收实际结果

验证脚本最终退出码 **0**。运行版本：MySQL **8.4.6**、PostgreSQL **17.6**。最终状态：`bi008-mysql-source-1` 和 `bi008-crm-source-1` 均 healthy，仅显示容器内部端口，没有发布宿主端口。

| 验收项 | 预期 | 实际 |
| --- | --- | --- |
| 表行数 | orders/refunds/customers 各 4 | 4 / 4 / 4，通过 |
| 金额总计 | 实付 290、退款 45、净收入 245 | 290.00 / 45.00 / 245.00，通过 |
| 逐订单净收入 | 70、75、50、50 | 与 Python 独立计算及 SQL 逐行一致 |
| 部门分组 | A 145、B 100 | 145.00 / 100.00 |
| 当前行业分组 | 软件 120、零售 125 | 120.00 / 125.00 |
| 按订单日 | 10-01 为 120、10-02 为 125 | 120.00 / 125.00 |
| 重复初始化 | 三表内容不变 | 完整原始记录指纹一致 |
| stop/up 后持久性 | 三表内容和检查结果不变 | 全部一致 |
| 两源 reader 的 UPDATE 和建表 | 拒绝 | 4 个拒绝测试通过 |
| 错部门退款、负实付、重复客户复合键 | 拒绝 | 3 个约束拒绝测试通过 |

PostgreSQL 权限测试先将会话 `default_transaction_read_only` 关闭再尝试 UPDATE/DDL，仍然因权限被拒绝；因此不是仅靠会话开关限制写入。当前 reader 可以看 A/B 全量测试记录，**尚未验证部门隔离**。

本步行业与部门分组由 Python 从两源原始记录计算；**没有执行单条跨库 SQL**。行业对账通过不等于 Trino 或 Wren 已接通。累计退款上限、跨库客户引用由校验程序检查，尚非数据库层写入保证；具体边界见数据字典。

### 5. 跟着做一次

先执行正确查询，观察 4 行订单的净收入：

```bash
sudo python3 scripts/source_db.py query --source mysql < sql/mysql/order_net.sql
```

再按数据字典中的错误 JOIN 示例查询，思考为什么订单 1 的 100 元被算了两次。最后到客户表查看重复的 customer_id，解释为什么还需要部门字段。所有练习默认使用 reader，不需要查看或复制实际密码。

只做数据核验，不重新初始化或重启：

```bash
sudo python3 scripts/source_db.py verify
```

完整重复 S2 会停止并启动这两个源，适合实验复现；日常查询无需重跑完整脚本。

### 6. 停止、恢复与当前 review 边界

暂时不用时停止业务源，卷内数据保留：

```bash
sudo docker-compose --profile sources stop mysql-source crm-source
```

恢复同一组数据：

```bash
sudo docker-compose --profile sources up -d --wait mysql-source crm-source
sudo python3 scripts/source_db.py verify
```

已有卷上可重复执行 seed 补缺失的基准 ID；不会清除额外数据或覆盖修改。如果数据质量/基准不一致，先查差异，不用删除卷解决。管理员密码来自首次建库，后续不要随意重建 `.env`；本步未验证备份恢复或容器卷损坏恢复。

本步已完成概念、配置、造数和运行验收，尚未提交，等待用户 review。review 建议从数据字典、SQL 和独立答案开始，再看脚本与证据。下一步 S3 才初始化 Superset 元数据库、创建管理员、接 MySQL，手工做指标卡、日趋势和日期筛选。`.codex/` 用户配置不属于本步变更。


## S3 操作记录：Superset 原生净收入看板

日期：2026-10-17。状态：**核心验收通过，待 review，未提交；图表拖动换位待手工确认。** 前置 S2 提交为 `1183f69`。本步没有接 Trino、MCP 或 Wren。

### 1. 访问入口与教学顺序

用户指定监听 `192.168.*` 局域网地址。通过 `ip -4 -o addr show scope global`、路由和 `hostname -I` 确认本机地址为 **192.168.2.46**，将 `.env` 的 `SUPERSET_BIND_HOST` 和 Compose 端口映射改为该地址。

- 登录入口：http://192.168.2.46:8088
- 实际看板：http://192.168.2.46:8088/superset/dashboard/1/
- 用户：`admin`；密码从本机 `.env` 的 `SUPERSET_ADMIN_PASSWORD` 读取，未写入 Git。
- 亲手复现：[S3-WALKTHROUGH.md](S3-WALKTHROUGH.md)。

容器内 Gunicorn 仍监听 `0.0.0.0:8088`，宿主仅发布到指定 LAN 地址；不是将宿主所有网卡都开放。`docker port` 实测为 `8088/tcp -> 192.168.2.46:8088`，`ss -ltn` 也确认该绑定，LAN 地址的 `/health` 返回 HTTP 200。Chrome 在本机经这个 LAN 地址完成验证；未从第二台设备单独验证连通性。该 IP 来自 DHCP，变化时需修改绑定配置并重建 Web 容器。

### 2. 这一步理解哪些对象

本步先直连 MySQL，不复制 CRM 数据，也不伪装跨库查询：

```text
浏览器 → Superset Web → MySQL 只读连接 → 订单/退款
                ↓
       独立元数据库：用户、数据集、图表、看板和布局
```

数据集 `008_order_net_v1` 保存一行一订单的 SQL 与字段；指标 `net_revenue` 保存 `SUM(net_amount)`；图表保存类型、指标与时间维度；看板保存图表引用、布局与原生筛选配置。源数据更新不需要重建这些资产，只需重新查询。

本次由脚本调用 Superset 原生 REST API 创建资产，再由真实 Chrome 验证显示与交互。**没有将脚本创建说成用户已亲手操作。** 教程提供 SQL Lab、数据集编辑、创建两图、添加时间筛选和修改布局的练习，建议使用个人后缀另建练习资产，保留基线。

### 3. 实际部署与初始化

工作目录：`deployment/008_first_bi_prototype`。新增部署入口 `scripts/deploy_s3.py`，按阶段执行：

```bash
sudo python3 scripts/deploy_s3.py --phase runtime
python3 scripts/deploy_s3.py --phase assets
sudo python3 scripts/deploy_s3.py --phase browser
sudo python3 scripts/deploy_s3.py --phase finish
```

runtime 实际执行：

1. 校验 Compose，并使用本机代理覆盖配置构建派生 Superset 镜像。
2. 启动/检查 metadata、mysql-source、crm-source。
3. 用一次性 init 容器运行 `superset db upgrade`，建立持久元数据库表结构。
4. 运行 `superset init` 同步权限角色。
5. 运行 `superset/bootstrap_admin.py`，通过应用安全管理器创建 admin；密码取环境变量，不放在进程参数。重跑发现用户已存在时保留原密码。
6. 启动 Superset Web，等待 healthy。元数据库、两个业务源和 Web 共四个常驻容器；初始化容器结束即删除。

元数据库使用 S1 的 `bi008_metadata` 卷，与两个业务卷独立。没有加载官方示例数据。

### 4. 驱动与网络问题的实际处理

首次加入 `PyMySQL[rsa]==1.1.1` 后，MySQL 连接创建成功，但虚拟数据集 POST 返回 500。查看 Superset 日志发现 `superset/db_engine_specs/mysql.py` 的类型识别仍导入 `MySQLdb`，缺少模块导致失败。保留实际 SQLAlchemy 的 PyMySQL 连接，另外安装 `mysqlclient==2.2.7` 供类型识别使用，并增加编译/客户端头文件依赖。修复后虚拟数据集元数据读取、SQL 查询和页面图表均成功。

派生镜像的 Python 驱动固定为 PostgreSQL `psycopg2-binary==2.9.10`、MySQL `PyMySQL[rsa]==1.1.1` 和 `mysqlclient==2.2.7`。原始镜像仍为 `apache/superset:6.1.0`。S3 镜像 ID 与 S1 不同，记录在最终证据中。

APT 更新一度下载较慢。先补齐大小写代理变量，再验证清华 Debian 镜像直连正常，将它仅配置到 `host/compose.build-proxy.yaml` 的 `DEBIAN_MIRROR` 参数及 NO_PROXY。通用 Dockerfile 默认仍使用 Debian 官方源。两次主动中止慢构建后重试；最终系统依赖安装约 16.5 秒、Python 驱动安装约 23.5 秒。未删除业务或元数据库卷。

在 MySQL 驱动构建期间，单独执行过 `sudo docker-compose up -d --no-deps superset`，先落实用户要求的 LAN 绑定；最终 runtime 又使用完整驱动镜像重建 Web。数据库连接地址始终是容器网络内 `mysql-source:3306`，不会因浏览器入口改为 LAN 而变化。

版本核查依据：[6.1.0 图表 API schema](https://github.com/apache/superset/blob/6.1.0/superset/charts/schemas.py)、[数据集 API schema](https://github.com/apache/superset/blob/6.1.0/superset/datasets/schemas.py)、[看板 API schema](https://github.com/apache/superset/blob/6.1.0/superset/dashboards/schemas.py)。配置与 API 均以实际部署版本运行结果为准。

### 5. 原生资产与 API 对账

`assets` 阶段使用登录 API 获取临时令牌和 CSRF，再创建实验专属名称的资产，读回并记录 ID。实际 SQL 继续来自 `sql/mysql/order_net.sql`，未复制另一份收入公式。

| 资产 | 实际 ID/定义 |
| --- | --- |
| 只读数据连接 | 1，`008 MySQL sales (read-only)`；用户 bi_reader；关闭 DML/CTAS/CVAS |
| 虚拟数据集 | 1，`008_order_net_v1`；默认时间列 order_date |
| 指标 | net_revenue，`SUM(net_amount)`，人民币元 |
| 指标卡 | 1，`008 净收入`，Big Number Total |
| 日趋势 | 2，`008 按日净收入`，ECharts Line Chart |
| 看板 | 1，`008 净收入入门看板`，原生可编辑资产 |
| 原生日期筛选 | 订单日期，作用于两图，默认 `[2026-10-01, 2026-11-01)` |

API 查询整月得 245，单日 `[2026-10-01, 2026-10-02)` 得 120。资产记录见 [s3-assets.json](evidence/s3-assets.json)。同名资产存在时脚本复用，不直接覆盖已有图表参数或非空看板布局；它不是通用资产迁移或跨调用事务机制。

### 6. Chrome 页面验证与结果

宿主已有 Google Chrome。Python venv 创建因未安装 ensurepip 失败，未为此修改系统包；改用 Node 安装 `playwright-core@1.56.1` 到被忽略的 `.local/browser/`，驱动本机 Chrome。验收脚本是 `scripts/verify_s3_browser.mjs`，浏览器测试结果见 [s3-browser.json](evidence/s3-browser.json)。

| 检查 | 实际结果 |
| --- | --- |
| 登录与原生页面 | admin 登录成功，看板显示两张图 |
| 整月指标卡 | 页面 245.00；真实图表查询结果 245 |
| 日趋势 | 实际图表请求返回 120、125，与指标卡可对账 |
| 日期筛选 | 在界面打开范围编辑器，改结束日并 Apply，再点击 Apply filters；指标卡 120，趋势仅第一天 120；恢复整月后 245 |
| 调整图表尺寸并保存 | 界面拖动 resize handle，点击 Save，API 读回布局变化 |
| 离开再重开 | 先离开到 welcome，再重开；布局字符串一致，指标卡仍 245 |
| 数据更新与刷新 | 管理员将订单 2 的 paid_amount 从 80 改为 90；重载页面后 255，趋势 120/135 |
| 恢复源数据 | 将订单 2 恢复 80；重载后 245，趋势 120/125 |
| 图表位置拖动换位 | **自动化未通过，待用户手工 review**；不计为通过 |

日期控件的无障碍名称是“订单日期”，而非所显示的日期范围；首次按日期文本定位超时，改用原生 `time-range-trigger` 后成功。补充拖动测试尝试了指针和 HTML5 事件：能进入拖动状态，但未出现有效放置目标，读回顺序未改变；没有把“Save 成功”误认为换位成功，也没有据此判定产品缺陷。随后仅重复尺寸/重开检查，并把当前图表高度恢复到可读布局，证据明确保留手工项。

源更新测试在 finally 中恢复订单 2，避免异常中断留下 90。测试后再次运行 `source_db.py verify`，净收入 245、三表各 4 行、所有拒绝测试通过，原始记录指纹仍与 S2 一致。最终 finish 阶段再次读取订单 2 为 80.00，并检查 LAN HTTP/容器状态，未再改源数据。

截图：

- [整月看板，245](evidence/s3-dashboard.png)
- [原生日期筛选，120](evidence/s3-date-filter.png)
- [源更新后刷新，255](evidence/s3-refresh-255.png)

最终绑定、镜像 ID、浏览器版本和已验证范围见 [s3-final-state.json](evidence/s3-final-state.json)。截图仅含合成业务数据。浏览器临时资料、下载的源码诊断材料和会话页面保存在忽略目录，不提交 Git；没有提交实际密码或认证令牌。

### 7. 现在跟着做什么

打开看板，先核对 245 和两天趋势；按教程把日期限制为第一天，观察两图一起变化，再恢复整月。进入 Edit dashboard，亲手拖动一张图到另一位置、保存并重开，确认这项尚未自动完成的交互，再反馈 review 结果。你也可以另建练习看板，避免改动实验基线。

本步应能回答三个问题：数据集与图表分别保存什么？退款为什么要先汇总？为什么保存布局不会冻结数据？实际操作路线见教程，而不是只阅读验证脚本。

### 8. 停止、恢复与 review 边界

日常停止 Web，不影响业务源和卷：

```bash
sudo docker-compose stop superset
```

恢复已初始化的 Web 和元数据库：

```bash
sudo docker-compose up -d --wait metadata superset
```

元数据库卷和 `.env` 需要保留；不要通过删除卷处理登录或图表错误。runtime 重跑包含数据库迁移和权限同步，日常恢复不需要重跑。完整 browser 阶段会调整尺寸并短暂更改合成订单，适合复现实验；普通看板刷新无需执行它。

用户回复“继续”，确认本步并推进 S4。保留图表拖动换位的手工未验证项，不声称所有原生编辑交互通过。S3 提交仅包含本目录，后面的 Wren 实验没有提前执行；`.codex/` 用户配置未改动。

## S4 操作记录：跨库关系与语义契约

### 1. 目的、前置状态与文件

用户回复“继续”后提交 S3，提交为 `4f2c973`，未 push。S3 图表拖动换位继续作为未验证项，不阻塞独立的跨源查询实验。本步不进入 Wren 或 MCP。

先理解三件事：Trino catalog 提供物理读取入口，SQL 决定如何关联，语义契约解释为什么这样关联。仅配置 connector 不会自动生成可靠业务语义。新增文件为 `trino/`、`sql/trino/orders_enriched_v1.sql`、`semantic/net_revenue.v1.json`、S4 脚本、证据和 [亲手操作教程](S4-WALKTHROUGH.md)。

### 2. 版本、资源与入口

Trino 固定 `trinodb/trino:481`，SQLAlchemy 驱动固定 `trino[sqlalchemy]==0.337.0`。核对依据：[481 容器文档](https://trinodb.github.io/docs.trino.io/481/installation/containers.html)、[MySQL connector](https://trinodb.github.io/docs.trino.io/481/connector/mysql.html)、[PostgreSQL connector](https://trinodb.github.io/docs.trino.io/481/connector/postgresql.html)、[驱动兼容说明](https://pypi.org/project/trino/0.337.0/)。未采用浮动 latest，未升级现有 MySQL/PostgreSQL。

Trino 容器限 2 GiB，JVM 最大堆 1 GiB，单节点查询内存 256 MB、堆预留 256 MB、查询最长运行 60 秒。这只是 4 条订单的实验预算，不代表生产容量。宿主发布 `127.0.0.1:18080`，Superset 内部访问 `trino:8080`；Superset LAN 入口保持 `192.168.2.46:8088`。

两个 catalog 使用 S2 的 `bi_reader`、`crm_reader`。属性文件采用 `${ENV:...}` 引用，Compose 从忽略的 `.env` 注入，未把真实密码写进 catalog。[Trino 环境变量密钥引用](https://trinodb.github.io/docs.trino.io/481/security/secrets.html)

S4 没有配置 Trino 用户认证或部门访问策略；HTTP 用户名只是查询标签。只能作为本机部署实验，不能用来证明普通用户隔离，也不应将 Trino 端口改绑 LAN 后直接供团队使用。部门权限在 S5 单独验证。

### 3. 实际构建与下载故障

在本目录执行：

```bash
sudo docker-compose -f compose.yaml -f host/compose.build-proxy.yaml --profile sources --profile federation build superset
sudo docker-compose --profile sources --profile federation up -d --no-deps --wait superset
```

首次 uv 通过默认 PyPI 通道长时间没有进展，主动中止。验证清华 PyPI 镜像可直连后，增加通用 `PYPI_INDEX_URL` 构建参数（默认官方 PyPI），仅在本机 override 指定镜像；重试 Python 安装约 4.6 秒。Superset Web 已用含 Trino 驱动的镜像重建；数据卷及 S3 原生资产保留。

官方 `docker pull trinodb/trino:481` 下载很慢；尝试镜像代理仍受代理通道影响，中止这两次未完成的拉取。未重启 Docker daemon，未修改宿主全局代理。使用 `scripts/fetch_trino_image.py` 从镜像代理取字节，官方校验基准来自 Docker Hub 的 [481 tag 接口](https://hub.docker.com/v2/repositories/trinodb/trino/tags/481)：

- 官方多架构 index：`sha256:5b5e0a97f599a30a2e23c7c7061e4e4d546a914b4fad2a2a002998140ceb4006`。
- amd64 manifest：`sha256:4b56ba858f31c4420df762015b63b0c0bcf712109d6401356732af43581c9cd3`。
- image config：`sha256:1629098dd4c6234ffab1e179d8a6da20964942a37f392d378b08b85231cd1079`。

镜像代理前端可直连，下载 CDN 直连超时；改为仅 CDN 使用本机代理。大层使用分段下载；每个压缩层核对 manifest 的 SHA-256，解压层核对 config 的 diff_id，再生成 Docker load 归档。归档、分段及令牌均不提交；脚本只保存去敏后的 digest 证据。默认复现仍可直接官方 pull，无需走备用下载脚本。

镜像导入成功。首次容器 API 已 ACTIVE，但 Compose 一直等待健康状态；检查发现健康探针的 YAML/ shell 单引号没有闭合，探针退出 2。改用 YAML 折叠字符串，增加 `sh -n -c` 检查，并仅重建 Trino 容器；没有改动或重建业务数据卷。后续同时核查 API `starting=false` 和 Docker healthy，避免仅看到端口可访问就误判就绪。

### 4. 启动、查询和发布命令

以下命令用于继续执行/复现；具体实测状态以本节后面的结果为准：

```bash
# 官方 pull 已完成时跳过备用下载与 load。
python3 scripts/fetch_trino_image.py
sudo docker load -i .local/trino-image/image.tar
sudo docker-compose --profile sources --profile federation up -d --wait trino

# 只读查询不需要 Docker 权限；完整实验含短暂 CRM 修改，需要部署身份。
python3 scripts/verify_s4.py --query < sql/trino/orders_enriched_v1.sql
sudo python3 scripts/verify_s4.py
python3 scripts/s4_assets.py
node scripts/verify_s4_browser.mjs
```

`verify_s4.py` 保存查询 ID、原始订单粒度结果、合计/部门/行业/日期对账、质量检查与反例。金额从 Trino JSON 按字符串解析，再用 Decimal 对账。缺失和重复维度、空值/负值、超额退款使用查询内 fixture，不写入源表；已有数据库约束仍由 S2 检查重新验证。

唯一的源修改是 A/1 当前行业“软件→零售→软件”，在 finally 中恢复。实验不承诺跨源一致快照；不要并发修改基准源。异常中断后应先检查 CRM 实际值，再按实验记录决定恢复，不能覆盖其他人的有意业务修改。

`net_revenue.v1.json` 记录粒度、复合键、基数、退款时间归属、当前行业、别名与质量规则。发布脚本读取指标表达式并执行质量检查，发现重复客户或错误粒度拒绝发布。契约不是 Wren 原生模型；负责人/审核人仍为空，只作为本地实验，不能声称公司业务已发布。

Superset 新增独立 Trino 连接、`008_orders_enriched_v1` 虚拟数据集和行业图。保留 S3 两张 MySQL 图作为独立基准；只在原看板追加行业图的一行，保留已有布局节点和日期筛选。API 没有被假定具备跨请求事务或 CAS，发布前读回检测已有变化，不能保证消除全部并发窗口。

### 5. 已实际通过的查询与异常用例

Trino 启动后，完整执行 `verify_s4.py`；所有断言通过。详见 [s4-queries.json](evidence/s4-queries.json)，每条 Trino 查询保留 query ID。没有把失败反例发布为数据集。

| 检查 | 实际结果 |
| --- | --- |
| 引擎版本 | `SELECT version()` 为 481 |
| 正确跨库数据集 | 4 行、4 个订单 ID，净收入 245.00 |
| 部门与日期 | A 145.00、B 100.00；10-01 120.00、10-02 125.00 |
| 当前行业 | 软件 120.00、零售 125.00 |
| 错误：直接 JOIN 退款 | 345.00，证明订单实付被重复计算 |
| 错误：客户关联漏部门 | 8 行、490.00，证明同 ID 跨部门放大 |
| 缺失 A/1 客户 fixture | 未知行业 70.00、软件 50.00、零售 125.00，订单收入保留 |
| 重复客户 fixture | 客户复合键检查抛错，拒绝发布 |
| 空/负实付 fixture | 检出 2 条无效记录 |
| 累计超额退款 fixture | 检出订单 1 违规；未把它写进源表 |
| A/1 当前行业改为零售 | 软件 50.00、零售 195.00，总额仍为 245.00 |
| 恢复 A/1 当前行业 | 软件 120.00、零售 125.00 |
| 源数据复核 | S2 的 7 项拒绝检查通过，指纹仍为 `f5f457e68edf6080470a01c093bb4be2cc23d3a08c337af6446ffae446a750f8` |

发布前对真实三表运行 7 类质量检查：订单重复、客户复合键重复、订单必要字段/金额、退款必要字段/金额、客户必要字段、孤儿退款、累计超额退款；均为 0。缺失客户是允许保留为未知行业的规则，不和孤儿退款混为同一类错误。S2 的固定基准核验额外要求已知客户和整套 seed 完全符合预期，不是通用缺失客户处理策略。

### 6. 原生 BI 资产、页面与最终状态

`s4_assets.py` 成功创建以下资产，并通过 API 读回及查询对账：

| 资产 | 实际 ID/定义 |
| --- | --- |
| Trino 数据连接 | 2，`008 Trino federation (source readers)` |
| 跨源虚拟数据集 | 2，`008_orders_enriched_v1`，一行一订单 |
| 指标 | net_revenue，`SUM(net_amount)`，对应 net_revenue@1 |
| 行业图 | 3，`008 按当前行业净收入`，ECharts Bar Chart |
| 看板 | 复用 1；保留 S3 两图及布局，只追加一行行业图 |

资产 ID、SQL SHA-256、质量查询及 API 返回见 [s4-assets.json](evidence/s4-assets.json)。原 S3 图继续读 MySQL，新图经 Trino 跨两库，图表结果一致是本步对账证据之一，不代表两个语义定义会自动同步。

真实 Chrome 验证了登录、三图渲染、日期筛选与离开重开：

- 整月：指标卡 245、趋势 120/125、行业 120/125。
- 截止 `2026-10-02`：指标卡 120、趋势仅 10-01 的 120、行业仅软件 120。
- 恢复整月并离开重开：回到 245、120/125、120/125。

首张截图捕捉到 ECharts 入场动画的中间帧，柱高尚未稳定；核查真实图表响应正确后，在数据断言通过时增加 1.5 秒动画等待，重跑浏览器验证并重新截图。未因此修改指标或源数据。结果见 [s4-browser.json](evidence/s4-browser.json)、[整月截图](evidence/s4-dashboard.png)、[日期筛选截图](evidence/s4-date-filter.png)。

最终状态由 `sudo python3 scripts/finish_s4.py` 重新核对，见 [s4-final-state.json](evidence/s4-final-state.json)。五个容器均 healthy，Trino 仅绑定本机 18080，Superset 保持 LAN 8088；订单 2 实付恢复为 80.00，A/1 行业为软件。实际驱动 `trino 0.337.0` 与 `SQLAlchemy 1.4.54`，Superset 6.1.0。运行内存采样仅用于确认本机资源可容纳，不作为大数据性能结论。

镜像来源及完整性证据见 [s4-image-download.json](evidence/s4-image-download.json)。导入后的 Trino image ID 与官方 config digest 一致。包含新驱动的 Superset image ID 为 `sha256:bf781e00c640a827b0eaabc6d1f49bee6b00191e3cfb0d9b978ddccc23c8361e`；未升级数据库或迁移业务数据。

### 7. 维护方式、亲手练习与停止恢复

现在可先读教程，在 SQL Lab 重现两个错误 JOIN，解释为什么 345、490 都是错的；再操作日期筛选，观察三图对账。行业变更已由部署脚本做过并恢复，普通查看看板不需要管理员修改源表。

本步的数据路径为“Superset→Trino→两个业务源”；正确性的证据是订单粒度、独立固定答案、错误反例、源变更/恢复和页面请求共同一致。若结果不对，先核对退款聚合与客户复合键，再查数据质量、时间范围和当前行业语义，最后检查图表配置。

目前仍是人工触发的“契约＋SQL＋质量检查＋BI 发布”链路，没有变更监听、血缘自动影响分析或 Wren 同步。数据表增加时维护业务域、稳定实体键、允许关联路径、负责人及回归；数据量增加时再测扫描、汇总与查询代价。维护业务语义不等于给 Agent 填更多 schema，暂时也没有必要为三张表引入 OWL/图数据库。

停止 Trino 不删除业务、元数据库或数据卷：

```bash
sudo docker-compose --profile sources --profile federation stop trino
```

恢复原型：

```bash
sudo docker-compose --profile sources --profile federation up -d --wait mysql-source crm-source metadata trino superset
```

此步骤不删除卷；BI 资产在 metadata，业务记录在各源卷，Trino 数据目录主要存运行文件，不保存一份自动同步的业务副本。

**S4 已完成，用户回复“没问题，继续”确认本步并授权提交及进入 W1。** 未验证范围包括部门认证/隔离、跨源原子快照、大数据性能、MCP 和 Wren；S3 图表拖动换位限制仍保留。本步确认并提交后，下一步为 W1：以同一数据基线建立 Wren 新版语义模型及查询实验，不开发新的 Agent。

交付自查：Python AST、Node 语法、Compose 基础/构建 override 校验、JSON 解析与证据一致性、Markdown 本地链接/围栏、全部本步文本的真实密码扫描及空白检查通过；`git diff --check` 通过。最终记录中的浏览器报告与独立浏览器证据一致；只提交过已确认的 S3，S4 仍未提交，`.codex/` 未改动或纳入提交。

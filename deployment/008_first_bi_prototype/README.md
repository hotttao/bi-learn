# 008：跟着实验理解 BI，再让 Codex 接入 MCP

## 当前状态与协作方式

2026-10-17：**S1 已经用户确认并提交（`a0b9333`）。S2 数据源与造数已完成，待 review、未提交：MySQL 与业务 PostgreSQL 均 healthy，三表各 4 行，净收入 245，重复初始化、只读权限及停止/启动持久性检查通过。Superset Web、Trino、MCP 和 Wren 尚未启动。**

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

S1 已于用户确认后提交为 `a0b9333`，没有 push。当前 review 范围是 S2：业务源 Compose 配置、建表与 seed SQL、凭据增量生成、源查询/对账脚本、数据字典和运行证据。先阅读 [DATA-DICTIONARY.md](DATA-DICTIONARY.md)，再看本文末尾 S2 操作记录。用户确认后才提交 S2 并进入 S3 手工 BI。下面保留 S1 当时的操作和 review 状态作为历史记录。

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

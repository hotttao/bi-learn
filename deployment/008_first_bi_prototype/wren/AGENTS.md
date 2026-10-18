# 008 Wren 项目操作入口

先遵循上级 `../AGENTS.md` 与 `../agent.md` 的阶段和 review 边界。使用 `../scripts/wren.sh`，它固定本地 CLI、项目、profile 目录和 strict 配置，不使用全局 wren 配置。

回答业务问题时：读取 `context instructions`，通过 `memory describe` 和 `memory recall` 发现模型/已确认示例，优先使用 net_revenue cube；否则使用模型名写 SQL，先 `dry-plan` 再查询并对账。不得直接命名物理 catalog 表，不能把部署会话用于证明部门隔离。对账后才用 `memory store` 保存已确认示例，不记录令牌、密码或真实敏感数据。

修改模型时先读当前 YAML 和语义版本；`context validate --strict`、`context build`、`memory index` 后使用 `../.local/wren-venv/bin/python ../scripts/verify_w1.py` 执行回归。`context validate --strict` 的含义是将警告视为错误，不等同于 runtime/config.json 中的查询 strict_mode。

当前为本地 W1 实验：三模型、两关系、一 cube，沿用 net_revenue@1。不要未经 review 修改净收入公式、客户复合键或历史语义；不要假定模型构建会更新 Superset。运行命令和复现路径见 `../W1-WALKTHROUGH.md`。

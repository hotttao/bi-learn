# 配置管理

本文档定义 workspace 级共享配置的 schema 规则与变更流程。

配置 schema 由 `idl/config/config.proto` 用 Protobuf 约束，跨服务共享。生成产物在 `hertz_gen/config/`（`media_agent/hertz_gen/config/config.pb.go`）。

> 配置 schema 是契约，不是自由发挥的 YAML。先改 proto，再重新生成，最后改业务代码读取新字段。

---

## 加载链路

```text
config.yaml  ──(protojson)──→  proto.Unmarshal(Config)  ──→  运行时 *config.Config
```

- 源是 YAML（人写、可读）
- 中间走 `protojson`（YAML → JSON → protobuf）
- 终点是 `hertz_gen/config.Config` 结构体，业务代码只消费 Go 结构体，不直接读 YAML key

**禁止**绕过 `Config` 结构体直接用 `viper.GetString` / `yaml.Unmarshal` 到零散 map 读配置。

---

## 生成

配置 proto **不走 hz 工具**，走纯 protoc：

```bash
make pb-config
# 等价于
# protoc --go_out=. --go_opt=module=media_agent \
#     --proto_path=idl idl/config/config.proto
```

| 动作 | 命令 | 产物 |
|------|------|------|
| 改完 `idl/config/config.proto` 后重新生成 | `make pb-config` | `hertz_gen/config/config.pb.go` |

生成代码 🔴 禁止手工编辑。改 schema 必须走 proto → `make pb-config`。

---

## Schema 命名约定

```text
- snake_case
- 治理类 message 第一个字段统一 `bool enabled = 1;`
- 服务名 / app 名走 `app.name`，禁止 `service.name`
```

| 规则 | 正确 | 错误 |
|------|------|------|
| 字段命名 | `max_open_conns` | `maxOpenConns` / `max-open-conns` |
| 治理段开关 | 治理 message 首字段 `bool enabled = 1;` | 用 `disable` / 中间字段当开关 |
| 服务逻辑名 | `app.name` | `service.name` |

> `service.name` 是 `AppClientConfig` 内部指代下游 app 的字段（必须与下游 `app.name` 一致），与本服务自描述的 `app.name` 是两回事，不要混用。

---

## Config 段的组织原则

每个 Config 段按"为何存在"切分，一个职责一个 message：

| 段类型 | 含义 | proto 中的位置 | 示例 |
|--------|------|---------------|------|
| 本服务自描述 | 本服务是谁 | `app`（单例） | `AppConfig` |
| 服务端配置 | 本服务怎么监听 | `server`（单例） | `ServerConfig` |
| 基础设施依赖 | 本服务依赖什么基础设施 | Config 顶层一个 `*Config` 段 | `database` / `redis` / `consul` / `otel` |
| 服务端中间件 | server 注册哪些中间件 | Config 顶层一个 `*Config` 段 | `recovery` / `cors` / `rate_limit` |
| 运维端点 | 暴露哪些运维路径 | Config 顶层一个 `*Config` 段 | `pprof` / `health` |
| 客户端（调下游） | 本服务调用哪些下游 | `client` map 每项 | `map<string, AppClientConfig>` |

**单例 vs map**：
- 本服务自描述、服务端、基础设施、中间件、运维端点 = 单例段（Config 顶层一个字段）
- 下游 client = map，key = 下游 `app.name`

---

## 生命周期规则

| 变更场景 | 正确做法 | 错误做法 |
|---------|---------|---------|
| 系统新增一个微服务 | 复制本 `Config` 模板，本服务 `app`/`server` 段保持单例 | 新服务去改别的服务的 config |
| 本服务调用某下游服务 | 在 `client` map 里加一项 `key=<下游 app 名>` | 在 Config 顶层加一个独立 client 字段 |
| 不再调用某下游 | 删 `client` map 里对应一项 | 留空 enabled=false 凑数 |
| 引入新基础设施依赖 | 在 Config 顶层加一个 `*Config` 段（如 `nacos` / `kafka`） | 塞进现有段当子字段 |
| 关闭某能力 | 该段 `enabled = false`，未启用段不参与初始化 | 删掉整段 / 注释掉 |

---

## 新增 / 修改字段的流程

```text
1. 改 idl/config/config.proto
   - 新字段用 snake_case
   - 治理段首字段保持 bool enabled = 1
   - 留好注释（含义、单位、默认值）
2. make pb-config                      → 重新生成 hertz_gen/config/config.pb.go
3. 更新 config.yaml 模板示例值
4. 业务代码读取新字段
5. gofmt + go build ./... 验证
```

| 想要的变更 | 正确路径 | 错误路径 |
|-----------|----------|----------|
| 加一个治理开关 | proto 加 `bool enabled = 1;` + 业务字段 → `make pb-config` | ❌ 在 yaml 里加个 key 业务直接读 |
| 新增基础设施段 | Config 顶层加 `XxxConfig xxx = N;` + 定义 message → `make pb-config` | ❌ 复用现有段塞字段 |
| 新增下游 client | `client` map 加项（yaml 配 key + `service_name`） | ❌ Config 顶层加 client 字段 |
| 调字段默认值 | 改 yaml 模板 / 业务读取处的兜底逻辑 | ❌ 改生成代码里的默认值 |

---

## enabled 统一语义

所有治理类 / 基础设施类段都以 `bool enabled = 1;` 开头，语义统一：

- `enabled = true` → 该段参与初始化，对应组件被装配
- `enabled = false` → 该段被跳过，对应组件不初始化，**不报错**
- 段缺失 → 等价 `enabled = false`

初始化代码必须按 `enabled` 短路，未启用段不得因为字段为空而 panic。

---

## 治理类配置复用

服务端中间件段与客户端 client 段复用同一组治理 message，避免重复定义：

| 治理 message | 服务端用 | 客户端用 |
|-------------|---------|---------|
| `TimeoutConfig` | `server.timeout` | `client.<k>.timeout` |
| `RetryConfig` | — | `client.<k>.retry` |
| `CircuitBreakerConfig` | `circuit_breaker` | `client.<k>.circuit_breaker` |
| `RateLimitConfig` | `rate_limit` | `client.<k>.rate_limit` |

新增治理能力时，优先复用已有 message，而不是为服务端 / 客户端各定义一份。

`RateLimitConfig.resource_pattern` 资源名规则：
- 服务端：`<app>:<method>:<path>`
- 客户端：`<caller_app>-><callee_app>`
- 留空走默认提取规则

---

## Review 清单

- 字段命名是 snake_case？
- 新治理段首字段是 `bool enabled = 1;`？
- 服务逻辑名走 `app.name`，没用 `service.name`？
- 新基础设施段加在 Config 顶层，新下游加在 `client` map？
- 改完 proto 跑了 `make pb-config`？
- yaml 模板与 schema 同步？
- 未启用段（`enabled=false` / 缺失）在初始化时被正确跳过？
- 治理能力复用了已有 message，没重复定义？
- 业务代码只读 Go 结构体，没绕过去直接读 YAML？

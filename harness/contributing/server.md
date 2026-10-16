# Server 端微服务治理指南

> 适用项目：使用 CloudWeGo Hertz 作为服务端框架的 Go 微服务项目。
> 适用对象：负责生成、修改、审查服务端启动、路由、中间件、服务注册、可观测性与服务端治理代码的 Coding Agent。
> 核心目标：Hertz 生成代码负责 HTTP 路由与 handler 框架结构，`hertz_infra/serverhertz` 负责提供服务端治理能力（server options / middleware / 服务注册），`main.go` 只做启动编排，业务层无感。

---

## 1. 设计目标

项目使用 CloudWeGo Hertz 作为服务端框架，并使用 `hz` 生成服务端代码。Hertz 生成代码已经负责 `main.go` / `router.go` / `router_gen.go` 基础形态、`biz/router/**` 路由注册、`biz/handler/**` handler 骨架、`hertz_gen/model/**` API DTO。

因此，服务端治理能力不要散落在 `main.go`、`router.go`、`biz/router/**` 或业务 handler 中，统一收口到：

```text
hertz_infra/serverhertz/
```

服务端治理按**能力收口与启动编排分离**组织：

- `hertz_infra/serverhertz` 提供**治理能力**——把配置转换成 Hertz server options 与全局 middleware，组合成 `ServerSuite`，并集成服务注册、tracing、metrics、recovery、CORS、request id、access log、限流、熔断、健康检查、pprof；它不关心业务用例，只关心"如何把治理装到 server 上"。
- `main.go` 负责**启动编排**——按顺序调用 `serverhertz` 的入口函数（`NewServerSuite` / `InitTracing` / `InitMetrics` / `NewServer` / `RegisterService`），不拼具体 options / middleware。
- `biz/handler` / `biz/service` / `biz/router` **无感**——不 import Hertz server 治理组件、注册中心、Sentinel / OTel / Prometheus SDK。

最终启动关系：

```text
main.go
  ↓
config.NewLoader()                          # 创建 Loader（.env + APP_ENV → conf/<env>.yaml）
  ↓
serverhertz.NewServerSuite(loader)          # suite 收 loader 不收 cfg
  ↓                                           # 可热更新能力组件在 suite 内部 loader.Subscribe 自行订阅
loader.Attach(suite.KVSource())             # 可选：挂载 Consul KV 配置中心源（nil 无操作）
  ↓
loader.Watch()                              # 启动文件 + KV 热更新 watch
defer loader.Close()                        # 统一释放 watcher 资源
  ↓
serverhertz.InitTracing / InitMetrics       # 进程级初始化
  ↓
suite.NewServer(ctx)                        # 创建 server + 挂全局 middleware，初始化失败返回 error
  ↓
register generated routes                   # hz 生成的业务路由
  ↓
serverhertz.RegisterService(ctx, suite)     # 服务注册
  ↓
h.Spin()
```

**配置订阅职责切分**：

- ServerSuite 持有 `*config.Loader`，不持有 cfg 快照——`Config()` 转发 `loader.Current()`；
- 启动期固化的能力（server options / middleware chain / registry）在 `NewServerSuite` / `NewServer` 内部一次性消费 `loader.Current()`，不订阅；
- 可热更新能力组件（限流规则 / 熔断规则）在 `NewServerSuite` 内部 `loader.Subscribe` 自己订阅，**ServerSuite 不做桥接**；
- 业务层直接 `import config "media_agent/hertz_infra/config"` 自己 `loader.Subscribe`，不走 ServerSuite。

---

## 2. 总体目录结构

```text
media_agent/                                    # workspace 根（go.work）
├── hertz_gen/                                  # 跨服务共享的 hz 生成根（独立 module，禁止手改）
│   ├── model/                                  # hz update 生成 API DTO
│   └── <app>/                                  # hz client 生成下游 stub
│
├── hertz_infra/                                # 跨服务共享的 hertz 治理 + 基础设施（独立 module）
│   ├── config/                                 # 配置加载层（详见 §4.3）
│   │   ├── loader.go                           # Loader：管理多 Source，统一 reload + Subscribe
│   │   ├── source.go                           # Source 接口：Get / Watch / Close / Priority
│   │   ├── file_source.go                      # 文件源：fsnotify watch + expandEnv + yaml.v3
│   │   ├── load.go                             # Load() / LoadFromPath() / godotenv + APP_ENV 路径解析
│   │   ├── expand.go                           # shell 风格 ${VAR:-default}（isEnvName 过滤模板变量）
│   │   └── parse.go                            # deepMerge + protojson → *configpb.Config
│   ├── serverhertz/                            # Hertz 服务端治理能力唯一收口
│   │   ├── suite.go                            # ServerSuite：统一组合 server options / middleware
│   │   ├── server.go                           # NewServer / NewServerSuite
│   │   ├── registry.go                         # 服务注册 / 反注册
│   │   ├── registry_<backend>.go               # 可选：新增注册中心后端（如 registry_nacos.go）
│   │   ├── consul_kv.go                        # Consul KV 配置中心 Source 实现（priority=20）
│   │   ├── timeout.go                          # 服务端读写 / idle / 请求超时
│   │   ├── recovery.go                         # panic recovery
│   │   ├── requestid.go                        # request id
│   │   ├── accesslog.go                        # access log
│   │   ├── cors.go                             # CORS
│   │   ├── ratelimit.go                        # 服务端限流（限流只在 server 侧）
│   │   ├── tracing.go                          # 服务端链路追踪
│   │   ├── metrics.go                          # 服务端指标
│   │   ├── pprof.go                            # pprof / debug，可选
│   │   ├── health.go                           # health / readiness / liveness
│   │   └── middleware.go                       # 中间件顺序约束
│   ├── clienthertz/                            # Hertz 客户端治理能力层（详见 client-cn.md）
│   ├── cache/                                  # 跨服务共享的 cache 抽象与实现
│   └── globalhertz/                           # 进程级全局状态：otel provider / hlog logger / sentinel
│
└── <app>/                                      # 单个 hertz 微服务（module: media_agent/<app>）
    ├── main.go                                 # Hertz 程序入口，保持轻薄
    ├── router.go                               # 用户自定义路由，可选
    ├── router_gen.go                           # hz 生成路由入口，禁止手改
    │
    ├── biz/
    │   ├── handler/                            # hz 生成或 hz 管理的 handler
    │   │   └── <service>/
    │   ├── router/                             # hz 生成或 hz 管理的路由注册
    │   │   ├── register.go
    │   │   └── <service>/
    │   ├── middleware/                         # 业务无关但和请求上下文相关的中间件，可选
    │   ├── domain/                             # 领域核心
    │   ├── policy/                             # 业务规则
    │   ├── service/                            # use-case service
    │   ├── workflow/                           # 跨用例编排
    │   └── dal/                                # 数据访问
    │
    ├── conf/
    │   ├── local.yaml
    │   ├── dev.yaml
    │   └── prod.yaml
    │
    └── idl/                                    # 本服务专属 IDL（共享 IDL 在 workspace 根 idl/）
```

> 跨服务共享的代码生成根 `hertz_gen/`、配置加载层 `hertz_infra/config`、Hertz 治理层 `hertz_infra/serverhertz` / `hertz_infra/clienthertz`、缓存抽象 `hertz_infra/cache`、全局状态 `hertz_infra/globalhertz` 都不在单个微服务目录内，而是 workspace 根的共享 module，通过 go.work 引用。本服务 `biz/` 下不再保留 `config` / `infra` / `cache` / `globalhertz` 目录。

---

## 3. 分层职责

| 层 | 路径 | 职责 | 是否手写 |
|---|---|---|---|
| Hertz 生成层 | `router_gen.go`、`biz/router/**`、`biz/handler/**`、`hertz_gen/model/**` | 路由、handler 骨架、API DTO | 部分生成，部分可补业务调用 |
| 服务端治理层 | `hertz_infra/serverhertz/**` | server options、middleware、服务注册、Tracing、Metrics、限流、恢复、健康检查 | 是 |
| 业务层 | `biz/service/**`、`biz/workflow/**` | 业务用例、业务编排 | 是 |
| 启动层 | `main.go` | 加载配置、初始化基础设施、启动 Hertz server | 是，但保持轻薄 |

核心原则：

- `hertz_infra/serverhertz` 负责"服务端治理能力"；
- `biz/handler` 只负责"HTTP 入参 / 出参 + 调用 service / workflow"；
- `biz/service` 不感知 Hertz server、中间件、注册中心；
- `main.go` 只做启动编排，不直接拼复杂 options / middleware。

---

## 4. 配置文件约定

服务端治理配置全部放在 `conf/<env>.yaml` 顶级块，每项治理能力一个独立顶级块，由 `hertz_infra/serverhertz` 统一消费。

> **命名约定**：
>
> - 字段名与项目其它配置块对齐，启用字段统一用 `enabled`（不是 `enable`）。
> - 当前服务名用 `app.name`（不是 `service.name`），与 `app.env` 同属 `app:` 顶级块。

### 4.1 治理能力与配置段对应表

| 治理能力 | YAML 顶级块 | 能力层文件 | 接入方式 |
|---|---|---|---|
| 服务基础参数（监听地址 / 超时 / body 限制） | `app`、`server` | `timeout.go` | `ServerSuite.Options()` |
| 服务注册（server-side） | `consul` | `registry.go`（+ 可选 `registry_<backend>.go`） | `RegisterService(ctx, suite)` |
| 配置中心（Consul KV 动态配置） | `consul_kv` | `consul_kv.go` | `ServerSuite.KVSource()` → `loader.Attach()` |
| panic recovery | `recovery` | `recovery.go` | `ServerSuite.Middlewares()` |
| request id | `request_id` | `requestid.go` | `ServerSuite.Middlewares()` |
| access log | `access_log` | `accesslog.go` | `ServerSuite.Middlewares()` |
| CORS | `cors` | `cors.go` | `ServerSuite.Middlewares()` |
| 服务端限流 | `rate_limit` | `ratelimit.go` | `ServerSuite.Middlewares()` |
| Internal JWT 认证 | `internal_jwt` | `serverhertz/jwt/` | `ServerSuite.Middlewares(ctx)` |
| 链路追踪 | `otel` | `tracing.go` | `ServerSuite.Middlewares()` + `InitTracing()` |
| 指标采集 | `prometheus` | `metrics.go` | `ServerSuite.Middlewares()` + `InitMetrics()` + `RegisterMetricsRoute()` |
| pprof / debug | `pprof` | `pprof.go` | `RegisterPProfRoutes()` |
| 健康检查 | `health` | `health.go` | `RegisterHealthRoutes()` |

规则：

- 每个治理能力必须有独立的 YAML 顶级块和 `enabled` 字段；
- `consul:` 块只服务本服务注册（server-side），不要混入客户端发现字段；客户端发现用独立的 `consul_discovery:` 块，详见 [`contributing/client-cn.md`](client-cn.md) §5.1；
- `consul_kv:` 块只服务动态配置中心（KV watch + deep-merge），与服务注册 (`consul:`) 物理隔离，不混字段；
- 新增注册中心后端（如 Nacos）需新增独立顶级块（如 `nacos:`），不通过 `type` 字段区分；
- 业务代码不直接读取这些治理配置；
- 配置加载由 `hertz_infra/config` 统一负责，`serverhertz.NewServerSuite(loader)` 只持有 `*config.Loader`。

### 4.2 配置 struct 结构

`*configpb.Config`（由 `idl/config/config.proto` 生成，产物在 `hertz_gen/config/config.pb.go`）顶层字段与上表 YAML 顶级块一一对应：

```text
Config
├── App            AppConfig            (app.name / app.env / app.version / app.address / app.advertise_address)
├── Server         ServerConfig         (read_timeout / write_timeout / idle_timeout / max_request_body_size / enable_print_route)
├── Consul         ConsulConfig         (server-side 服务注册)
├── ConsulKv       ConsulKVConfig       (Consul KV 配置中心：enabled / address / data_id)
├── Otel           OtelConfig           (进程级 tracing provider)
├── Prometheus     PrometheusConfig     (进程级 metrics exporter)
├── AccessLog      AccessLogConfig
├── Recovery       RecoveryConfig
├── RequestID      RequestIDConfig
├── CORS           CORSConfig
├── RateLimit      RateLimitConfig      (服务端限流；熔断在 client.<app>.circuit_breaker)
├── PProf          PProfConfig
└── Health         HealthConfig
```

业务代码通过 `config.Config`（`hertz_infra/config` 包中的 `type Config = configpb.Config` 别名）引用配置类型，不直接 import `hertz_gen/config`。

### 4.3 配置加载层

配置加载层的架构规则（Source 接口、Loader API、加载链、订阅职责切分、依赖规则）见 [`contributing/arch.md`](arch.md) 「配置加载层」章节。本节只列 server 治理相关的使用约束：

- `main.go` 调用 `config.NewLoader()` 创建 Loader，`serverhertz.NewServerSuite(loader)` 收 loader 不收 cfg；
- ServerSuite 持有 `*config.Loader`，`Config()` 转发 `loader.Current()`；
- 启动期固化能力（server options / middleware chain / registry）在 `NewServerSuite` / `NewServer` 内部一次性消费 `loader.Current()`，不订阅；
- 可热更新能力组件（限流规则 / 熔断规则）在 `NewServerSuite` 内部 `loader.Subscribe` 自行订阅，**ServerSuite 不做桥接**；
- `loader.Attach(suite.KVSource())` 挂载可选的 Consul KV 源（`enabled=false` 时返回 `nil`，`Attach(nil)` 无操作）。

---

## 5. serverhertz 包设计

`hertz_infra/serverhertz` 是 Hertz 服务端治理能力唯一收口。它允许 import `cloudwego/hertz`、`hertz-contrib`、`sentinel-golang`、`go.opentelemetry.io/otel`；不允许包含业务逻辑。

### 5.1 ServerSuite 装配面

`suite.go` 定义 `ServerSuite`，持有 `*config.Loader`（不持有 cfg 快照），是治理能力接入 server 的唯一装配点：

```text
ServerSuite
├── Loader() *config.Loader               # 暴露底层 loader（仅供 serverhertz 内部组件订阅用）
├── Config() *configpb.Config             # 转发 loader.Current()（启动期一次性消费）
├── Options() []config.Option              # 组装 server option（host:port / timeout / body limit）
├── KVSource() config.Source               # Consul KV 配置中心 Source（enabled=false 时返回 nil）
└── 启动期固化的能力字段（registrar / registryInfo 等）
```

`server.go` 提供两个入口：

- `NewServerSuite(loader *config.Loader) *ServerSuite`：loader → `ServerSuite`，启动期固化能力在此一次性消费 `loader.Current()`，可热更新能力组件在此自行 `loader.Subscribe`；
- `suite.NewServer(ctx) (*server.Hertz, error)`：创建 server + 挂全局 middleware；Internal JWT 首次加载 JWKS 失败时拒绝启动。

**配置订阅职责切分**（核心原则）：

| 能力类型 | 订阅方式 | 原因 |
|---|---|---|
| 启动期固化（server options / middleware chain / registry） | 不订阅，一次性消费 `loader.Current()` | Hertz server 启动后不可变 |
| 可热更新能力组件（限流规则 / 熔断规则） | 在 `NewServerSuite` 内部 `loader.Subscribe` 自己订阅 | 组件自己最清楚哪些字段能热加载 |
| 业务配置（开关 / 阈值 / 下游 URL） | 业务层直接 `loader.Subscribe` | 单一职责，不走 ServerSuite 桥接 |

`KVSource()` 是 ServerSuite 暴露给配置加载层的工厂方法：当 `consul_kv.enabled=true` 时构造 `consulKVSource`（实现 `config.Source` 接口，`priority=20`），否则返回 `nil`。`main.go` 将其传给 `loader.Attach()`，`Attach(nil)` 无操作，从而实现"配置中心是可选 suite 功能，没启用也能用纯本地文件配置"。

规则：

- `Options()` 只组装，不写具体能力实现；
- 每个治理能力拆到独立文件（§4.1 已列），由 `ServerSuite` 调用；
- 新增可热更新能力时，在 `NewServerSuite` 内部 `loader.Subscribe` 订阅，**不通过 ServerSuite 桥接**；
- `main.go` 不直接拼接这些 options / middleware。

### 5.2 全局 middleware 顺序

推荐顺序（由外到内），在 `middleware.go` 中定义：

```text
recovery → request_id → tracing → metrics → access_log → rate_limit → cors → internal_jwt → handler
```

> 治理职责对称约定：**限流只在 server 侧**（`serverhertz/ratelimit`），**熔断只在 client 侧**
> （`clienthertz/circuitbreaker`）。故 server 中间件链无 circuit_breaker——熔断保护的是"调下游"，
> 归 clienthertz。

新增 middleware 时按此顺序插入，并在 `middleware.go` 更新顺序说明。

---

## 6. 注册 / 发现规则

服务注册（server-side）和服务发现（client-side）是**两个独立的关注点**，必须在 YAML 和代码里**物理隔离**。

| 关注点 | 顶级 YAML 块 | 字段范围 | 消费方 |
|--------|--------------|----------|--------|
| **服务注册**（server-side） | `consul:` | `enabled` / `address` / `service_name` / `service_address` / `service_port` / `tags` / `health_check_path` / `check_interval` / `check_timeout` / `deregister_critical_after` / `metadata` | `hertz_infra/serverhertz`（本文 §5.2 `registry.go`） |
| **服务发现**（client-side） | `consul_discovery:` | `enabled` / `address` / `refresh_interval` | `hertz_infra/clienthertz`（详见 [`contributing/client-cn.md`](client-cn.md) §5.1） |

规则：

- **不要把服务注册字段（`service_name` / `service_address` 等）混入 `consul_discovery` 块**——后者只服务客户端发现；
- **不要把服务发现字段（`refresh_interval` 等）混入 `consul` 块**——后者只服务本服务注册；
- 服务注册发生在启动阶段（由 `serverhertz.RegisterService` 在 main 启动阶段完成）；
- 服务反注册发生在退出阶段（`defer registryHandle.Deregister(ctx)`）；
- 服务发现 client 应包装在稳定接口之后；
- 业务层（`service` / `workflow` / `handler`）**禁止 import** Consul 包；
- 健康检查端点应保持在 HTTP / 基础设施边界（`serverhertz/health.go`）；
- 新增注册中心后端需新增独立顶级块（如 `nacos:`）与独立 `registry_<backend>.go`，不通过 `type` 字段区分。

---

## 7. 可观测性规则

可观测性包括日志、追踪、指标、监控。

规则：

- 请求级日志属于 middleware（`serverhertz/accesslog.go`）；
- 业务事件日志属于 service / workflow；
- domain 不应记录日志；
- 具体的日志 / 追踪 SDK 留在 `hertz_infra/globalhertz`（全局状态）与 `hertz_infra/serverhertz`（server 实装）；
- OpenTelemetry 集成必须隐藏在可观测性抽象之后；
- Trace ID 与 request ID 应通过 `context.Context` 传播；
- 不要把 `*app.RequestContext` 传出 Hertz 边界；
- metrics route 标签必须使用模板路由，不要直接使用原始 path，避免高基数；
- 不在业务代码中散落 Prometheus 埋点；
- `/metrics` 是否暴露由配置控制。

---

## 8. main.go 启动方式

`main.go` 只做启动编排，调用 `config` 与 `serverhertz` 的入口函数：

1. `config.NewLoader()`（或 `NewLoaderFromPath(path)`）创建 `Loader`，内部完成 `.env` + `APP_ENV` → `conf/<env>.yaml` 加载；
2. `serverhertz.NewServerSuite(loader)` 构造 `ServerSuite`——suite 收 loader 不收 cfg，可热更新能力组件在 suite 内部自行 `loader.Subscribe`；
3. `loader.Attach(suite.KVSource())` 可选挂载 Consul KV 配置中心源（`enabled=false` 时返回 `nil`，`Attach(nil)` 无操作）；
4. `loader.Watch()` 启动文件 + KV 热更新 watch；
5. `defer loader.Close()` 统一释放 watcher 资源（**必须 defer**，否则 watch goroutine 与 HTTP 连接泄漏）；
6. `serverhertz.InitTracing(ctx, suite.Tracing)` 初始化进程级 tracing provider；
7. `serverhertz.InitMetrics(suite.Metrics)` 初始化进程级 metrics exporter；
8. `suite.NewServer(ctx)` 创建 Hertz server 并挂载全局 middleware（启动期一次性消费 `loader.Current()`，初始化失败返回 error）；
9. 注册生成的业务路由（`register(h)`）；
10. `serverhertz.RegisterHealthRoutes` / `RegisterPProfRoutes` / `RegisterMetricsRoute` 注册基础设施路由；
11. `serverhertz.RegisterService(ctx, suite)` 注册服务，`defer` 反注册；
12. `h.Spin()` 启动，`<-ctx.Done()` 后 graceful shutdown。

`main.go` 禁止出现：

```text
consulregistry.NewConsulRegister(...)
consulapi.NewClient(...)                   # 配置中心 Consul 客户端，由 serverhertz/consul_kv.go 构造
sentinel.NewServerMiddleware(...)
otel.NewTracerProvider(...)
prometheus.MustRegister(...)
server.WithReadTimeout(...)
h.Use(customRecovery)
yaml.Unmarshal(...)                         # 配置加载收口在 hertz_infra/config
fsnotify.NewWatcher(...)                    # 文件 watch 收口在 hertz_infra/config/file_source.go
```

这些都应该收口在 `hertz_infra/serverhertz` 或 `hertz_infra/config`。

---

## 9. Handler / Router 使用规则

### 9.1 handler 规则

`biz/handler/**` 只做：

1. 绑定请求；
2. 基础参数校验；
3. DTO 转 command；
4. 调用 service / workflow；
5. command result 转 response；
6. 返回 HTTP 响应。

handler 不允许：

- 注册服务；
- 初始化 tracing / metrics；
- 手写 access log / recovery / 限流；
- 直接操作注册中心；
- 直接 import Sentinel / OpenTelemetry / Prometheus SDK。

### 9.2 router 规则

`biz/router/**` 由 Hertz 管理或生成。

router 只做：

- 路由注册；
- route group middleware 绑定；
- 路径到 handler 的映射。

router 不允许：

- 写业务逻辑；
- 初始化治理组件；
- 注册服务；
- 操作数据库；
- 调用外部服务。

---

## 10. 新增一个服务端治理能力

1. 在 `conf/<env>.yaml` 增加新能力顶级块（如 `auth_context:`），含 `enabled` 字段；
2. 在 `idl/config/config.proto` 的 `Config` message 增加对应字段，`protoc` 重新生成 `hertz_gen/config/config.pb.go`；
3. 新增 `hertz_infra/serverhertz/<ability>.go`，定义 `Config` struct 与构造函数（option 或 middleware）；
4. 在 `ServerSuite` 增加字段；
5. 在 `NewServerSuite(cfg)` 中完成配置转换；
6. 在 `Options()`（若为 option）或 `Middlewares()`（若为 middleware）中接入；
7. 如涉及顺序，更新 `middleware.go`；
8. 补充单元测试（覆盖 `enabled=false` no-op、`enabled=true` middleware 挂载、配置缺失、middleware 顺序）；
9. 运行 `gofmt` + `go test ./...`。

---

## 11. 新增服务注册后端

以新增 Nacos 为例。注意：`consul:` 块专用于 Consul，新增 Nacos 需要**新增独立顶级配置块**，不再通过 `type` 字段区分。

1. 在 `conf/<env>.yaml` 新增独立顶级块（如 `nacos:`），并在 `Config` / `ServerSuite` 增加对应字段；
2. 在 `NewServerSuite(cfg)` 中完成新配置块转换；
3. 新增 `registry_<backend>.go`（如 `registry_nacos.go`），实现 `register<Backend>(ctx, suite) (RegistryHandle, error)`；
4. 在 `registry.go` 的 `RegisterService` 中按独立配置块的存在性选择后端；
5. 实现 `Deregister`；
6. 测试 `enabled=false`、注册成功、注册失败、反注册失败；
7. `main.go` 只调用 `RegisterService`，不感知具体注册中心；
8. 运行 `gofmt` + `go test ./...`。

规则：

- 每种注册中心一个文件；
- 不要把 Consul / Nacos / Kubernetes 注册逻辑堆在一个大函数里；
- `main.go` 不感知具体注册中心类型。

---

## 12. 测试规范

### 12.1 serverhertz 单元测试

`serverhertz` 至少测试：

- `NewServerSuite(cfg)` 配置转换正确；
- `Options()` 能根据配置返回 server options；
- `Middlewares()` 顺序稳定；
- 每个 middleware `enabled=false` 时 no-op；
- 服务注册关闭时返回 noop handle；
- Consul 启用时返回真实的 `RegistryHandle`（含正确的 `Deregister` 实现）；
- health / pprof / metrics route 在 `enabled=false` 时不注册。

### 12.2 handler 测试

handler 测试不需要启动真实注册中心、真实 OTel、真实 Prometheus。

建议：

- handler 单测只测试请求绑定和响应转换；
- service mock 注入 handler；
- serverhertz middleware 单独测试；
- 集成测试再启动完整 Hertz server。

---

## 13. 禁止事项

1. 在 `main.go` 中直接写复杂 Hertz server options；
2. 在 `main.go` 中直接创建 Consul / Nacos 注册器；
3. 在 `main.go` 中直接拼接一堆 middleware；
4. 在 `biz/handler/**` 中写服务注册、tracing、metrics、recovery、限流逻辑；
5. 在 `biz/router/**` 中写服务治理逻辑；
6. 在 `biz/service/**` 中 import Hertz server、hertz-contrib、Sentinel、OpenTelemetry、Prometheus SDK；
7. 把所有治理代码堆在 `serverhertz/init.go`；
8. 新增治理能力但不接入 `ServerSuite`；
9. 新增 middleware 但不更新 middleware 顺序说明；
10. 服务注册失败后继续启动服务；
11. 退出时不反注册服务；
12. 生产环境默认开启无鉴权 pprof；
13. metrics 使用原始 URL path 作为 label，导致高基数；
14. access log 默认打印 request body / response body；
15. 在业务代码中直接操作治理配置；
16. 把服务注册字段（`service_name` 等）混入 `consul_discovery` 块；
17. 把服务发现字段（`refresh_interval` 等）混入 `consul` 块；
18. 用 `enable`（不带 `d`）作为启用字段名——必须用 `enabled`；
19. 用 `service.name` 表示当前服务名——必须用 `app.name`；
20. 保留 `biz/config/` 薄壳——配置加载统一收口在 `hertz_infra/config`，业务层直接 import；
21. biz 层 import `consul/api`、`fsnotify`、`yaml.v3`、`koanf/viper`——这些依赖只允许出现在 `hertz_infra/config` 与 `hertz_infra/serverhertz`；
22. 调用 `loader.Watch()` 后不 `defer loader.Close()`——会导致 watch goroutine 与 HTTP 连接泄漏；
23. 用 `config_center.backend` 字符串字段标识配置中心后端——后端由 proto 类型名锁定（`ConsulKVConfig`），新增后端新增独立顶级块与 proto message；
24. 在 `Loader` 之外实现配置热更新逻辑——文件 watch 与 KV blocking-query 都收口在 `Source` 实现内；
25. `NewServerSuite` 收 cfg 而非 loader——suite 必须持有 loader 才能让组件自行订阅；
26. 在 ServerSuite 上加 `Subscribe` 桥接层转发配置变更——可热更新能力组件在 `NewServerSuite` 内部直接 `loader.Subscribe`，业务层也直接订阅，ServerSuite 不做桥接。

---

## 14. 修改清单

### 14.1 新增服务端治理能力时

- [ ] 更新 `conf/<env>.yaml`（新增顶级块，含 `enabled`）；
- [ ] 更新 `idl/config/config.proto` 的 `Config` message，`protoc` 重新生成 `hertz_gen/config/config.pb.go`；
- [ ] 新增 `hertz_infra/serverhertz/<ability>.go`；
- [ ] 在 `ServerSuite` 增加字段；
- [ ] 在 `NewServerSuite(cfg)` 中完成配置转换；
- [ ] 在 `Options()` 或 `Middlewares()` 中接入；
- [ ] 如涉及顺序，更新 `middleware.go`；
- [ ] 补充单元测试；
- [ ] 运行 `gofmt` + `go test ./...`。

### 14.2 新增服务注册后端时

- [ ] 在 `Config` / `ServerSuite` 增加新的顶级配置块及其独立 Config（如 `Nacos NacosConfig`）；
- [ ] 在 `NewServerSuite` 中完成新配置块的转换；
- [ ] 新增 `registry_<backend>.go`（如 `registry_nacos.go`）；
- [ ] 在 `RegisterService` 中按独立配置块的存在性选择后端；
- [ ] 实现 `Deregister`；
- [ ] 测试 `enabled=false`、注册成功、注册失败、反注册失败；
- [ ] `main.go` 只调用 `RegisterService`，不感知具体注册中心；
- [ ] 运行 `gofmt` + `go test ./...`。

### 14.3 修改 main.go 时

- [ ] `main.go` 只做启动编排；
- [ ] `main.go` 不直接创建具体治理组件；
- [ ] `main.go` 不直接拼 middleware；
- [ ] `main.go` 调用 `config.NewLoader()` / `NewLoaderFromPath(path)` 加载配置；
- [ ] `main.go` 调用 `serverhertz.NewServerSuite(loader)`——收 loader 不收 cfg；
- [ ] `main.go` 调用 `loader.Attach(suite.KVSource())` 挂载可选的 KV 源；
- [ ] `main.go` 调用 `loader.Watch()` 启动热更新；
- [ ] `main.go` `defer loader.Close()` 释放 watcher 资源；
- [ ] `main.go` 调用 `suite.NewServer(ctx)` 并处理初始化错误；
- [ ] `main.go` 调用 `serverhertz.RegisterService(ctx, suite)`；
- [ ] `main.go` 在退出时反注册服务；
- [ ] `main.go` 支持 graceful shutdown。

### 14.4 字段命名统一检查

- [ ] 启用字段用 `enabled`（不是 `enable`）；
- [ ] 当前服务名用 `app.name`（不是 `service.name`）；
- [ ] `consul:` 与 `consul_discovery:` 物理隔离，不混字段；
- [ ] `consul:`（服务注册）与 `consul_kv:`（配置中心）物理隔离，不混字段；
- [ ] 配置中心后端由 proto 类型名锁定（`ConsulKVConfig`），不用 `backend` 字符串字段区分。

---

## 15. 与 clienthertz 的关系

`serverhertz` 和 `clienthertz` 是对称的两个治理收口：

```text
hertz_infra/serverhertz
  负责当前服务作为 server 时的治理能力

hertz_infra/clienthertz
  负责当前服务调用下游服务时的治理能力
```

二者不要混用。本表是"能力 → 收口包"的快速参考，命名约定、YAML 块命名、字段命名等顶层规则以 [`contributing/architecture-cn.md`](architecture-cn.md) 的层边界规则为准。

| 场景 | 应放位置 |
|---|---|
| 当前服务注册到 Consul / Nacos | `serverhertz/registry.go`（YAML 顶级块 `consul:`） |
| 当前服务发现下游服务 | `clienthertz/discovery.go`（YAML 顶级块 `consul_discovery:`） |
| 当前服务从 Consul KV 拉取动态配置 | `serverhertz/consul_kv.go`（YAML 顶级块 `consul_kv:`，实现 `config.Source` 接口） |
| 配置加载与热更新编排 | `hertz_infra/config/loader.go`（不感知具体 Source 后端） |
| 当前服务接收请求的限流 | `serverhertz/ratelimit.go` |
| 当前服务调用下游的限流 | `clienthertz/ratelimit.go` |
| 当前服务接收请求的 tracing middleware | `serverhertz/tracing.go` |
| 当前服务调用下游的 tracing middleware | `clienthertz/tracing.go` |
| 当前服务暴露 `/metrics` | `serverhertz/metrics.go` |
| 当前服务记录下游调用 metrics | `clienthertz/metrics.go` |

---

## 16. 一句话原则

```text
Hertz 生成代码负责"接收请求和路由到 handler"，
hertz_infra/config 负责"加载与热更新配置（文件 + Consul KV）"，
serverhertz 负责"把服务端装上治理能力并注册出去"，
业务代码负责"处理业务用例"，
main.go 负责"把这些东西按顺序启动起来"；
配置加载收口 hertz_infra/config，不保留 biz/config 薄壳；
注册（consul:）、发现（consul_discovery:）、配置中心（consul_kv:）三块物理隔离，
启用字段统一 enabled，当前服务名统一 app.name。
```

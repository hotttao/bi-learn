# Client 调用指南

> 适用项目：使用 CloudWeGo Hertz 作为 HTTP / RPC 框架的 Go 微服务项目。
> 适用对象：负责生成、修改、审查客户端调用与客户端侧服务治理代码的 Coding Agent。
> 核心目标：`hertz_gen` 负责完成具体下游服务调用，`hertz_infra/clienthertz` 负责提供治理能力（options / middleware），`<app>/biz/shared/client` 负责按业务需要初始化具体下游 client。

---

## 1. 设计目标

项目通过 `hz client` 生成下游 Hertz 服务的客户端代码。生成后的 `hertz_gen` 已经包含：

- 下游服务的请求 / 响应模型；
- 下游服务的 typed client interface；
- 下游服务的 `NewHertzClient` 构造入口；
- 具体 RPC / HTTP 调用方法。

因此，本项目**不强制**为每个下游服务增加一层 `biz/client/<feature>` 业务包装。

客户端治理按**能力与实例分离**组织：

- `hertz_infra/clienthertz` 提供**治理能力**——把配置转换成 `client.Option` / `client.Middleware`，组合成 `ClientSuite`；它不关心"哪个服务需要哪些下游 client"，只关心"如何把治理装到 client 上"。
- `<app>/biz/shared/client` 提供**实例装配**——按当前服务的业务需要，决定初始化哪些下游 client、把它们暴露为哪些全局变量；它消费 `clienthertz` 的能力，不自己写治理逻辑。

**配置粒度统一收敛到 app 级**：

- 同一个下游 app 暴露的所有 service（如 `media-scheduler` 进程的 `JobService` + `WorkflowService`）共享同一份 `client.<app_name>` 配置；
- 配置的行为（timeout / retry / circuit_breaker）应用在 app 级，不下沉到 service 级；
  - **熔断只在 client 侧**（`circuit_breaker`）；**限流只在 server 侧**（`serverhertz/ratelimit`，YAML 顶层 `rate_limit`），
    故 `client.<app>` 不含 `rate_limit`。见 `contributing/server.md` §5.2。
- 避免配置嵌套层级过深，2 层结构（`client.<app_name>`）即可。

最终调用关系：

```text
service / workflow
        ↓
<app>/biz/shared/client.<XxxClient>          # 业务级全局 client 实例
        ↓
hertz_infra/clienthertz.ClientSuite           # 治理能力（options + middleware）
        ↓
hertz_gen/<app>/<service>.Client              # 生成层 typed client
        ↓
callee service
```

---

## 2. 总体目录结构

```text
media_agent/                                    # workspace 根（go.work）
├── hertz_gen/                                  # 🔴 hz 生成根（独立 module，跨服务共享，禁止手改）
│   ├── model/                                  # hz update + hz client 共享的 API DTO 根
│   │   └── <app>/<service>/
│   └── <app>/                                  # hz client 生成的下游 client stub
│       ├── <service>_service.go
│       └── hertz_client.go
│
├── hertz_infra/                                # 跨服务共享的 hertz 治理层（独立 module）
│   └── clienthertz/                            # 治理能力提供方（不持有业务 client 实例）
│       ├── suite.go                            # ClientSuite：组合 client options / middleware 的装配面
│       ├── config.go                           # clienthertz 内部配置类型或转换辅助
│       ├── discovery.go                        # 服务发现 option 工厂
│       ├── loadbalance.go                      # 负载均衡 option 工厂
│       ├── timeout.go                          # 超时 option 工厂
│       ├── retry.go                            # 重试 option 工厂
│       ├── circuitbreaker.go                   # 熔断 middleware 工厂（熔断只在 client 侧）
│       ├── tracing.go                          # tracing provider 初始化 + middleware 工厂
│       ├── metrics.go                          # metrics exporter 初始化 + middleware 工厂
│       └── middleware.go                       # 中间件顺序约定
│
└── <app>/                                      # 单个 hertz 微服务（module: media_agent/<app>）
    ├── main.go
    ├── biz/
    │   ├── config/                             # 配置加载
    │   ├── service/                            # 业务用例
    │   ├── workflow/                           # 跨服务编排
    │   ├── handler/                            # Hertz handler
    │   └── shared/
    │       └── client/                         # 🟢 业务级 client 实例装配（每个 app 自有）
    │           ├── clients.go                  # 全局下游 client 变量声明
    │           ├── init.go                     # Init(cfg)：初始化当前服务需要的所有下游 client
    │           └── init_<downstream_app>.go    # 可选：按下游 app 拆分初始化
    └── conf/
        ├── local.yaml
        ├── dev.yaml
        └── prod.yaml
```

> `hertz_gen/` 与 `hertz_infra/clienthertz/` 是 workspace 根的共享 module，跨服务复用；`<app>/biz/shared/client/` 是每个微服务自有的业务级装配层，不在 workspace 共享。

---

## 3. 分层职责

| 层 | 路径 | 职责 | 是否手写 |
|---|---|---|---|
| 生成层 | `hertz_gen/<app>/` + `hertz_gen/model/` | 下游服务模型、typed client、调用方法 | 否，`hz client` 生成 |
| 能力层 | `hertz_infra/clienthertz/` | 提供治理能力的 option / middleware 工厂 + `ClientSuite` 装配面；不持有业务 client 实例 | 是 |
| 实例层 | `<app>/biz/shared/client/` | 按当前服务业务需要，消费 `ClientSuite` 能力，初始化具体下游 client，暴露为全局变量 | 是 |
| 业务层 | `biz/service/**` / `biz/workflow/**` | 使用初始化后的 client 完成业务调用 | 是 |
| 启动层 | `main.go` | 加载配置、调用 `biz/shared/client.Init` 与 `clienthertz` 的进程级初始化、启动 Hertz server | 是，但保持轻薄 |

核心原则：

- `hertz_gen` 只负责"能调用"；
- `clienthertz` 只负责"提供治理能力"，不知道业务用哪些下游；
- `biz/shared/client` 负责"决定用哪些下游 + 把治理装上去 + 暴露全局实例"；
- `service` / `workflow` 负责"什么时候调用、调用什么"；
- `main.go` 只负责"启动顺序"。

---

## 4. 生成 Hertz client

下游 client stub 的生成命令、关键参数、生成产物路径详见 [`contributing/idl.md`](idl.md) §4。本文件不再重复。

规则：

- `hertz_gen/**` 禁止手改；
- 下游 IDL 变更后重新运行 `hz client`（项目脚本：`make hz-client IDL=<app>/<file>.proto`）；
- 不在 `hertz_gen` 中加入治理逻辑、业务逻辑、mock、adapter、wrapper。

---

## 5. 配置文件约定

客户端治理配置分为两类：

1. 进程级治理配置（顶级块，含 `consul_discovery`）；
2. 下游 app 级治理配置（`client.<app_name>` 两层结构）。

> **命名约定**：
>
> - 字段名与项目其它配置块对齐，启用字段统一用 `enabled`（不是 `enable`）。
> - 当前 caller 服务名用 `app.name`（不是 `service.name`），与 `app.env` 同属 `app:` 顶级块。

### 5.1 进程级治理配置

进程级配置放在 `conf/<env>.yaml` 顶级。

```yaml
app:
  name: "media-materials"
  env: "dev"

consul_discovery:
  enabled: true
  address: "127.0.0.1:8500"
  refresh_interval: 5s
```

- `app.name` 是当前 caller 服务名；
- `consul_discovery` 是**客户端侧服务发现**配置，由 `hertz_infra/clienthertz` 消费；
- `consul_discovery` 只含 `enabled / address / refresh_interval` 三个字段；
- 进程级发现配置是当前进程级别的基础设施能力，所有下游 app 共享。

> 服务注册（server-side）字段保留在独立 `consul:` 顶级块，由 `hertz_infra/serverhertz` 消费，不要混入 `consul_discovery`。详见 [`contributing/architecture-cn.md`](architecture-cn.md) §注册 / 发现规则。

### 5.2 下游 app 级治理配置

每个下游 app 放在 `client.<app_name>` 下，**两层结构即可，不嵌 `services` 子键**。

```yaml
client:
  media_scheduler:
    service_name: "media-scheduler"
    base_domain: "discovery://media-scheduler"

    timeout:
      enabled: true
      dial: 500ms
      read: 3s
      write: 3s
      max_conn_duration: 30s

    retry:
      enabled: true
      max_attempts: 3
      init_delay: 50ms
      max_delay: 2s

    circuit_breaker:
      enabled: true
      threshold: 0.5
      stat_interval_ms: 10000
      min_request_amount: 20
      retry_timeout_ms: 5000
```

- `media_scheduler` 是 caller 内部使用的 app key（用下划线，匹配 Go 变量命名风格）；
- `service_name` 是注册中心里的下游服务名（可用连字符，如 `media-scheduler`）；
- `base_domain` 是 Hertz client 访问地址，服务发现模式推荐 `discovery://<service-name>`，静态地址可用 `http://<host>:<port>`；
- `timeout / retry / circuit_breaker` 是该 app 的治理行为，每个行为都必须显式配置 `enabled`；
  - **限流不在 `client.<app>` 配置**——限流只在 server 侧（YAML 顶层 `rate_limit`），见 `server.md` §5.2；
- 同一个 app 暴露多个 service（如 `media-scheduler` 进程同时提供 `JobService` 和 `WorkflowService`）**共享这一份 `client.media_scheduler` 配置**——不按 service 拆分配置，避免嵌套层级过深。

### 5.3 治理能力与配置段对应表

| 治理能力 | YAML 配置段（在 `client.<app>` 下） | 进程级配置 | 能力层文件 | 接入方式 |
|---|---|---|---|---|
| 服务发现 | — | `consul_discovery` 顶级块 | `discovery.go` | `ClientSuite.Options()` |
| 负载均衡 | `load_balance` | — | `loadbalance.go` | `ClientSuite.Options()` |
| 超时 | `timeout` | — | `timeout.go` | `ClientSuite.Options()` |
| 重试 | `retry` | — | `retry.go` | `ClientSuite.Options()` |
| 熔断 | `circuit_breaker` | — | `circuitbreaker.go` | `ClientSuite.Middlewares()` |
| 链路追踪 | `tracing`（可选，或复用 `otel` 顶级块） | `otel` 顶级块 | `tracing.go` | `ClientSuite.Middlewares()` + `InitTracing()` |
| 指标采集 | `metrics`（可选，或复用 `prometheus` 顶级块） | `prometheus` 顶级块 | `metrics.go` | `ClientSuite.Middlewares()` + `InitMetrics()` |

> 限流不在 client 侧：限流只在 server 侧（`serverhertz/ratelimit`，YAML 顶层 `rate_limit`），故本表无 rate_limit 行。

### 5.4 配置 struct 结构

`biz/config/config.go` 中 `client` 字段直接用 `map[string]AppClientConfig`，便于扩展多个下游 app；新增下游 app 时只需在 YAML 顶层增加一个 key，不需要在 `Config` 中加字段。

```text
Config
├── App             AppConfig              (app.name / app.env)
├── ConsulDiscovery ConsulDiscoveryConfig  (进程级服务发现)
└── Client          map[string]AppClientConfig
                     └── AppClientConfig
                         ├── ServiceName
                         ├── BaseDomain
                         ├── Timeout        TimeoutConfig
                         ├── Retry          RetryConfig
                         └── CircuitBreaker CircuitBreakerConfig
```

规则：

- 业务代码不直接读取 `client.<app_name>`；
- 配置加载后由 `biz/shared/client.Init` 统一消费（经 `clienthertz` 转换为治理能力）。

---

## 6. clienthertz 能力层

`hertz_infra/clienthertz` 是 Hertz 客户端治理能力唯一收口，**只提供能力，不持有业务 client 实例**。

它允许 import：

```text
github.com/cloudwego/hertz/...
github.com/hertz-contrib/...
github.com/alibaba/sentinel-golang/...
go.opentelemetry.io/otel/...
```

它不应该包含业务逻辑，也不应该知道当前服务有哪些下游。

### 6.1 ClientSuite 装配面

`suite.go` 定义 `ClientSuite`，它把配置转换成 Hertz client options 和 middleware，是治理能力接入 client 的唯一装配点：

```text
ClientSuite
├── Options() []client.Option                    # 组装 option（discovery / loadbalance / timeout / retry）
├── Middlewares(serviceName) []client.Middleware # 组装 middleware（tracing / metrics / circuitbreaker）
└── 各能力字段（Discovery / LoadBalance / Timeout / Retry / CircuitBreaker / Tracing / Metrics）
```

装配规则：

- `Options()` 只组装 option，不写具体能力实现；
- `Middlewares()` 只组装 middleware，不写具体能力实现；
- 具体能力实现拆到独立文件（§6.2）；
- 新增治理能力时，先新增独立文件，再在 `ClientSuite` 增加字段并接入 `Options()` / `Middlewares()`；
- `clienthertz` 不调用 `NewHertzClient`，不持有 client 实例——这是 `biz/shared/client` 的职责。

### 6.2 各能力文件职责

每个治理能力一个文件，文件内定义该能力的 `Config` struct 与 `client.Option` / `client.Middleware` 构造函数，由 `ClientSuite` 调用：

| 文件 | 职责 | 接入方式 | 读取配置 |
|------|------|----------|----------|
| `discovery.go` | 根据 `consul_discovery` 创建服务发现 option | `Options()` | `consul_discovery` 顶级块 |
| `loadbalance.go` | 根据 `load_balance` 创建负载均衡 option | `Options()` | `client.<app>.load_balance` |
| `timeout.go` | 根据 `timeout` 创建超时 option | `Options()` | `client.<app>.timeout` |
| `retry.go` | 根据 `retry` 创建重试 option（含幂等判断：GET/HEAD/OPTIONS/PUT/DELETE 可重试，POST 需 `Idempotency-Key`） | `Options()` | `client.<app>.retry` |
| `circuitbreaker.go` | 根据 `circuit_breaker` 创建熔断 middleware | `Middlewares()` | `client.<app>.circuit_breaker` |
| `tracing.go` | 初始化 OTel provider（进程级）+ 创建 tracing middleware | `Middlewares()` + `InitTracing()` | `otel` 顶级块 |
| `metrics.go` | 初始化 metrics exporter（进程级）+ 创建 metrics middleware | `Middlewares()` + `InitMetrics()` | `prometheus` 顶级块 |
| `middleware.go` | 定义中间件顺序常量 | — | — |

> 限流不在 client 侧（无 `ratelimit.go`）：限流只在 server 侧，见 `server.md` §5.2。

### 6.3 中间件顺序

推荐顺序（由外到内）：

```text
tracing → metrics → circuitbreaker → request
```

- tracing 放最外层，覆盖完整调用链；
- metrics 记录端到端耗时；
- circuitbreaker 在请求前快速失败；
- retry 通常通过 client option 配置，不作为普通 middleware 排序。

新增能力时按此顺序插入，并在 `middleware.go` 更新顺序说明。

---

## 7. biz/shared/client 实例层

`<app>/biz/shared/client/` 是业务级 client 实例装配层，**每个微服务自有**。它决定当前服务需要哪些下游 client，消费 `clienthertz` 的治理能力，把初始化后的 client 暴露为全局变量。

### 7.1 clients.go

全局下游 client 变量声明，类型直接使用 `hertz_gen` 生成的 `Client` interface。

命名规则：

```text
<AppPascalCase><ServicePascalCase>Client
```

| app | service | 变量名 |
|---|---|---|
| `media_scheduler` | `JobService` | `MediaSchedulerJobClient` |
| `media_scheduler` | `WorkflowService` | `MediaSchedulerWorkflowClient` |
| `media_materials` | `MaterialService` | `MediaMaterialsMaterialClient` |

规则：

- 同一个 `client.<app>` 配置块下可以有多个全局 client 变量，对应该 app 暴露的多个 service；
- 全局变量只声明实例，不写初始化逻辑；
- 初始化由 `init.go` 或 `init_<downstream_app>.go` 完成；
- 业务代码只能使用这些已经初始化的全局 client，不允许自己 new client。

### 7.2 init.go

`Init(cfg)` 是当前服务客户端初始化唯一入口，职责：

1. 调用 `clienthertz` 的进程级初始化（`InitTracing` / `InitMetrics`，若未在 `main.go` 调用）；
2. 调用 `clienthertz.BuildBaseSuite(cfg)` 构造 `ClientSuite` 基底（从进程级配置 `app.name` / `consul_discovery`）；
3. 对当前服务需要的每个下游 app 调用 `init<DownstreamApp>Clients(cfg, baseSuite)`（**注意是 `Clients` 复数**，因为一个 app 可能初始化多个 service）；
4. 下游 app 配置缺失必须返回错误，不能静默忽略。

按下游 app 拆分时新增 `init_<downstream_app>.go`，文件内：

1. 读取该 app 的 `AppClientConfig`（缺失则返回错误）；
2. 调用 `clienthertz.BuildAppSuite(baseSuite, appCfg)` 把 app 级 4 个行为配置拷贝到 `ClientSuite` 副本（**只拷贝一次**，因为 4 行为是 app 级共享的）；
3. 对该 app 的每个 service 调用 `hertz_gen/<app>.NewHertzClient(baseDomain, clienthertz.BuildOptions(suite, "<app>")...)`；
4. 赋值给 `clients.go` 中的全局变量。

### 7.3 main.go

`main.go` 只调用 `<app>/biz/shared/client.Init(cfg)`（以及 `clienthertz` 的进程级 `InitTracing` / `InitMetrics`），不写具体 client 初始化细节，禁止出现 `NewHertzClient` / `client.WithRetryConfig` / `client.WithMiddleware` / `consulregistry.NewConsulResolver` / `sentinel.NewClientMiddleware` 等调用。

---

## 8. 业务代码如何使用 client

业务代码直接使用 `biz/shared/client` 中初始化后的全局 client：

```go
resp, err := client.MediaSchedulerJobClient.SubmitJob(ctx, req)
```

规则：

- 业务代码可以使用 `biz/shared/client.<XxxClient>`；
- 业务代码可以使用 `hertz_gen/model` 的请求 / 响应类型；
- 业务代码不负责 client 初始化、不拼接 client options、不调用 `NewHertzClient`；
- 业务代码不 import `github.com/cloudwego/hertz/pkg/app/client`、`github.com/hertz-contrib/*`；
- 业务代码不 import Sentinel / OpenTelemetry / Prometheus SDK；
- handler 尽量不要直接调用下游 client，应通过 service / workflow 调用。

---

## 9. 新增一个下游 app

1. `hz client` 生成 `hertz_gen/<app>/`（详见 [`contributing/idl.md`](idl.md) §4）；
2. 在 `conf/<env>.yaml` 的 `client:` 下增加 `<app>` 配置块（2 层结构，不嵌 `services`）；
3. 在 `biz/shared/client/clients.go` 增加全局 client 变量（同一 app 多 service 共享 `client.<app>` 配置）；
4. 新增 `biz/shared/client/init_<app>.go`，实现 `init<App>Clients(cfg, baseSuite)`，内部用 `clienthertz.BuildAppSuite` + `clienthertz.BuildOptions` 装配 client；
5. 在 `biz/shared/client/init.go` 的 `Init(cfg)` 中注册 `init<App>Clients`；
6. 业务代码通过 `client.<XxxClient>` 使用；
7. 运行 `gofmt` + `go test ./...`。

---

## 10. 新增一个治理能力

1. 在 `conf/<env>.yaml` 的 `client.<app>` 下增加新能力段（如 `bulkhead:`），含 `enabled` 字段；
2. 在 `biz/config/config.go` 的 `AppClientConfig` 增加对应字段；
3. 新增 `hertz_infra/clienthertz/<ability>.go`，定义 `Config` struct 与构造函数（option 或 middleware）；
4. 在 `ClientSuite` 增加字段；
5. 在 `Options()`（若为 option）或 `Middlewares()`（若为 middleware）中接入；
6. 在 `BuildAppSuite` 中加一段配置拷贝（**只加一次**，因为是 app 级行为，所有用这个 app 的 service 自动获得）；
7. 更新 `middleware.go` 中的顺序说明；
8. 补充单元测试（覆盖 `enabled=false` no-op、`enabled=true` middleware 加入、配置缺失、初始化失败、middleware 顺序）；
9. 运行 `gofmt` + `go test ./...`。

---

## 11. 禁止事项

1. 手改 `hertz_gen/**`；
2. 在 `main.go` 中写具体下游 client 初始化；
3. 在业务代码中调用 `NewHertzClient`；
4. 在业务代码中手动拼 `client.Option`；
5. 在业务代码中写服务发现、负载均衡、重试、熔断、限流、Tracing、Metrics 逻辑；
6. 给每个下游 service 复制一套治理代码——同一 app 的所有 service 共享一份 `client.<app>` 配置；
7. 把治理逻辑写进 `hertz_gen`；
8. 在 `hertz_infra/clienthertz` 中持有业务 client 实例或调用 `NewHertzClient`——能力层只提供 options / middleware，实例化归 `biz/shared/client`；
9. 把所有治理逻辑堆到单个 `init.go`；
10. 新增治理能力但不接入 `ClientSuite`；
11. 新增下游 app 但不在 `biz/shared/client.Init()` 注册；
12. 在 handler 中绕过 service / workflow 直接编排复杂下游调用；
13. 在业务层 import `github.com/hertz-contrib/*`；
14. 在业务层 import Sentinel / OpenTelemetry / Prometheus SDK；
15. 在请求执行过程中初始化 client；
16. 初始化失败后继续启动服务；
17. 在 `client.<app>` 下再加 `services` 嵌套层级——配置粒度在 app 级，2 层结构即可；
18. 用 `enable`（不带 `d`）作为启用字段名——必须用 `enabled`；
19. 用 `service.name` 表示当前 caller 服务名——必须用 `app.name`。

---

## 12. 修改清单

### 12.1 新增下游 app 时

- [ ] 使用 `hz client` 生成 `hertz_gen`（详见 [`contributing/idl.md`](idl.md) §4）；
- [ ] 更新 `conf/<env>.yaml` 的 `client.<app>` 配置块（2 层结构，不嵌 `services`）；
- [ ] 在 `biz/shared/client/clients.go` 增加全局 client 变量（同一 app 多 service 共享 `client.<app>` 配置）；
- [ ] 新增或更新 `biz/shared/client/init_<app>.go`（函数名 `init<App>Clients`，复数）；
- [ ] 在 `biz/shared/client/init.go` 的 `Init(cfg)` 中注册 `init<App>Clients`；
- [ ] 业务代码通过 `client.<XxxClient>` 使用；
- [ ] 不手改 `hertz_gen/**`；
- [ ] 不在 `main.go` 写具体 client 初始化；
- [ ] 运行 `gofmt` + `go test ./...`。

### 12.2 新增治理能力时

- [ ] 更新 `conf/<env>.yaml`（在 `client.<app>` 下增加新能力段）；
- [ ] 更新 `biz/config/config.go` 的 `AppClientConfig`；
- [ ] 新增 `hertz_infra/clienthertz/<ability>.go`；
- [ ] 在 `ClientSuite` 增加字段；
- [ ] 在 `Options()` 或 `Middlewares()` 中接入；
- [ ] 在 `BuildAppSuite` 中加一段配置拷贝（只加一次）；
- [ ] 更新 `middleware.go` 中的顺序说明；
- [ ] 补充单元测试；
- [ ] 运行 `gofmt` + `go test ./...`。

### 12.3 字段命名统一检查

- [ ] 启用字段用 `enabled`（不是 `enable`）；
- [ ] 当前服务名用 `app.name`（不是 `service.name`）。

---

## 13. 与 architecture-cn.md 的关系

- `architecture-cn.md` 是规则文档（rule），定义层边界与硬约束；
- `client-cn.md` 是规范文档（spec），定义配置约定、能力层 / 实例层切分、`ClientSuite` 装配面、初始化流程、修改清单；
- 冲突时，以 `architecture-cn.md` 的层边界规则为准；
- 本指南的层边界、硬约束、禁止 import 规则与 `architecture-cn.md` §Hertz 客户端代码保持一致。

---

## 14. 一句话原则

```text
hertz_gen 负责"调用下游服务"，
clienthertz 负责"提供治理能力（options / middleware），不持有业务 client 实例"，
biz/shared/client 负责"按业务需要消费治理能力、初始化具体下游 client、暴露全局实例"，
业务代码负责"使用已经初始化好的 client 完成业务流程"；
配置粒度统一收敛到 app 级，client.<app_name> 两层即可，不再嵌 services；
命名上 enabled / app.name 与 architecture-cn.md 保持一致。
```

# 网关与 BFF 架构

> 适用项目：使用 Traefik 作为入口网关 + Hertz BFF 作为业务聚合层的 Go 微服务项目。
> 适用对象：负责架构设计、网关配置、BFF 开发的 Coding Agent。
> 核心目标：Traefik 负责 SSL/路由/负载均衡/服务发现（north-south），Hertz BFF 负责业务聚合/组装/透传（应用层），后端服务专注于领域逻辑。

本文档是通用 harness 约束，不绑定具体服务。文中用 `<svc-A>` / `<svc-B>` 泛指任意后端服务，用 `<auth-svc>` 泛指认证服务，用 `<bff>` 泛指 BFF 服务。

---

## 1. 架构分层

```text
Internet → Traefik（入口网关）→ BFF（业务聚合层）→ 后端服务（领域层）
                            → 前端静态文件
```

| 层 | 工具 | 职责 | 不做 |
|:---|:---|:---|:---|
| 入口网关 | Traefik | SSL 终止、路由、负载均衡、健康检查 | 业务逻辑 |
| BFF | Hertz | 聚合、组装、透传、SSE 代理、统一 auth | DB、领域逻辑 |
| 后端服务 | Hertz | 领域逻辑、DB、CRUD | 前端适配 |
| 前端 | SPA | UI、交互 | 直接调后端 |

### 流量分类

| 类型 | 说明 | BFF 处理方式 |
|:---|:---|:---|
| 聚合 | 一个前端请求需要多个后端服务的响应拼装 | service 层并发调多个 typed client → 合并映射 |
| 组装 | 前端传简单参数，BFF 负责查数据拼复杂入参 | service 层调 typed client 查数据 → 组装 → 调 typed client 提交 |
| 透传 | 前端请求与后端接口 1:1，无需聚合 | service 层调单个 typed client → 1:1 映射 |
| SSE | 后端事件流透传到前端 | BFF 代理 chunk（唯一允许 raw HTTP 的场景） |
| 直连 | 不经 BFF 的路由（如 OAuth callback） | Traefik 直接路由到后端服务 |

---

## 2. Traefik 约束

### 2.1 路由发现

- **Docker label 驱动**：每个服务在自己的 `docker-compose.yml` 里声明 Traefik label，不集中配置。
- **Consul Catalog**：可选，用于非 Docker 环境（K8s 迁移时）。
- **禁止**：手写 Traefik 静态配置文件管路由——路由随服务声明走。

### 2.2 路由优先级

| 优先级 | 路径前缀 | 目标 | 说明 |
|:---:|:---|:---|:---|
| 高 | `/api/auth` | `<auth-svc>` | OAuth callback 等不经 BFF |
| 中 | `/api/bff` | `<bff>` | 聚合/透传/SSE |
| 中 | `/api/v1` | 后端服务 | 内部调试，prod 可关 |
| 低 | `/` | 前端 nginx | 兜底 |

### 2.3 SSL

- Traefik 统一终止 SSL，后端服务 / BFF 收到的是明文 HTTP。
- 证书走 Let's Encrypt 自动签发 / 续期，持久化到 `acme.json`。
- **禁止**：后端服务 / BFF 自己管 SSL 证书。

---

## 3. BFF 架构约束

### 3.1 目录结构

BFF 遵循 `architecture.md` 的 DDD 分层，但**无 dal / domain 层**（BFF 不持 DB、不持领域核心）：

```text
<bff>/biz/
├── handler/          # 薄：bind → command → service → 映射
├── service/          # 聚合编排：调 typed client + 映射
├── shared/
│   ├── auth/         # auth 中间件
│   └── client/       # typed client 装配（clienthertz）
├── middleware/       # BFF 特有中间件
└── router/           # Hertz 路由（hz 生成）
```

### 3.2 依赖规则

```text
handler  → service / shared / model（BFF API DTO）
service  → shared/client（typed client）/ shared
shared   → hertz_infra/clienthertz / hertz_gen（typed client stub）
```

**禁止**：
- BFF import `dal` / `domain`（任何后端服务的）。
- BFF import `gorm` / 数据库驱动。
- BFF `handler` 直接调 `shared/client`（必须经 `service`）。

### 3.3 后端调用

- **必须**走 `hz-client` 生成的 typed client（`hertz_gen/<app>/<service>_service.go`）。
- typed client 经 `clienthertz` 获得 Consul 发现 + 熔断 + 重试 + 超时。
- **禁止**：BFF 用 raw `net/http` / `httputil.ReverseProxy` 调后端。
- **唯一例外**：SSE / streaming 代理（typed client 不支持 streaming）。

### 3.4 DTO 解耦

- BFF IDL（`idl/<bff>/*.proto`）定义**前端视角**的 DTO，不引用后端 proto message。
- BFF service 层负责后端 DTO → BFF DTO 的映射（即使 1:1 也走映射）。
- 后端改字段 → BFF service 层适配 → 前端无感。
- **禁止**：BFF DTO 直接 import / 引用后端 proto message。

### 3.5 IDL-first

- 先定义 BFF IDL（前端要什么接口 + DTO），再生成代码、实现 service。
- BFF IDL 的路径前缀统一 `/api/bff/`。
- 聚合接口的 DTO 反映前端需要的数据形状，不暴露后端内部结构。
- 透传接口也需在 BFF IDL 定义（不直接暴露后端接口给前端）。

---

## 4. Auth 约束

- BFF 的 auth 与后端服务一致：cookie JWT（本地 HS256 验签）+ Bearer service token（调 `<auth-svc>` 验证）。
- BFF 是 OAuth client（在 `<auth-svc>` 的 `oauth_clients` 表注册），有自己的 `client_secret`。
- BFF 与同网关下的后端服务共享 JWT 密钥（同一密钥 → cookie 跨服务同域有效）。
- BFF 调后端时，后端 auth 中间件校验请求——cookie 同域自动带，或 BFF 在 typed client 请求头加 Bearer token。
- **禁止**：BFF 绕过 auth 中间件暴露未鉴权接口（除 login / callback / logout）。

---

## 5. 前端约束

### 5.1 项目位置

前端源码放独立目录，独立于任何 Go module。

### 5.2 静态文件服务

| 环境 | 方式 | 说明 |
|:---|:---|:---|
| 开发 | Vite dev server + proxy `/api` → BFF | 热更新 |
| 生产 | nginx 容器 + Traefik label 路由 | 独立于 BFF，不占 BFF 资源 |

- 生产环境**禁止**用 BFF `go:embed` serve 前端——静态文件负载不应由 Go 进程承担。
- 开发环境可用 `go:embed` 便于单二进制调试。

### 5.3 前端调用约束

- 生产环境前端**可以调** BFF `/api/bff/*`，也可以直连后端 `/api/v1/*`。
- 开发环境可直连后端调试。

---

## 6. 配置约束

### 6.1 三层配置

| 层 | 载体 | 放什么 | 例子 |
|:---|:---|:---|:---|
| 公共 | `docker-compose/.env.<infra>` + `env_file` | infra 开关、共享密钥、下游 URL | `OTEL_ENABLED`、JWT 密钥、`<svc-A>_URL` |
| 服务特有 | `docker-compose` `environment` | 该服务独有的变量 | `POSTGRES_DSN`（db 名不同）、`LOG_DIR` |
| 本地默认 | yaml `${VAR:-default}` | `go run` 时的默认值 | `${SERVER_ADDR:-:8080}` |

### 6.2 约定大于配置

- `SERVER_ADDR` 不在 `environment` 里设——yaml 已有 `${SERVER_ADDR:-:port}` 默认值。
- 环境变量名全链路一致，不在 docker-compose 里重命名。
- yaml 提供本地 `go run` 的默认值，docker 环境用 env_file 覆盖。
- 下游 URL 在 env_file 里用 `host.docker.internal`，yaml 默认值用 `127.0.0.1`。

---

## 7. 禁止事项汇总

| 禁止 | 理由 |
|:---|:---|
| BFF 用 raw HTTP 调后端（SSE 除外） | 绕过 typed client 的类型安全 + 治理 |
| BFF 持 DB / domain / dal 层 | BFF 是聚合层，不是领域服务 |
| BFF handler 直接调 typed client | handler 必须经 service 层 |
| BFF DTO 引用后端 proto message | 解耦前后端 |
| 生产环境 BFF serve 静态文件 | 静态负载不该由 Go 进程承担 |
| 生产环境前端直连后端 | 前端只调 BFF |
| Traefik 手写静态路由配置 | 路由随 Docker label / Consul 声明走 |
| 后端服务自己管 SSL | Traefik 统一终止 |
| docker-compose 里重命名环境变量 | 全链路名字一致 |
| `SERVER_ADDR` 在 environment 里设 | yaml 默认值已够 |

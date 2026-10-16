# IDL 与 Protobuf 约束指南

> 适用项目：所有基于 Hertz 的 Go 微服务
> 适用对象：负责修改 IDL、生成代码、HTTP 注解的 Coding Agent
> 核心目标：IDL 是 API 形态的唯一源；生成路径由 `go_package` 决定；双重 HTTP 注解不可遗漏；禁止手改生成产物

---

## 术语约定

| 占位符 | 含义 | 示例 |
|--------|------|------|
| `<app>` | 微服务名（go module / proto package / IDL 一级目录） | `media_example` |
| `<service>` | IDL `service` 名（`go_package` 最后一段，handler/router 子目录名） | `hello` |
| `<file>` | IDL 文件名（不含 `.proto`） | `hello` |

> IDL 约定：`option go_package = "<app>/<service>";`

---

## 1. IDL 生成路径规则

### 1.1 IDL 文件组织

```text
idl/<app>/<file>.proto
  option go_package = "<app>/<service>";
```

示例：

```proto
syntax = "proto3";

package media_example.hello;

option go_package = "media_example/hello";
```

### 1.2 生成产物映射

```text
idl/<app>/<file>.proto
      │
      ├── hz model  ──→ hertz_gen/model/<app>/<service>/<file>.pb.go        🔴 共享 API DTO
      ├── hz client ──→ hertz_gen/<app>/<service>_service.go                 🔴 调用下游服务的 client stub
      │                 hertz_gen/<app>/hertz_client.go                      🔴 client 聚合入口
      └── hz update ──→ <app>/biz/handler/<service>/<service>_service.go    🟡 handler 骨架
                        <app>/biz/router/<service>/<service>.go             🔴 路由注册
                        <app>/biz/router/<service>/middleware.go             🟡 中间件 hook
                        <app>/biz/router/register.go                         🔴 路由聚合
```

> `hz model` / `hz client` 写入 workspace 级共享 module `hertz_gen/`；`hz update` 写入各微服务 `<app>/`。`hertz_gen/` 下按 `<app>` 分目录，与 `hertz_gen/model/<app>/<service>/` 保持一致——即 `hertz_gen/<app>/` 放该 app 的下游 client stub，`hertz_gen/model/<app>/<service>/` 放该 app 的 API DTO。

### 1.3 严格项目约定

项目推荐约定（默认可选，可通过 `--strict-idl` 强制）：

```text
idl/<app>/<file>.proto
  option go_package = "<app>/<service>";
  go_package 最后一段 == <service>
```

强制命令：

```bash
go run ./tools/archcheck --strict-idl
```

该严格检查不是 Hertz 要求；它是希望生成 handler / router 目录呈 `<service>` 形态的团队的项目约定。

---


## 2. HTTP 方法注解

每个 IDL RPC 都需要 **两条相互独立的 HTTP 注解**，分别给两个工具用：

| 工具 | 注解形式 | 作用 |
|------|----------|------|
| `hz`（Hertz 路由生成） | `option (api.post) = "/api/users";` | 驱动 `hz update` 生成路由代码 |
| `protoc-gen-openapi`（Swagger / OpenAPI） | `option (google.api.http) = { post: "/api/users" };` | 驱动 `make swagger-gen` 生成 OpenAPI 文档 |

**两条都必须写。** 它们不能互相替代——`hz` 不读 `google.api.http`，`protoc-gen-openapi` 不读 `api.*`。

正确写法（每个 RPC 同时挂两条注解）：

```proto
import "api.proto";
import "google/api/annotations.proto";

service HelloService {
  rpc GetHello(GetHelloReq) returns(Hello) {
    option (google.api.http) = { get: "/api/hello/:id" };
    option (api.get) = "/api/hello/:id";
  };
  rpc CreateHello(CreateHelloReq) returns(CreateHelloResp) {
    option (google.api.http) = { post: "/api/hello" };
    option (api.post) = "/api/hello";
  };
}
```

**路径参数格式：** `api.*` 注解里使用 `:id`（冒号），不是 `{id}`（花括号）。
- 正确：`option (api.get) = "/api/hello/:id";`
- 错误：`option (api.get) = "/api/hello/{id}";`  // 会导致参数绑定失败

`google.api.http` 注解使用 `{id}`（OpenAPI 标准）。两种格式在同一文件内并存，分别服务于不同的工具。

**常见错误：** 只写 `api.*` 注解（hz 路由生效但 OpenAPI 文档为空）或只写 `google.api.http` 注解（OpenAPI 文档生效但 hz 路由为空）。

**可用的 `api.*` 注解**（定义在 `idl/api.proto`）：
- `api.get` - GET 方法
- `api.post` - POST 方法
- `api.put` - PUT 方法
- `api.delete` - DELETE 方法
- `api.patch` - PATCH 方法
- `api.options` - OPTIONS 方法
- `api.head` - HEAD 方法
- `api.any` - ANY 方法

---

## 3. Protobuf 字段命名约定

**所有 Protobuf message 字段名必须使用 snake_case。** 这样可以保证：
- URL query 参数绑定一致（`query:"field_name"`）
- JSON 序列化输出一致
- OpenAPI / Swagger 文档一致

**正确：**

```proto
message ListProductsReq {
  string team_id = 1[(api.query)="team_id"];
  string user_id = 2[(api.query)="user_id"];
  int32 page = 3[(api.query)="page"];
  int32 page_size = 4[(api.query)="page_size"];
}
```

**错误：**

```proto
message ListProductsReq {
  string teamId = 1[(api.query)="team_id"];   // camelCase 字段名 - 不一致
  string userId = 2[(api.query)="user_id"];
  int32 page = 3[(api.query)="page"];
  int32 pageSize = 4[(api.query)="pageSize"]; // camelCase - 会引发绑定问题
}
```

**生成的 Go Model Tag：**
- Proto 字段名 `team_id` → Go struct tag `json:"team_id,omitempty" query:"team_id"`
- Proto 字段名 `page_size` → Go struct tag `json:"page_size,omitempty" query:"page_size"`

**常见错误：** 在 proto 字段名中用 camelCase，期望 URL 参数变成 snake_case。`.proto` 文件的所有字段名都必须用 snake_case。

**强制检查：** `hz update` 后运行 `go build ./...` 验证生成代码可编译。检查生成的 `hertz_gen/model/<app>/<service>/*.pb.go` 中 `query` tag 是否一致。

---


## 4 平台限制

> **注意**：hz `--use` 在 Linux/macOS 下生效；Windows 因 hz 内部 `filepath.Clean` 路径分隔符问题失效，需在 Linux/macOS 下执行 `hz update`。

---

## 5. 新增 IDL 端点的完整流程

```
1. idl/<app>/<file>.proto  ← 添加 RPC + google.api.http + api.* 双重注解
2. make hz-model           ← 重新生成共享 API DTO（仓库根目录）
3. make hz-client          ← 若供其它服务调用，重新生成 client stub（仓库根目录）
4. 切换到 `<app>` 目录
5. make hz-update          ← 重新生成 <app>/biz/handler/、<app>/biz/router/（<app>/ 目录）
6. make swagger-gen        ← 重新生成 swagger/openapi.yaml（<app>/ 目录）
7. make skills-gen         ← 同步 .claude/skills/（如启用，<app>/ 目录）
8. 验证                    ← go build ./... / swagger diff / 路由 handler 数量 / make arch-check
```


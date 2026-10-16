# Swagger / OpenAPI 与 Skill 文档

> 适用项目：所有基于 Hertz 的 Go 微服务
> 适用对象：负责维护 API 文档、IDL 注解、生成流程的 Coding Agent
> 核心目标：IDL 是 API 文档唯一源；openapi spec 从 IDL 生成；swagger-ui 内嵌公共库；skill 文档从 spec 生成

---

## 0. 设计分工

| 关注点 | 归属 | 说明 |
|--------|------|------|
| OpenAPI spec（各服务私有） | 各服务 | 由本服务 IDL 生成、embed 进本服务二进制 |
| swagger-ui 静态资源（公共） | `hertz_infra/serverhertz/swaggerui` | go:embed 内嵌，所有服务复用 |
| swagger-gen / skill-gen | 各服务 Makefile | 服务相关，不放根 Makefile |
| swagger-ui-fetch | 根 Makefile | 拉公共 UI 资源，所有服务共用 |

swagger 不挂在 `serverhertz.ServerSuite`——因为 spec 各服务私有，suite 是公共库无法预知。
各服务在自己的 router 层（`customizedRegister`）注册 `/swagger/*any` + `/openapi.yaml`。

---

## 1. IDL 注解（前提）

每个 RPC 需要**两条** HTTP 注解（详见 [idl.md](idl.md) §2）：

| 工具 | 注解 | 作用 |
|------|------|------|
| `hz`（路由生成） | `option (api.get) = "/hello";` | 驱动 `hz update` 生成路由 |
| `protoc-gen-openapi`（文档） | `option (google.api.http) = { get: "/hello" };` | 驱动 `swagger-gen` 生成 OpenAPI |

**两条都必须写**。只写 `api.*` → openapi 文档 paths 为空；只写 `google.api.http` → 路由不生成。

测试专用 service（如 `test.proto`）不挂 `google.api.http`，并被 `swagger-gen` 排除，避免污染正式文档。

---

## 2. 生成流程（各服务 Makefile）

```bash
# 在 <app>/ 目录下

# 1. 生成 OpenAPI v3 spec → <app>/swagger/openapi.yaml
make swagger-gen

# 2. （升级 swagger-ui 时）重新拉取公共 UI 资源 —— 在仓库根
make swagger-ui-fetch

# 3. 生成 Claude skill 文档 → skills/<服务名>/
make skill-gen
```

### swagger-gen

- 工具：`protoc-gen-openapi`（gnostic），`go install github.com/google/gnostic/cmd/protoc-gen-openapi@latest`
- 输入：本服务正式 service proto（排除 test.proto）
- 产物：`<app>/swagger/openapi.yaml`（OpenAPI v3）
- 约定：proto 用 `--openapi_opt=naming=proto`，字段名保持 proto 原名

### skill-gen

- 工具：`openapi-to-skills`（系统安装）
- 输入：`<app>/swagger/openapi.yaml`
- 产物：`skills/<服务名>/`（单层：`SKILL.md` + `references/`，name = 服务名）
- 别人拿到 `skills/` 目录即可按服务名找到对应接口 skill，指导如何调用 API
- `openapi-to-skills` 会在 `-o` 目录下再建一层 `--name` 子目录，故 `-o` 指向 `skills/`、`--name` 用服务名，产出正好 `skills/<服务名>/`（单层）
- `--groupBy=path` 按 path 分组

---

## 3. 服务集成（router 层）

spec 由服务自己 embed + 注册。在 hz 生成的 `customizedRegister`（`router.go`，非 DO NOT EDIT）里：

```go
import (
	_ "embed"
	"media_agent/hertz_infra/serverhertz/swaggerui"
)

//go:embed swagger/openapi.yaml
var openapiSpec []byte

func customizedRegister(r *server.Hertz) {
	// ... 业务路由 ...

	// GET /swagger/*any → 公共 swaggerui 包内嵌的 swagger-ui 静态资源
	r.GET("/swagger/*any", swaggerui.StaticHandler("/swagger"))
	// GET /openapi.yaml → 本服务 embed 的 OpenAPI spec
	r.GET("/openapi.yaml", func(ctx context.Context, c *app.RequestContext) {
		c.Header("Content-Type", "application/x-yaml")
		c.Write(openapiSpec)
	})
}
```

- `swagger/openapi.yaml` 是生成产物，但需随源码提交（go:embed 要求 build 时存在；重新生成后提交）。
- `swagger-initializer.js` 的 spec url 已被 `swagger-ui-fetch` 改为 `/openapi.yaml`。

---

## 4. 公共 swagger-ui 资源

`hertz_infra/serverhertz/swaggerui/`：

- `dist/`：swagger-ui-dist 运行必需文件（index.html / swagger-ui-bundle.js / swagger-ui.css / swagger-initializer.js 等），已提交。
- `swaggerui.go`：`//go:embed dist` + `StaticHandler(prefix)` 返回 Hertz handler，按路径前缀 strip 后从 embed 读文件。

升级 swagger-ui 版本：仓库根 `make swagger-ui-fetch`（重新拉取覆盖 `dist/`，并提交）。

---

## 5. 何时重跑

| 场景 | 动作 |
|------|------|
| IDL 新增 / 改动 RPC（含 `google.api.http`） | `make swagger-gen && make skill-gen`，提交 spec 与 skill 产物 |
| 升级 swagger-ui 版本 | 根 `make swagger-ui-fetch`，提交 `dist/` |
| 新服务接入 swagger | 在该服务 router.go 加 `/swagger/*any` + `/openapi.yaml`（§3）；Makefile 加 swagger-gen/skill-gen target |

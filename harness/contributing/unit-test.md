# 单元测试指南

本文档定义 Hertz web 服务的单元测试规范。

> 测试行为，不测试实现细节。不写只证明"代码做了代码做的事"的测试。

---

## 运行测试

```bash
go test ./...                                    # 全部测试
go test ./<app>/biz/domain/...                   # 单层
go test ./<app>/biz/service/<domain> -run Test   # 单包
go test ./... -race                              # 竞态检测
go test ./... -cover                             # 覆盖率摘要
```

项目质量门禁：

```bash
make arch-check   # 架构边界检查
make dead-check   # 死代码检查
make test         # 单元测试
make check        # 全量检查
```

CI 推荐顺序：**架构边界检查 → 死代码检查 → 单元测试**。

---

## 测试工具

| 工具 | 用途 | 包路径 |
|------|------|--------|
| Hertz `ut` | Handler 测试，不走网络，直接 `ServeHTTP` | `github.com/cloudwego/hertz/pkg/common/ut` |
| Hertz `assert` | 官方断言（`DeepEqual` / `NotNil` / `Nil`） | `github.com/cloudwego/hertz/pkg/common/test/assert` |
| GoConvey | BDD 风格断言（Hertz 官方推荐） | `github.com/smartystreets/goconvey` |
| testify | Mock / 断言 / Suite | `github.com/stretchr/testify` |
| `httptest.Server` | 外部 HTTP client 测试 | `net/http/httptest` |
| Testcontainers | DAL 真实数据库测试 | `github.com/testcontainers/testcontainers-go` |

核心 API：

```go
// 模拟一次 HTTP 请求，返回 ResponseRecorder
ut.PerformRequest(e *route.Engine, method, url string, body *ut.Body, headers ...ut.Header) *ut.ResponseRecorder

// 直接构造 *app.RequestContext，不注册路由测 handler
ut.CreateUtRequestContext(method, url string, body *ut.Body, headers ...ut.Header) *app.RequestContext

// Hertz 官方断言
assert.DeepEqual(t, expected, actual)
```

---

## 测试什么

**应当测试**：

```text
domain 不变量、值对象校验
policy 分支规则
service 用例（单 domain）
workflow 编排决策（跨 domain）
错误映射、事务行为
repository 实现（测试数据库）
外部 client 包装（fake HTTP/RPC server）
handler 请求/响应映射（当非平凡时）
```

**通常跳过**：

```text
hertz_gen/model 生成代码
biz/router 生成路由代码、router_gen.go
只做 绑定 → 调 service → 返回响应 的薄 handler
无行为的纯结构体、常量、一行包装
逐行镜像实现的测试
```

---

## 文件放置

测试文件与源码同目录：

```text
<app>/biz/domain/<domain>/model.go               →  model_test.go
<app>/biz/policy/<domain>/policy.go              →  policy_test.go
<app>/biz/service/<domain>/service.go            →  service_test.go
<app>/biz/workflow/<flow>_flow.go                →  <flow>_flow_test.go
<app>/biz/dal/repo/<domain>_repo.go              →  <domain>_repo_test.go
<app>/biz/handler/<service>/<service>_service.go →  <service>_service_test.go
```

默认用包内测试（`package <domain>`）。仅当需要从外部验证公开 API 时用外部测试包（`package <domain>_test`）。

---

## 架构对齐

测试遵循与源码相同的依赖规则（见 `arch.md` "依赖与 import 规则"）。

**禁止**：

```text
domain 测试 import hertz_gen/model
service 测试 import biz/handler 或 github.com/cloudwego/hertz/pkg/app
policy 测试 import biz/dal/query
service/<a> 测试 import service/<b>   # 同层 domain 隔离
```

**正确**：

```text
domain   → 只用 domain 类型
policy   → 直接构造 domain 对象
service  → fake repository + fake policy
workflow → fake service + fake client
handler  → fake service / workflow 接口
dal      → 测试数据库 fixture
client   → httptest.Server 或 fake RPC server
```

不要因为"只是测试"就破坏架构。

---

## 分层测试

### Domain

纯净、快速。测试构造函数、值对象校验、不变量、状态转换。

- 不 import Hertz、生成 DTO、数据库
- 优先表驱动

```go
func TestNewEmailRejectsInvalidAddress(t *testing.T) {
    _, err := NewEmail("not-an-email")
    assert.NotNil(t, err)
}
```

### Policy

测试易变业务规则：权限、定价、状态转换、发布、注册。

- 直接构造 domain 对象
- 不查 DB / Redis / 外部 API
- 不 import 其它 policy 包

```go
func TestOrderPolicyCanCancel(t *testing.T) {
    Convey("订单取消规则", t, func() {
        cases := []struct{ name string; status order.Status; want bool }{
            {"待支付可取消", order.StatusPending, true},
            {"已发货不可取消", order.StatusShipped, false},
        }
        for _, c := range cases {
            Convey(c.name, func() {
                So(policy.OrderPolicy{}.CanCancel(&order.Order{Status: c.status}), ShouldEqual, c.want)
            })
        }
    })
}
```

### Service

测试单 domain 用例：command 校验、repo 调用、policy 决策、事务、错误传播、结果映射。

- 用 fake repository，不用真实 DB
- 不 import `biz/dal`、`hertz_gen/model`、Hertz
- 不调用其它 domain 的 service（同层隔离）；跨 domain 放 workflow

```go
type fakeUserRepo struct {
    saved *user.User
    err   error
}
func (r *fakeUserRepo) Save(ctx context.Context, u *user.User) error {
    if r.err != nil { return r.err }
    r.saved = u
    return nil
}

func TestUserServiceCreateUser(t *testing.T) {
    repo := &fakeUserRepo{}
    svc := NewService(repo, UserPolicy{})
    got, err := svc.CreateUser(context.Background(), CreateUserCommand{Name: "Ada"})
    assert.Nil(t, err)
    assert.NotNil(t, repo.saved)
    assert.DeepEqual(t, "Ada", got.Name)
}
```

### Workflow

测试跨 domain 编排：调用顺序、补偿、外部 client 失败、saga 边界。

- 用 fake service + fake client
- 不 import handler / 生成 DTO
- 不测底层 SQL
- 断言可观察的流程行为

### Handler

仅当 handler 做了超出 `BindAndValidate → service → OK` 的事才测：绑定边界、状态码映射、响应形状、错误响应格式。

用 `ut.PerformRequest`（注册路由）或 `ut.CreateUtRequestContext`（直接测函数）——不走网络。

```go
func TestHelloHandler(t *testing.T) {
    h := server.Default()
    h.GET("/hello/:name", Hello)
    w := ut.PerformRequest(h.Engine, "GET", "/hello/hertz", nil,
        ut.Header{Key: "Connection", Value: "close"})
    resp := w.Result()
    assert.DeepEqual(t, 200, resp.StatusCode())
    assert.DeepEqual(t, `{"message":"hi hertz"}`, string(resp.Body()))
}
```

推荐模式：

```text
非法请求               → 400
service ErrNotFound    → 404
合法请求               → 期望响应 DTO
```

业务规则断言留在 policy / service 测试，不放 handler 测试。

### DAL

集成风格。测试 SQL 映射、repo 实现、事务、错误映射、迁移兼容。

- 可 import `biz/dal`、`biz/domain`
- 禁止 import `biz/handler`、`hertz_gen/model`
- 用真实测试数据库（Testcontainers 或专用测试库）
- 每测试一事务，测完回滚

无自定义 SQL 的 repo 方法不必测。

### Client

测试外部服务包装（位于 `hertz_infra/clienthertz`）：请求构造、响应解码、错误映射、超时/重试。

- HTTP client 用 `httptest.Server`
- Hertz 下游 client stub（`hertz_gen/<app>/**`）用 fake 实现接口
- 不调用真实外部服务
- 不把第三方 SDK 类型泄漏到 service / domain 测试

```go
server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
    w.WriteHeader(http.StatusCreated)
    _, _ = w.Write([]byte(`{"id":"pay_123"}`))
}))
defer server.Close()
```

---

## Mock 与 Fake

**优先手写简单 fake**（domain / policy / service / workflow）。仅当接口大或跨多测试共享时用生成的 mock（`mockery` / `testify/mock`）。

```go
type fakeClock struct{ now time.Time }
func (c fakeClock) Now() time.Time { return c.now }
```

避免：全局可变 mock、真实网络调用、真实时间、sleep 测试、mock 被测代码本身。

用依赖注入传 fake。

---

## 确定性

时间 / ID / 随机需注入，不断言真实时间生成的精确值：

```go
clock := fakeClock{now: time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)}
user := domain.NewUser(name, email, clock.Now())
```

---

## 错误断言

```go
// 哨兵错误
if !errors.Is(err, domain.ErrUserNotFound) { t.Fatalf(...) }

// 类型化错误
var pe *domain.PolicyError
if !errors.As(err, &pe) { t.Fatalf(...) }
```

除非字符串就是被测的公开行为，否则不断言错误字符串。

---

## 覆盖率

优先测试：

```text
业务关键路径、分支规则、错误路径
鉴权决策、金额/定价计算
事务边界、外部 API 包装
```

不要靠测试生成代码、getter、常量、平凡委托来刷覆盖率。

---

## 反模式

| 反模式 | 修正 |
|--------|------|
| 测试生成代码（`hertz_gen/model`、`biz/router`） | 改测 handler 映射或 service 行为 |
| 测试镜像实现（`got != 1+2`） | 断言业务可见行为与边界 |
| service 测试 import Hertz / 生成 DTO | 用 Command |
| 真实外部调用 | 用 fake client 或 httptest.Server |
| `service/<a>` 测试 import `service/<b>` | workflow 测试组合两者 fake |

---

## Review 清单

- 测试断言行为而非实现细节？
- 文件在被测代码旁？
- 不测生成代码（除强理由）？
- 遵守架构 import 规则？
- service 测试避开 Hertz / 生成 DTO？
- policy 测试避开 DAL / 其它 policy 包？
- workflow 测试覆盖跨 domain 编排？
- 时间 / ID / 随机是否确定性？
- 外部系统是否已 fake？
- 错误断言用 `errors.Is` / `errors.As`？
- 真实 bug 时会失败，无害重构时不失败？

---

## 总结

```text
domain   → 纯测试：不变量与值对象
policy   → 规则测试：用 domain 对象
service  → 用例测试：fake repository
workflow → 编排测试：fake service / client
dal      → 集成测试：真实测试数据库
client   → fake server 测试
handler  → HTTP 映射测试（Hertz ut.PerformRequest），仅当非平凡
```

> 如果一条测试需要破坏架构才能写出来，生产代码大概需要一个更好的接缝。

# E2E 测试

## 测试分工

| 测试类型 | 位置 | 驱动方式 | 覆盖 |
|---------|------|---------|------|
| 接口测试（需启动服务、打 HTTP） | `media-cli` | 裸 HTTP + cookie jar（被测服务由 docker-compose 起） | 各服务 HTTP 接口 |
| 接口无关测试（domain 逻辑/算法/迁移） | `<app>/e2e/` | go test + testcontainers | domain 业务逻辑、事务、纯算法 |

## 启动流程（4 步）

```bash
# 1. tearup 基础服务（按需起 infra 子集，dev=只起 pg，local=全部）
INFRA=dev  docker compose -f docker-compose/docker-compose.yml up -d postgres
# 或全部 infra：
INFRA=local docker compose --profile infra -f docker-compose/docker-compose.yml up -d

# 2. tearup 被测试服务
INFRA=local docker compose -f media_auth/docker-compose.yml up -d media_auth

# 3. 执行测试命令（media-cli 子命令，cookie 跨命令复用）
MEDIA_AUTH_ADDR=http://localhost:8001 \
  go -C media-cli run . media_auth auth login --username alice --password secret123
MEDIA_AUTH_ADDR=http://localhost:8001 \
  go -C media-cli run . media_auth auth me
# 或 service 特有子命令（如 media-cli media_example 自己的命令）

# 4. teardown 被测试服务
docker compose -f media_auth/docker-compose.yml down
# 基础服务保留常驻（除非确定不再测试）
```

## docker-compose profiles 与 env_file

### 基础环境（`docker-compose/docker-compose.yml`）

- `postgres` 无 profile（默认启，单元测试/单服务调试必需）
- `otel-collector`/`jaeger`/`prometheus`/`loki`/`grafana`/`promtail`/`consul` 标 `profiles: [infra]`（默认不启，联调时按 profile 启）

### 切换 infra 子集

```bash
# 单元测试/单服务调试：只起 pg（INFRA=unit 用 .env.unit，关其他 infra）
INFRA=unit  docker compose -f docker-compose/docker-compose.yml up -d postgres
# 联调全部：起 pg + 所有 infra（INFRA=e2e 用 .env.e2e，开所有）
INFRA=e2e   docker compose --profile infra -f docker-compose/docker-compose.yml up -d
# 联合启动（基础 + 服务）：
INFRA=e2e   docker compose -f docker-compose/docker-compose.yml -f media_auth/docker-compose.yml up -d
```

### env_file 共享（`docker-compose/.env.<infra>`）

- `.env.unit`：只开 pg（其他 infra=false）
- `.env.e2e`：开全部 infra

被测服务 compose 引用共享 env_file：
```yaml
services:
  media_auth:
    env_file:
      - ../docker-compose/.env.${INFRA:-e2e}   # INFRA 切换 → 自动选 unit/e2e
    environment:
      SERVER_ADDR: ":8001"                    # 服务特有变量（不随 INFRA 变）
      MEDIA_AUTH_DB_DSN: postgres://...@host.docker.internal:5432/media_auth?sslmode=disable
      LOG_DIR: /media_logs/media-auth
    volumes:
      - ../media_logs/media-auth:/media_logs/media-auth   # 日志映射 host（grep debug）
    extra_hosts:
      - "host.docker.internal:host-gateway"             # 容器内访问 host pg
```

## 文件组织

```text
docker-compose/
  docker-compose.yml              # 基础环境（pg + infra services with profiles）
  .env.unit                         # INFRA=unit：只开 pg
  .env.e2e                          # INFRA=e2e：开全部 infra
media_auth/
  Dockerfile
  docker-compose.yml                # media_auth 独立 compose（build + env_file + ports + volumes）
media_example/
  Dockerfile
  docker-compose.yml                # 同上
media-cli/
  e2e/                              # media-cli 的 HTTP e2e 测试（裸 HTTP，cookie 跨命令复用）
```

## 关键约定

- **`media-cli` 不用管服务启停**——那是 docker-compose 的事。media-cli 只发 HTTP 请求。
- **基础环境常驻**（除非确定不再测试才停），避免每次 e2e 重复 build/起。
- **cookie 跨命令复用**：`media-cli media_auth auth login` 下发 cookie 落盘 `media-cli/.cookies/`，后续 `auth me`/`change-password` 自动带。
- **日志映射 host**：`LOG_DIR=/media_logs/<app>` + volume 映射 `../media_logs/<app>/`，便于 `grep` debug。

## 边界

- 不再提供 media-cli `e2e` 编排子命令（用 docker-compose 直管，更灵活）
- 接口无关测试（migration / 事务 / 纯算法）继续用 `<app>/e2e/` 的 go test + testcontainers

// new_server 工具：生成新 Hertz 微服务骨架（health 端点）。
//
// 用法（在仓库根执行）：
//
//	go run ./harness/tools/new_server <new_module> <new_app> <port>
//
// 例：
//
//	go run ./harness/tools/new_server media_test media-test 8003
//
// 流程：
//  1. 验证参数（module 蛇形、app 短横线、port>1024）
//  2. 在 idl/<new_module>/ 下创建 health.proto
//  3. 跑 make hz-model 生成 model（hertz_gen/model/<new_module>/health/）
//  4. 创建 <new_module>/ 目录 + .hz + go.mod（含 replace）
//  5. 跑 hz update 生成 handler/router
//  6. 创建骨架文件（main.go / router.go / router_gen.go / biz/dal 三件套 / conf / Dockerfile / docker-compose / migrations / Makefile 等）
//  7. 改 go.work：追加 use ./<new_module>
//  8. 改 media-cli/conf：加 client.<new_module> 段
//  9. go mod tidy + build 验证
package main

import (
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
)

var repoRoot string

func main() {
	if len(os.Args) != 4 {
		log.Fatalf("usage: new_server <new_module> <new_app> <port>\n  e.g. new_server media_test media-test 8003")
	}
	newModule := os.Args[1]
	newApp := os.Args[2]
	portStr := os.Args[3]

	if err := validate(newModule, newApp, portStr); err != nil {
		log.Fatalf("invalid args: %v", err)
	}

	wd, _ := os.Getwd()
	repoRoot = wd
	idlPath := filepath.Join(repoRoot, "idl")
	newPath := filepath.Join(repoRoot, newModule)
	if _, err := os.Stat(newPath); err == nil {
		log.Fatalf("target already exists: %s", newPath)
	}

	// 1. 创建 IDL
	idlDir := filepath.Join(idlPath, newModule)
	os.MkdirAll(idlDir, 0755)
	healthProto := filepath.Join(idlDir, "health.proto")
	protoContent := fmt.Sprintf(`syntax = "proto3";

package %s.health;

import "api.proto";
import "google/api/annotations.proto";

option go_package = "%s/health";

service HealthService {
  rpc Health(HealthRequest) returns (HealthResponse) {
    option (api.get) = "/health";
    option (google.api.http) = { get: "/health" };
  }
}

message HealthRequest {}

message HealthResponse {
  string status = 1;
}
`, newModule, newModule)
	os.WriteFile(healthProto, []byte(protoContent), 0644)
	log.Printf("IDL: %s", healthProto)

	// 2. 生成 model。Unix 开发环境沿用根 Makefile；Windows 没有 make 时
	// 直接执行等价的 hz model，保证脚手架本身可跨平台运行。
	log.Printf("generate health model...")
	if err := generateHealthModel(newModule); err != nil {
		log.Fatalf("generate health model: %v", err)
	}

	// 3. 创建服务目录 + .hz
	os.MkdirAll(newPath, 0755)
	os.WriteFile(filepath.Join(newPath, ".hz"), []byte("hz version: v0.9.1\n"), 0644)

	// 4. go.mod（含 replace hertz_gen + hertz_infra）
	goModContent := fmt.Sprintf(`module media_agent/%s

go 1.26.3

require (
	github.com/cloudwego/hertz v0.10.5
	github.com/golang-migrate/migrate/v4 v4.19.1
	github.com/google/wire v0.7.0
	github.com/lib/pq v1.12.3
	gorm.io/driver/postgres v1.5.11
	gorm.io/gorm v1.31.2
)

replace (
	media_agent/hertz_gen => ../hertz_gen
	media_agent/hertz_infra => ../hertz_infra
)
`, newModule)
	os.WriteFile(filepath.Join(newPath, "go.mod"), []byte(goModContent), 0644)

	// 5. hz update（生成 handler/router）
	log.Printf("hz update...")
	if err := runCmdInDir(newPath, "hz", "update",
		"--idl", "../idl/"+newModule+"/health.proto",
		"--proto_path", "../idl",
		"--module", "media_agent/"+newModule,
		"--use", "media_agent/hertz_gen/model",
		"--sort_router",
	); err != nil {
		log.Fatalf("hz update: %v", err)
	}
	if err := fixWindowsModelImports(newPath, newModule); err != nil {
		log.Fatalf("fix generated model imports: %v", err)
	}

	// 6. 创建骨架文件
	createSkeletonFiles(newPath, newModule, newApp, portStr)

	// 7. 填充 health handler（status="ok"）
	fillHealthHandler(newPath, newModule)

	// 8. go.work
	if err := addToGoWork(filepath.Join(repoRoot, "go.work"), newModule); err != nil {
		log.Fatalf("go.work: %v", err)
	}
	log.Printf("go.work: added use ./%s", newModule)

	// 9. media-cli 是可选调用方。独立实验 workspace 不包含它时跳过。
	mediaCliConf := filepath.Join(repoRoot, "media-cli/conf")
	if _, err := os.Stat(mediaCliConf); err == nil {
		if err := addMediaCliClient(mediaCliConf, newModule, newApp, portStr); err != nil {
			log.Fatalf("media-cli conf: %v", err)
		}
		log.Printf("media-cli conf: added client.%s", newModule)
	} else if !os.IsNotExist(err) {
		log.Fatalf("media-cli conf: %v", err)
	} else {
		log.Printf("media-cli conf: skipped (directory not present)")
	}

	// 10. go mod tidy + build
	log.Printf("go mod tidy + build...")
	if err := runCmdInDir(newPath, "go", "mod", "tidy"); err != nil {
		log.Fatalf("go mod tidy: %v", err)
	}
	if err := runCmdInDir(newPath, "go", "build", "./..."); err != nil {
		log.Fatalf("go build: %v", err)
	}
	log.Printf("build: OK")

	log.Printf("\n=== done ===")
	log.Printf("next steps:")
	log.Printf("  cd %s", newModule)
	log.Printf("  # verify: go run . (需要先起 postgres)")
	log.Printf("  # test: docker compose -f %s/docker-compose.yml up", newModule)
	log.Printf("  #        curl http://localhost:%s/health", portStr)
}

// createSkeletonFiles 创建骨架文件（main.go / router.go / dal / conf / Dockerfile 等）。
func createSkeletonFiles(newPath, newModule, newApp, portStr string) {
	// 目录
	dirs := []string{
		"conf", "migrations", "script",
		"biz/dal", "biz/domain", "biz/policy", "biz/service",
		"biz/middleware", "biz/workflow", "biz/shared",
	}
	for _, d := range dirs {
		os.MkdirAll(filepath.Join(newPath, d), 0755)
	}

	// main.go
	writeFile(newPath, "main.go", fmt.Sprintf(`package main

import (
	"context"
	"log"
	"os"
	"path/filepath"

	"media_agent/hertz_infra/config"
	"media_agent/hertz_infra/serverhertz"
	"media_agent/%s/biz/dal"
)

func main() {
	loader, err := config.NewLoader()
	if err != nil {
		log.Fatalf("init config loader: %%v", err)
	}
	defer loader.Close()
	cfg := loader.Current()

	logCfg := cfg.GetLog()
	if logCfg.GetDir() == "" {
		logCfg.Dir = resolveDefaultLogDir(cfg.GetApp().GetName())
	}

	suite := serverhertz.NewServerSuite(loader)

	if err := loader.Attach(suite.KVSource()); err != nil {
		log.Fatalf("attach kv source: %%v", err)
	}
	if err := loader.Watch(); err != nil {
		log.Fatalf("start config watch: %%v", err)
	}

	shutdownObs, err := suite.InitObservability()
	if err != nil {
		log.Fatalf("init observability: %%v", err)
	}
	defer shutdownObs(context.Background())

	dbCfg := cfg.GetDatabase()
	db, err := dal.NewDB(dbCfg)
	if err != nil {
		log.Fatalf("init db: %%v", err)
	}
	if err := dal.Migrate("migrations", dbCfg); err != nil {
		log.Fatalf("db migrate: %%v", err)
	}
	_ = db

	h, err := suite.NewServer(context.Background())
	if err != nil {
		log.Fatalf("init hertz server: %%v", err)
	}
	register(h)
	suite.RegisterRoutes(h)

	if err := suite.RegisterService(); err != nil {
		log.Fatalf("register service: %%v", err)
	}
	defer func() {
		if err := suite.DeregisterService(); err != nil {
			log.Printf("deregister service: %%v", err)
		}
	}()

	log.Printf("%s: listening on %%s", cfg.GetServer().GetAddress())
	h.Spin()
}

func resolveDefaultLogDir(appName string) string {
	wd, _ := os.Getwd()
	dir := wd
	for {
		if _, err := os.Stat(filepath.Join(dir, "go.work")); err == nil {
			return filepath.Join(dir, "media_logs", appName)
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return ""
		}
		dir = parent
	}
}
`, newModule, newApp))

	// router.go
	writeFile(newPath, "router.go", `package main

import "github.com/cloudwego/hertz/pkg/app/server"

func customizedRegister(r *server.Hertz) {
	// your code ...
}
`)

	// router_gen.go
	writeFile(newPath, "router_gen.go", fmt.Sprintf(`package main

import (
	"github.com/cloudwego/hertz/pkg/app/server"
	router "media_agent/%s/biz/router"
)

func register(r *server.Hertz) {
	router.GeneratedRegister(r)
	customizedRegister(r)
}
`, newModule))

	// biz/dal/db.go
	writeFile(newPath, "biz/dal/db.go", `package dal

import (
	"fmt"
	"time"

	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	gormlogger "gorm.io/gorm/logger"

	configpb "media_agent/hertz_gen/config"
)

func NewDB(cfg *configpb.DatabaseConfig) (*gorm.DB, error) {
	if cfg == nil || !cfg.GetEnabled() {
		return nil, nil
	}
	switch cfg.GetDriver() {
	case "postgres", "":
		db, err := gorm.Open(postgres.Open(cfg.GetDsn()), &gorm.Config{
			Logger: gormlogger.Default.LogMode(gormlogger.Warn),
		})
		if err != nil {
			return nil, fmt.Errorf("dal: open postgres: %w", err)
		}
		sqlDB, err := db.DB()
		if err != nil {
			return nil, fmt.Errorf("dal: get *sql.DB: %w", err)
		}
		if n := int(cfg.GetMaxOpenConns()); n > 0 {
			sqlDB.SetMaxOpenConns(n)
		}
		if n := int(cfg.GetMaxIdleConns()); n > 0 {
			sqlDB.SetMaxIdleConns(n)
		}
		if s := cfg.GetConnMaxLifetimeSeconds(); s > 0 {
			sqlDB.SetConnMaxLifetime(time.Duration(s) * time.Second)
		}
		return db, nil
	default:
		return nil, fmt.Errorf("dal: unsupported database driver %q", cfg.GetDriver())
	}
}
`)

	// biz/dal/migrate.go
	writeFile(newPath, "biz/dal/migrate.go", `package dal

import (
	"database/sql"
	"fmt"
	"os"

	"github.com/golang-migrate/migrate/v4"
	"github.com/golang-migrate/migrate/v4/database/postgres"
	"github.com/golang-migrate/migrate/v4/source/iofs"
	_ "github.com/lib/pq"

	configpb "media_agent/hertz_gen/config"
)

func Migrate(migrationsDir string, cfg *configpb.DatabaseConfig) error {
	if cfg == nil || !cfg.GetMigrateOnStart() {
		return nil
	}
	dsn := cfg.GetDsn()
	if dsn == "" {
		return fmt.Errorf("dal: migrate: empty dsn")
	}
	if migrationsDir == "" {
		return fmt.Errorf("dal: migrate: empty migrations dir")
	}
	if _, err := os.Stat(migrationsDir); err != nil {
		return fmt.Errorf("dal: migrate dir %q: %w", migrationsDir, err)
	}

	src, err := iofs.New(os.DirFS(migrationsDir), ".")
	if err != nil {
		return fmt.Errorf("dal: migrate source: %w", err)
	}

	sqlDB, err := sql.Open("postgres", dsn)
	if err != nil {
		return fmt.Errorf("dal: open sql for migrate: %w", err)
	}
	defer sqlDB.Close()

	dbDriver, err := postgres.WithInstance(sqlDB, &postgres.Config{})
	if err != nil {
		return fmt.Errorf("dal: postgres migrate driver: %w", err)
	}

	m, err := migrate.NewWithInstance("iofs", src, "postgres", dbDriver)
	if err != nil {
		return fmt.Errorf("dal: new migrate: %w", err)
	}
	defer m.Close()

	if err := m.Up(); err != nil && err != migrate.ErrNoChange {
		return fmt.Errorf("dal: migrate up: %w", err)
	}
	return nil
}
`)

	// biz/dal/transaction.go
	writeFile(newPath, "biz/dal/transaction.go", `package dal

import (
	"context"

	"gorm.io/gorm"
)

type TxRunner interface {
	RunInTx(ctx context.Context, fn func(ctx context.Context) error) error
}

type txRunner struct{ db *gorm.DB }

func NewTxRunner(db *gorm.DB) TxRunner {
	if db == nil {
		return nil
	}
	return &txRunner{db: db}
}

func (r *txRunner) RunInTx(ctx context.Context, fn func(ctx context.Context) error) error {
	return r.db.Transaction(func(tx *gorm.DB) error {
		return fn(context.WithValue(ctx, ctxKey{}, tx))
	})
}

type ctxKey struct{}

func WithTx(ctx context.Context, tx *gorm.DB) context.Context {
	return context.WithValue(ctx, ctxKey{}, tx)
}

func FromContext(ctx context.Context, db *gorm.DB) *gorm.DB {
	if tx, ok := ctx.Value(ctxKey{}).(*gorm.DB); ok && tx != nil {
		return tx
	}
	return db
}
`)

	// biz/dal/wire_set.go
	writeFile(newPath, "biz/dal/wire_set.go", `package dal

import "github.com/google/wire"

var ProviderSet = wire.NewSet()
`)

	// biz/dal/doc.go
	writeFile(newPath, "biz/dal/doc.go", "package dal\n")

	// doc.go 文件
	for _, d := range []string{"domain", "policy", "service", "middleware", "workflow", "shared"} {
		writeFile(newPath, "biz/"+d+"/doc.go", "package "+d+"\n")
	}

	// .env
	writeFile(newPath, ".env", "APP_ENV=local\n")

	// .gitignore
	writeFile(newPath, ".gitignore", "output/\ntmp/\n")

	// CLAUDE.md
	writeFile(newPath, "CLAUDE.md", "# CLAUDE.md\n\n## Development\n\nFollow [architecture.md](../harness/contributing/architecture.md)\n")

	// build.sh
	writeFile(newPath, "build.sh", "#!/bin/bash\nRUN_NAME=hertz_service\nmkdir -p output/bin\ngo build -o output/bin/${RUN_NAME}\n")
	os.Chmod(filepath.Join(newPath, "build.sh"), 0755)

	// Makefile
	writeFile(newPath, "Makefile", fmt.Sprintf(`HERTZ_GEN_MODEL ?= media_agent/hertz_gen/model

.PHONY: run
run:
	go run .

.PHONY: test
test:
	go test ./...

.PHONY: arch-check
arch-check:
	go run ../harness/tools/archcheck

.PHONY: check
check: arch-check test

.PHONY: tidy
tidy:
	go mod tidy

.PHONY: fmt
fmt:
	gofmt -w .

BIZ_PROTOS := $(shell find ../idl/%s -type f -name '*.proto' | sed 's|^\.\./idl/||')

.PHONY: hz-update
hz-update:
	@for p in $(BIZ_PROTOS); do \
		echo ">> hz update $$p"; \
		hz update --idl ../idl/$$p --proto_path ../idl --use $(HERTZ_GEN_MODEL) --sort_router || exit 1; \
	done

.PHONY: hz-gen
hz-gen: hz-update

.PHONY: swagger-gen
swagger-gen:
	@mkdir -p swagger
	protoc --proto_path=../idl --openapi_out=swagger --openapi_opt=naming=proto $(BIZ_PROTOS)

.PHONY: migrate-new
migrate-new:
	@test -n "$(NAME)" || { echo "usage: make migrate-new NAME=add_xxx"; exit 1; }
	migrate create -ext sql -dir migrations -seq $(NAME)

.PHONY: migrate-up
migrate-up:
	migrate -path migrations -database "$$PG_DSN" up

.PHONY: migrate-down
migrate-down:
	migrate -path migrations -database "$$PG_DSN" down 1

.PHONY: lefthook-install
lefthook-install:
	go install github.com/evilmartians/lefthook@latest
	lefthook install
`, newModule))

	// lefthook.yml
	writeFile(newPath, "lefthook.yml", `pre-commit:
  commands:
    gofmt:
      glob: "*.go"
      run: gofmt -w {staged_files} && git add {staged_files}
    arch-check:
      run: go run ../harness/tools/archcheck
    test:
      run: go test ./...
`)

	// staticcheck.conf
	writeFile(newPath, "staticcheck.conf", "")

	// script/bootstrap.sh
	writeFile(newPath, "script/bootstrap.sh", "#!/bin/bash\nCURDIR=$(cd $(dirname $0); pwd)\nBinaryName=hertz_service\necho \"$CURDIR/bin/${BinaryName}\"\nexec $CURDIR/bin/${BinaryName}\n")
	os.Chmod(filepath.Join(newPath, "script/bootstrap.sh"), 0755)

	// conf/local.yaml
	confContent := fmt.Sprintf(`app:
  name: %s
  version: v1
  env: ${APP_ENV:-local}

server:
  enabled: true
  address: ${SERVER_ADDR:-:%s}

database:
  enabled: true
  driver: postgres
  dsn: ${POSTGRES_DSN:-postgres://media_auth:media_auth@127.0.0.1:5432/%s?sslmode=disable}
  max_open_conns: 50
  max_idle_conns: 10
  migrate_on_start: true

consul:
  enabled: ${CONSUL_ENABLED:-false}

consul_kv:
  enabled: ${CONSUL_KV_ENABLED:-false}

otel:
  enabled: ${OTEL_ENABLED:-false}

prometheus:
  enabled: ${PROMETHEUS_ENABLED:-false}

access_log:
  enabled: true
  format: "[${time}] ${status} - ${latency} ${method} ${path}"
  time_format: "2006-01-02T15:04:05Z"

log:
  enabled: true
  level: ${LOG_LEVEL:-debug}
  dir: ${LOG_DIR:-}
  filename: app.log
  max_size_mb: 100
  max_backups: 10
  max_age_days: 7
  compress: true
  console: true

recovery:
  enabled: true
  print_stack: true

request_id:
  enabled: true
  header: "X-Request-ID"

cors:
  enabled: true
  allow_origins: ["*"]
  allow_methods: ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
  allow_headers: ["*"]
  allow_credentials: false
  max_age_seconds: 600

pprof:
  enabled: true
  prefix: "/debug/pprof"

health:
  enabled: true
  path: "/health"
`, newApp, portStr, newApp)
	writeFile(newPath, "conf/local.yaml", confContent)
	// dev.yaml = local 但 env=dev
	devContent := strings.Replace(confContent, "APP_ENV:-local", "APP_ENV:-dev", 1)
	writeFile(newPath, "conf/dev.yaml", devContent)

	// migrations
	writeFile(newPath, "migrations/000001_init.up.sql", fmt.Sprintf("CREATE SCHEMA IF NOT EXISTS %s;\n", newApp))
	writeFile(newPath, "migrations/000001_init.down.sql", fmt.Sprintf("DROP SCHEMA IF EXISTS %s CASCADE;\n", newApp))

	// Dockerfile
	writeFile(newPath, "Dockerfile", fmt.Sprintf(`FROM golang:1.26-alpine AS builder
RUN apk add --no-cache git ca-certificates
ENV GOPROXY=https://goproxy.cn,direct
ENV GOSUMDB=off
WORKDIR /workspace
COPY hertz_gen/go.mod hertz_gen/go.sum hertz_gen/
COPY hertz_infra/go.mod hertz_infra/go.sum hertz_infra/
COPY %s/go.mod %s/go.sum %s/
RUN cd %s && GOWORK=off go mod download
COPY hertz_gen/ hertz_gen/
COPY hertz_infra/ hertz_infra/
COPY %s/ %s/
RUN cd %s && GOWORK=off CGO_ENABLED=0 go build -o /out/%s .

FROM alpine:3.20
RUN apk add --no-cache ca-certificates tzdata
WORKDIR /app
COPY --from=builder /out/%s /app/%s
COPY %s/migrations/ /app/migrations/
COPY %s/conf/ /app/conf/
COPY %s/.env /app/.env
EXPOSE %s
ENTRYPOINT ["/app/%s"]
`, newModule, newModule, newModule, newModule,
		newModule, newModule, newModule, newModule,
		newModule, newModule, newModule, newModule, newModule,
		portStr, newModule))

	// docker-compose.yml
	writeFile(newPath, "docker-compose.yml", fmt.Sprintf(`services:
  %s:
    build:
      context: ..
      dockerfile: %s/Dockerfile
    ports:
      - "%s:%s"
    env_file:
      - ../docker-compose/.env.${INFRA:-e2e}
    environment:
      SERVER_ADDR: ":%s"
      POSTGRES_DSN: postgres://media_auth:media_auth@host.docker.internal:5432/%s?sslmode=disable
      LOG_DIR: /media_logs/%s
    volumes:
      - ../media_logs/%s:/media_logs/%s
    extra_hosts:
      - "host.docker.internal:host-gateway"
    restart: unless-stopped
`, newModule, newModule, portStr, portStr, portStr, newApp, newApp, newApp, newApp))
}

// fillHealthHandler 填充 health handler（status="ok"）。
func fillHealthHandler(newPath, newModule string) {
	handlerPath := filepath.Join(newPath, "biz/handler/health/health_service.go")
	data, err := os.ReadFile(handlerPath)
	if err != nil {
		log.Printf("warn: health handler not found: %v", err)
		return
	}
	content := string(data)
	// 替换 `resp := new(health.HealthResponse)` 为 `resp := &health.HealthResponse{Status: "ok"}`
	content = strings.Replace(content,
		"resp := new(health.HealthResponse)",
		`resp := &health.HealthResponse{Status: "ok"}`,
		1)
	os.WriteFile(handlerPath, []byte(content), 0644)
	log.Printf("health handler: status=ok")
}

func validate(newModule, newApp, portStr string) error {
	if newModule == "" {
		return fmt.Errorf("new_module is empty")
	}
	if newApp == "" {
		return fmt.Errorf("new_app is empty")
	}
	port, err := strconv.Atoi(portStr)
	if err != nil {
		return fmt.Errorf("port must be numeric: %s", portStr)
	}
	if port <= 1024 {
		return fmt.Errorf("port must be > 1024 (avoid system ports)")
	}
	if !regexp.MustCompile(`^[a-z][a-z0-9_]*$`).MatchString(newModule) {
		return fmt.Errorf("new_module must be snake_case (e.g. media_test)")
	}
	if !regexp.MustCompile(`^[a-z][a-z0-9-]*$`).MatchString(newApp) {
		return fmt.Errorf("new_app must be kebab-case (e.g. media-test)")
	}
	return nil
}

func generateHealthModel(newModule string) error {
	if _, err := exec.LookPath("make"); err == nil {
		return runCmd("make", "hz-model")
	}

	return runCmdInDir(filepath.Join(repoRoot, "hertz_gen"), "hz", "model",
		"--module", "media_agent/hertz_gen",
		"--model_dir", "model",
		"--idl", "../idl/"+newModule+"/health.proto",
		"--proto_path=../idl",
	)
}

// hz --use currently normalizes the shared model path incorrectly on Windows.
// Keep generation deterministic by correcting only that generated import path.
func fixWindowsModelImports(newPath, newModule string) error {
	wrong := "media_agent/" + newModule + "/biz/model/" + newModule + "/"
	right := "media_agent/hertz_gen/model/" + newModule + "/"
	return filepath.Walk(filepath.Join(newPath, "biz"), func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() || filepath.Ext(path) != ".go" {
			return nil
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		updated := strings.ReplaceAll(string(data), wrong, right)
		if updated == string(data) {
			return nil
		}
		return os.WriteFile(path, []byte(updated), 0644)
	})
}

func writeFile(base, rel, content string) {
	path := filepath.Join(base, rel)
	os.MkdirAll(filepath.Dir(path), 0755)
	os.WriteFile(path, []byte(content), 0644)
}

func addToGoWork(goWorkPath, newModule string) error {
	data, err := os.ReadFile(goWorkPath)
	if err != nil {
		return err
	}
	entry := "\t./" + newModule + "\n"
	if strings.Contains(string(data), entry) {
		return nil
	}
	content := string(data)
	idx := strings.Index(content, "use (")
	if idx < 0 {
		return fmt.Errorf("go.work has no 'use (' block")
	}
	insertAt := strings.Index(content[idx:], "\n")
	if insertAt < 0 {
		return fmt.Errorf("go.work format unexpected")
	}
	insertAt += idx + 1
	newContent := content[:insertAt] + entry + content[insertAt:]
	return os.WriteFile(goWorkPath, []byte(newContent), 0644)
}

func addMediaCliClient(confDir, newModule, newApp, portStr string) error {
	envVarName := strings.ReplaceAll(strings.ToUpper(newApp), "-", "_") + "_ADDR"
	clientBlock := fmt.Sprintf("  %s:\n"+
		"    enabled: true\n"+
		"    service_name: %s\n"+
		"    base_domain: ${%s:-http://127.0.0.1:%s}\n"+
		"    use_discovery: false\n"+
		"    timeout:\n"+
		"      enabled: true\n"+
		"      connect_timeout_ms: 200\n"+
		"      read_timeout_ms: 5000\n"+
		"      write_timeout_ms: 5000\n"+
		"      request_timeout_ms: 10000\n",
		newModule, newApp, envVarName, portStr,
	)
	for _, f := range []string{"local.yaml", "dev.yaml"} {
		path := filepath.Join(confDir, f)
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		content := string(data)
		idx := strings.Index(content, "client:")
		if idx < 0 {
			continue
		}
		endIdx := strings.Index(content[idx:], "\n")
		insertAt := idx + endIdx + 1
		newContent := content[:insertAt] + "\n" + clientBlock + content[insertAt:]
		if err := os.WriteFile(path, []byte(newContent), 0644); err != nil {
			return err
		}
	}
	return nil
}

func runCmd(name string, args ...string) error {
	cmd := exec.Command(name, args...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	return cmd.Run()
}

func runCmdInDir(dir, name string, args ...string) error {
	cmd := exec.Command(name, args...)
	cmd.Dir = dir
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	return cmd.Run()
}

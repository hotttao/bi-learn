// gormgen 是 GORM Gen 代码生成器的工作区级共享入口。
//
// 各微服务在自己的 tools/gormgen/ 目录下维护 service-specific 的生成配置
// （引用本服务的 biz/dal/model 类型）。本文件仅作为通用模板与文档说明，
// 不直接引用任何具体服务的 model 包。
//
// 服务侧用法（在 <service>/tools/gormgen/main.go 中）：
//
//	package main
//
//	import (
//	    "log"
//	    "os"
//
//	    "gorm.io/driver/mysql"
//	    "gorm.io/gen"
//	    "gorm.io/gorm"
//
//	    "media_agent/<service>/biz/dal/model"
//	)
//
//	func main() {
//	    dsn := os.Getenv("MYSQL_DSN")
//	    if dsn == "" {
//	        log.Fatal("MYSQL_DSN is required for GORM Gen")
//	    }
//	    db, err := gorm.Open(mysql.Open(dsn), &gorm.Config{})
//	    if err != nil {
//	        log.Fatalf("open mysql: %v", err)
//	    }
//	    g := gen.NewGenerator(gen.Config{
//	        OutPath:      "biz/dal/query",
//	        ModelPkgPath: "biz/dal/model",
//	        Mode:         gen.WithDefaultQuery | gen.WithQueryInterface,
//	    })
//	    g.UseDB(db)
//	    g.ApplyBasic(
//	        model.FooRecord{},
//	        // ... 列出该服务所有需要生成 query 的 row model
//	    )
//	    g.Execute()
//	}
//
// 规则：
// - 每个微服务在自己的 tools/gormgen/ 下维护本服务的生成入口，不在 harness/tools/ 内硬编码业务 model。
// - harness/tools/gormgen/ 仅保留本模板，供新服务复制起步。
// - 生成的 query 代码归 hertz-owned，禁止手改（见 contributing/architecture.md「Generated Code Rules」）。
package main

func main() {
	panic("harness/tools/gormgen is a template only; copy it into <service>/tools/gormgen/ and customize the model list")
}

// new_ui 工具：参考 ui_example 生成新前端项目骨架。
//
// 用法（在仓库根执行）：
//
//	go run ./harness/tools/new_ui <name>
//
// 例：
//
//	go run ./harness/tools/new_ui ui_platform
//
// 流程：
//  1. 校验参数（name 蛇形，如 ui_platform）+ 定位源 (repoRoot/ui_example)
//  2. git ls-files 拿源项目受版本控制的文件（自动跳过 node_modules/dist/.vite 等）
//  3. 逐文件复制到 repoRoot/<name>，文本文件做项目名标识替换 + 历史溯源清理
//  4. 输出摘要 + 后续步骤（yarn install && yarn dev）
//
// 标识替换：
//   - ui-example → <kebab>  覆盖 @media/ui-example、index.html title、App.tsx h1、favicon/logo
//   - ui_example → <name>   目录名/路径形式
//
// 与 new_server 不同：本工具不自动跑 yarn install（需联网 + husky 在 monorepo 根的副作用），
// 生成后请手动执行 next steps 中的命令验证。
package main

import (
	"bytes"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
)

var repoRoot string

func main() {
	if len(os.Args) != 2 {
		log.Fatalf("usage: new_ui <name>\n  e.g. new_ui ui_platform")
	}
	name := os.Args[1]

	if err := validateName(name); err != nil {
		log.Fatalf("invalid args: %v", err)
	}

	wd, _ := os.Getwd()
	repoRoot = wd
	source := filepath.Join(repoRoot, "ui_example")
	target := filepath.Join(repoRoot, name)

	if _, err := os.Stat(source); err != nil {
		log.Fatalf("source not found: %s (run from repo root)", source)
	}
	// 目标：不存在或空目录则用；非空则报错（保护已有内容）。
	if entries, err := os.ReadDir(target); err == nil && len(entries) > 0 {
		log.Fatalf("target not empty: %s", target)
	}
	if err := os.MkdirAll(target, 0o755); err != nil {
		log.Fatalf("mkdir target: %v", err)
	}

	files, err := gitLsFiles(source)
	if err != nil {
		log.Fatalf("git ls-files: %v", err)
	}
	if len(files) == 0 {
		log.Fatalf("no tracked files in source: %s", source)
	}

	kebab := toKebab(name)
	scoped := "@media/" + kebab

	copied, rewritten := 0, 0
	for _, rel := range files {
		srcPath := filepath.Join(source, rel)
		dstPath := filepath.Join(target, rel)
		if err := os.MkdirAll(filepath.Dir(dstPath), 0o755); err != nil {
			log.Fatalf("mkdir %s: %v", filepath.Dir(dstPath), err)
		}
		buf, err := os.ReadFile(srcPath)
		if err != nil {
			log.Fatalf("read %s: %v", rel, err)
		}
		mode := os.FileMode(0o644)
		if fi, err := os.Stat(srcPath); err == nil {
			mode = fi.Mode().Perm()
		}
		content := buf
		// 含 NUL 字节视为二进制原样复制（与 git 一致）；否则做文本替换。
		if bytes.IndexByte(buf, 0) < 0 {
			rewrittenStr := rewrite(string(buf), name, kebab, target)
			if rewrittenStr != string(buf) {
				rewritten++
			}
			content = []byte(rewrittenStr)
		}
		if err := os.WriteFile(dstPath, content, mode); err != nil {
			log.Fatalf("write %s: %v", rel, err)
		}
		copied++
	}

	log.Printf("scaffolded %s -> %s", scoped, target)
	log.Printf("  %d files copied, %d rewritten", copied, rewritten)
	log.Printf("\n=== done ===")
	log.Printf("next steps:")
	log.Printf("  cd %s", name)
	log.Printf("  yarn install")
	log.Printf("  yarn dev   # http://localhost:5173")
}

// rewrite 对文本内容做历史溯源清理 + 项目名标识替换。
// 顺序：先清理溯源（片段替换，避开 em dash 等特殊字符以保证匹配），再通用替换标识。
func rewrite(text, name, kebab, target string) string {
	out := text

	// 1. 历史溯源清理
	// 1a. README 顶部 Windows 源路径 → 目标绝对路径
	out = strings.ReplaceAll(out, "D:\\Code\\media\\media_v2\\ui_example", target)
	// 1b. 删除 README 顶部 koala 溯源从句
	out = strings.ReplaceAll(out,
		"`, initialized by mirroring the architecture and tooling from `D:\\Code\\koala\\ui\\contributing`.",
		"`.",
	)
	// 1c. 删除 index.html description 的 koala 溯源括注
	out = strings.ReplaceAll(out, " (architecture aligns with koala/ui/contributing)", "")

	// 2. 项目名标识替换
	out = strings.ReplaceAll(out, "ui-example", kebab)
	out = strings.ReplaceAll(out, "ui_example", name)

	return out
}

func gitLsFiles(source string) ([]string, error) {
	cmd := exec.Command("git", "-C", source, "ls-files")
	var out bytes.Buffer
	cmd.Stdout = &out
	if err := cmd.Run(); err != nil {
		return nil, err
	}
	trimmed := strings.TrimSpace(out.String())
	if trimmed == "" {
		return nil, nil
	}
	return strings.Split(trimmed, "\n"), nil
}

func validateName(name string) error {
	if !regexp.MustCompile(`^[a-z][a-z0-9_]*$`).MatchString(name) {
		return fmt.Errorf("name must be snake_case (e.g. ui_platform)")
	}
	return nil
}

func toKebab(name string) string {
	return strings.ReplaceAll(strings.ToLower(name), "_", "-")
}

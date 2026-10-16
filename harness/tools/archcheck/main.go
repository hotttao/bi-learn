package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

type GoPackage struct {
	ImportPath  string   `json:"ImportPath"`
	Dir         string   `json:"Dir"`
	Imports     []string `json:"Imports"`
	TestImports []string `json:"TestImports"`
}

type Rule struct {
	Name       string
	Comment    string
	FromPrefix string
	ToPrefixes []string
	ToImports  []string
}

type SiblingRule struct {
	Name        string
	Comment     string
	LayerPrefix string
}

type Violation struct {
	RuleName   string
	Comment    string
	FromPkg    string
	FromDir    string
	ToImport   string
	ToDir      string
	IsExternal bool
}

type IDLViolation struct {
	RuleName string
	Comment  string
	File     string
	Expected string
	Actual   string
}

func main() {
	strictIDL := flag.Bool("strict-idl", false, "enforce idl/<feature>/*.proto go_package last segment == feature")
	flag.Parse()

	root, err := repoRoot()
	if err != nil {
		fail("find repo root: %v", err)
	}

	modulePath, err := goModulePath(root)
	if err != nil {
		fail("read go module path: %v", err)
	}

	pkgs, err := goList()
	if err != nil {
		fail("go list: %v", err)
	}

	byImport := make(map[string]GoPackage, len(pkgs))
	for _, pkg := range pkgs {
		byImport[pkg.ImportPath] = pkg
	}

	var violations []Violation

	for _, from := range pkgs {
		fromRel := normalizeArchPath(relDir(root, from.Dir))
		if !shouldCheckFrom(fromRel) {
			continue
		}

		imports := append([]string{}, from.Imports...)
		imports = append(imports, from.TestImports...)

		for _, imp := range imports {
			to, isInternal := byImport[imp]
			toRel := ""

			if isInternal {
				toRel = normalizeArchPath(relDir(root, to.Dir))
				toRel = relDir(root, to.Dir)
			}

			violations = append(violations, checkLayerRules(from, fromRel, imp, toRel, isInternal)...)

			if isInternal {
				violations = append(violations, checkSiblingRules(from, fromRel, imp, toRel)...)
			}
		}
	}

	var idlViolations []IDLViolation
	if *strictIDL {
		idlViolations, err = checkStrictProtoGoPackageRules(root)
		if err != nil {
			fail("check protobuf go_package rules: %v", err)
		}
	}

	if len(violations) > 0 || len(idlViolations) > 0 {
		printViolations(modulePath, violations, idlViolations)
		os.Exit(1)
	}

	fmt.Println("architecture check passed")
}

func shouldCheckFrom(fromRel string) bool {
	return fromRel == "." ||
		hasPathPrefix(fromRel, "biz") ||
		hasPathPrefix(fromRel, "cmd") ||
		hasPathPrefix(fromRel, "tools")
}

// normalizeArchPath strips the per-service directory prefix so that workspace
// paths like `media_materials/biz/domain/foo` collapse to the canonical
// `biz/domain/foo` form the rules match against. Shared workspace roots
// (`hertz_gen/`, `hertz_infra/`) are returned unchanged.
func normalizeArchPath(rel string) string {
	rel = filepath.ToSlash(rel)
	for _, seg := range []string{"biz/", "hertz_gen/", "hertz_infra/"} {
		if idx := strings.Index(rel, seg); idx >= 0 {
			return rel[idx:]
		}
	}
	return rel
}

func checkLayerRules(from GoPackage, fromRel string, imp string, toRel string, isInternal bool) []Violation {
	var violations []Violation

	for _, rule := range hertzArchitectureRules() {
		if !hasPathPrefix(fromRel, rule.FromPrefix) {
			continue
		}

		if isInternal && matchesAnyPathPrefix(toRel, rule.ToPrefixes) {
			violations = append(violations, Violation{
				RuleName: rule.Name,
				Comment:  rule.Comment,
				FromPkg:  from.ImportPath,
				FromDir:  fromRel,
				ToImport: imp,
				ToDir:    toRel,
			})
			continue
		}

		if !isInternal && matchesAnyImportPrefix(imp, rule.ToImports) {
			violations = append(violations, Violation{
				RuleName:   rule.Name,
				Comment:    rule.Comment,
				FromPkg:    from.ImportPath,
				FromDir:    fromRel,
				ToImport:   imp,
				IsExternal: true,
			})
			continue
		}
	}

	return violations
}

func checkSiblingRules(from GoPackage, fromRel string, imp string, toRel string) []Violation {
	var violations []Violation

	for _, rule := range hertzSiblingRules() {
		fromFeature := firstChildAfter(fromRel, rule.LayerPrefix)
		toFeature := firstChildAfter(toRel, rule.LayerPrefix)

		if fromFeature == "" || toFeature == "" {
			continue
		}

		if fromFeature == toFeature {
			continue
		}

		violations = append(violations, Violation{
			RuleName: rule.Name,
			Comment:  rule.Comment,
			FromPkg:  from.ImportPath,
			FromDir:  fromRel,
			ToImport: imp,
			ToDir:    toRel,
		})
	}

	return violations
}

func hertzArchitectureRules() []Rule {
	return []Rule{
		{
			Name:       "domain-must-not-import-outer-layers",
			Comment:    "domain is the stable business core and must not depend on outer layers or Hertz",
			FromPrefix: "biz/domain",
			ToPrefixes: []string{
				"hertz_gen/model", "biz/handler", "biz/router", "biz/middleware",
				"biz/policy", "biz/service", "biz/workflow", "biz/dal", "biz/client",
				"biz/infra", "hertz_infra",
				"biz/bootstrap", "biz/app", "biz/wire",
			},
			ToImports: []string{"github.com/cloudwego/hertz"},
		},
		{
			Name:       "policy-must-not-import-framework-or-infrastructure",
			Comment:    "policy may depend on domain, but not on Hertz, generated DTOs, handlers, routers, DAL, clients, services, workflows, or startup wiring",
			FromPrefix: "biz/policy",
			ToPrefixes: []string{
				"hertz_gen/model", "biz/handler", "biz/router", "biz/middleware",
				"biz/service", "biz/workflow", "biz/dal", "biz/client",
				"biz/infra", "hertz_infra",
				"biz/bootstrap", "biz/app", "biz/wire",
			},
			ToImports: []string{"github.com/cloudwego/hertz"},
		},
		{
			Name:       "service-must-not-import-web-or-concrete-infrastructure",
			Comment:    "service accepts commands and depends on domain interfaces, not Hertz, DTOs, handlers, routers, concrete DAL, clients, workflows, or startup wiring",
			FromPrefix: "biz/service",
			ToPrefixes: []string{
				"hertz_gen/model", "biz/handler", "biz/router", "biz/middleware",
				"biz/workflow", "biz/dal", "biz/client",
				"biz/infra", "hertz_infra",
				"biz/bootstrap", "biz/app", "biz/wire",
			},
			ToImports: []string{"github.com/cloudwego/hertz"},
		},
		{
			Name:       "workflow-must-not-import-web-or-raw-dal",
			Comment:    "workflow orchestrates services and clients, but must not depend on handlers, routers, DTOs, raw DAL, startup wiring, or Hertz",
			FromPrefix: "biz/workflow",
			ToPrefixes: []string{
				"hertz_gen/model", "biz/handler", "biz/router", "biz/middleware",
				"biz/dal/query", "biz/dal/repo",
				"biz/infra", "hertz_infra",
				"biz/bootstrap", "biz/app", "biz/wire",
			},
			ToImports: []string{"github.com/cloudwego/hertz"},
		},
		{
			Name:       "dal-must-not-import-application-or-web-layers",
			Comment:    "DAL implements domain interfaces and must not depend on handlers, routers, DTOs, policies, services, workflows, startup wiring, or Hertz",
			FromPrefix: "biz/dal",
			ToPrefixes: []string{
				"hertz_gen/model", "biz/handler", "biz/router", "biz/middleware",
				"biz/policy", "biz/service", "biz/workflow",
				"biz/infra", "hertz_infra",
				"biz/bootstrap", "biz/app", "biz/wire",
			},
			ToImports: []string{"github.com/cloudwego/hertz"},
		},
		{
			Name:       "client-must-not-import-application-or-web-layers",
			Comment:    "external clients should not depend on handlers, routers, generated DTOs, services, workflows, DAL, startup wiring, or Hertz",
			FromPrefix: "biz/client",
			ToPrefixes: []string{
				"hertz_gen/model", "biz/handler", "biz/router", "biz/middleware",
				"biz/service", "biz/workflow", "biz/dal",
				"biz/infra", "hertz_infra",
				"biz/bootstrap", "biz/app", "biz/wire",
			},
			ToImports: []string{"github.com/cloudwego/hertz"},
		},
	}
}

func hertzSiblingRules() []SiblingRule {
	return []SiblingRule{
		{
			Name:        "no-cross-service-feature",
			Comment:     "service feature packages must not import other service feature packages; use workflow for cross-feature orchestration",
			LayerPrefix: "biz/service",
		},
		{
			Name:        "no-cross-policy-feature",
			Comment:     "policy feature packages must not import other policy feature packages; compose policies from service or workflow",
			LayerPrefix: "biz/policy",
		},
		{
			Name:        "no-cross-handler-feature",
			Comment:     "handler feature packages should not import other handler feature packages; call service or workflow instead",
			LayerPrefix: "biz/handler",
		},
	}
}

func checkStrictProtoGoPackageRules(root string) ([]IDLViolation, error) {
	idlDir := filepath.Join(root, "idl")
	if _, err := os.Stat(idlDir); err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}

	var violations []IDLViolation

	err := filepath.WalkDir(idlDir, func(file string, d os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}

		if d.IsDir() || filepath.Ext(file) != ".proto" {
			return nil
		}

		rel := relDir(root, file)
		if isExemptProto(rel) {
			return nil
		}

		feature := expectedFeatureFromProtoPath(rel)
		if feature == "" {
			return nil
		}

		content, err := os.ReadFile(file)
		if err != nil {
			return err
		}

		goPkg, ok := extractGoPackage(string(content))
		if !ok {
			violations = append(violations, IDLViolation{
				RuleName: "proto-go-package-required",
				Comment:  "strict IDL mode requires project feature proto files to declare option go_package",
				File:     rel,
				Expected: "go_package ending with /" + feature,
				Actual:   "<missing>",
			})
			return nil
		}

		last := lastPathSegment(goPackageImportPath(goPkg))
		if last != feature {
			violations = append(violations, IDLViolation{
				RuleName: "proto-go-package-last-segment-must-match-feature",
				Comment:  "strict IDL mode expects Hertz generated handler/router directory to match idl/<feature>",
				File:     rel,
				Expected: "go_package ending with /" + feature,
				Actual:   goPkg,
			})
		}

		return nil
	})

	if err != nil {
		return nil, err
	}

	return violations, nil
}

func isExemptProto(rel string) bool {
	rel = filepath.ToSlash(rel)
	base := filepath.Base(rel)

	if base == "api.proto" {
		return true
	}

	return strings.Contains(rel, "/google/") ||
		strings.Contains(rel, "/third_party/") ||
		strings.Contains(rel, "/vendor/")
}

func expectedFeatureFromProtoPath(rel string) string {
	rel = filepath.ToSlash(strings.Trim(rel, "/"))
	if !strings.HasPrefix(rel, "idl/") {
		return ""
	}

	withoutPrefix := strings.TrimPrefix(rel, "idl/")
	parts := strings.Split(withoutPrefix, "/")
	if len(parts) == 0 {
		return ""
	}

	if len(parts) == 1 {
		name := strings.TrimSuffix(parts[0], ".proto")
		if name == "" || name == "api" {
			return ""
		}
		return name
	}

	feature := parts[0]
	switch feature {
	case "", "api", "common", "shared", "google", "third_party", "vendor":
		return ""
	default:
		return feature
	}
}

var goPackageRE = regexp.MustCompile(`(?m)^\s*option\s+go_package\s*=\s*"([^"]+)"\s*;`)

func extractGoPackage(content string) (string, bool) {
	match := goPackageRE.FindStringSubmatch(content)
	if len(match) != 2 {
		return "", false
	}

	value := strings.TrimSpace(match[1])
	return value, value != ""
}

func goPackageImportPath(goPkg string) string {
	goPkg = strings.TrimSpace(goPkg)
	if idx := strings.Index(goPkg, ";"); idx >= 0 {
		goPkg = goPkg[:idx]
	}
	return strings.Trim(goPkg, "/")
}

func lastPathSegment(path string) string {
	path = strings.Trim(path, "/")
	if path == "" {
		return ""
	}

	parts := strings.Split(path, "/")
	return parts[len(parts)-1]
}

func repoRoot() (string, error) {
	cmd := exec.Command("git", "rev-parse", "--show-toplevel")
	out, err := cmd.Output()
	if err == nil {
		return strings.TrimSpace(string(out)), nil
	}

	wd, wdErr := os.Getwd()
	if wdErr != nil {
		return "", wdErr
	}

	return wd, nil
}

func goModulePath(root string) (string, error) {
	cmd := exec.Command("go", "list", "-m")
	cmd.Dir = root

	out, err := cmd.Output()
	if err != nil {
		return "", err
	}

	return strings.TrimSpace(string(out)), nil
}

// goList 只检查当前工作目录下的包：在 cwd 执行 go list -json ./...。
// 因此 archcheck 在哪个目录运行，就只检查那个目录（通常是某个 module 根或其子目录），
// 不会因 workspace 中其他 module 的编译错误而中断。
func goList() ([]GoPackage, error) {
	wd, err := os.Getwd()
	if err != nil {
		return nil, err
	}
	return goListInDir(wd)
}

// goListInDir 在指定目录下执行 go list -json ./...。
func goListInDir(dir string) ([]GoPackage, error) {
	cmd := exec.Command("go", "list", "-json", "./...")
	cmd.Dir = dir

	out, err := cmd.Output()
	if err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			return nil, fmt.Errorf("%w\n%s", err, string(ee.Stderr))
		}
		return nil, err
	}

	dec := json.NewDecoder(bytes.NewReader(out))

	var pkgs []GoPackage
	for {
		var pkg GoPackage
		err := dec.Decode(&pkg)
		if err == nil {
			pkgs = append(pkgs, pkg)
			continue
		}
		if errors.Is(err, io.EOF) {
			break
		}
		return nil, err
	}

	return pkgs, nil
}

// workspaceModuleDirs 已删除：archcheck 现在只检查 cwd 下的包，不再枚举整个 go.work。

func relDir(root string, path string) string {
	rel, err := filepath.Rel(root, path)
	if err != nil {
		return filepath.ToSlash(path)
	}
	return filepath.ToSlash(rel)
}

func hasPathPrefix(path string, prefix string) bool {
	path = strings.Trim(path, "/")
	prefix = strings.Trim(prefix, "/")
	return path == prefix || strings.HasPrefix(path, prefix+"/")
}

func matchesAnyPathPrefix(path string, prefixes []string) bool {
	for _, prefix := range prefixes {
		if hasPathPrefix(path, prefix) {
			return true
		}
	}
	return false
}

func matchesAnyImportPrefix(importPath string, prefixes []string) bool {
	for _, prefix := range prefixes {
		if importPath == prefix || strings.HasPrefix(importPath, prefix+"/") {
			return true
		}
	}
	return false
}

func firstChildAfter(path string, prefix string) string {
	path = strings.Trim(path, "/")
	prefix = strings.Trim(prefix, "/")

	if path == prefix || !strings.HasPrefix(path, prefix+"/") {
		return ""
	}

	rest := strings.TrimPrefix(path, prefix+"/")
	parts := strings.Split(rest, "/")
	if len(parts) == 0 {
		return ""
	}

	child := parts[0]
	if child == "" {
		return ""
	}

	switch child {
	case "shared", "internal", "common":
		return ""
	default:
		return child
	}
}

func printViolations(modulePath string, violations []Violation, idlViolations []IDLViolation) {
	sort.Slice(violations, func(i, j int) bool {
		if violations[i].FromDir == violations[j].FromDir {
			if violations[i].ToImport == violations[j].ToImport {
				return violations[i].RuleName < violations[j].RuleName
			}
			return violations[i].ToImport < violations[j].ToImport
		}
		return violations[i].FromDir < violations[j].FromDir
	})

	sort.Slice(idlViolations, func(i, j int) bool {
		if idlViolations[i].File == idlViolations[j].File {
			return idlViolations[i].RuleName < idlViolations[j].RuleName
		}
		return idlViolations[i].File < idlViolations[j].File
	})

	fmt.Println("architecture check failed")
	fmt.Println()
	fmt.Printf("module: %s\n", modulePath)
	fmt.Println()

	for _, v := range violations {
		fmt.Printf("- [%s] %s\n", v.RuleName, v.Comment)
		fmt.Printf("  from: %s\n", v.FromDir)
		if v.IsExternal {
			fmt.Printf("  to:   %s\n", v.ToImport)
		} else {
			fmt.Printf("  to:   %s (%s)\n", v.ToDir, v.ToImport)
		}
		fmt.Println()
	}

	for _, v := range idlViolations {
		fmt.Printf("- [%s] %s\n", v.RuleName, v.Comment)
		fmt.Printf("  file:     %s\n", v.File)
		fmt.Printf("  expected: %s\n", v.Expected)
		fmt.Printf("  actual:   %s\n", v.Actual)
		fmt.Println()
	}
}

func fail(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(1)
}

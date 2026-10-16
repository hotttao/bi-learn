package main

import (
	"bufio"
	"bytes"
	"errors"
	"flag"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

type ReExportViolation struct {
	File    string
	Line    int
	Message string
}

func main() {
	var (
		packages      = flag.String("packages", "./...", "comma-separated package patterns passed to deadcode")
		filter        = flag.String("filter", "", "deadcode -filter regexp; defaults to current module path")
		ignoreFile    = flag.String("ignore", ".deadcodeignore", "file containing regexp patterns for deadcode output lines to ignore")
		skipDeadcode  = flag.Bool("skip-deadcode", false, "skip golang.org/x/tools/cmd/deadcode")
		skipReExports = flag.Bool("skip-reexports", false, "skip re-export alias checks")
	)
	flag.Parse()

	// wd 是当前工作目录：deadcheck 只检查 cwd 下的包（通常是某个 module 根
	// 或其子目录），不枚举整个 go.work，也不会因其他 module 的编译错误而中断。
	wd, err := os.Getwd()
	if err != nil {
		fail("get working directory: %v", err)
	}

	modulePath, err := goModulePath(wd)
	if err != nil {
		fail("read go module path: %v", err)
	}

	var failed bool

	if !*skipReExports {
		violations, err := checkNoReExportAliases(wd)
		if err != nil {
			fail("check re-export aliases: %v", err)
		}
		if len(violations) > 0 {
			failed = true
			printReExportViolations(violations)
		}
	}

	if !*skipDeadcode {
		deadcodeFilter := strings.TrimSpace(*filter)
		if deadcodeFilter == "" {
			deadcodeFilter = regexp.QuoteMeta(modulePath)
		}

		ignorePatterns, err := loadIgnorePatterns(wd, *ignoreFile)
		if err != nil {
			fail("load ignore patterns: %v", err)
		}

		lines, raw, err := runDeadcode(wd, deadcodeFilter, splitCSV(*packages))
		filtered := filterDeadcodeLines(lines, ignorePatterns)

		if len(filtered) > 0 {
			failed = true
			printDeadcodeViolations(deadcodeFilter, filtered)
		} else if err != nil {
			// If every reported line was ignored, deadcode may still exit non-zero.
			// Treat that as success. If there was no parsable output, surface the
			// command error.
			if len(lines) == 0 && strings.TrimSpace(raw) != "" {
				fail("deadcode failed: %v\n%s", err, raw)
			}
		}
	}

	if failed {
		os.Exit(1)
	}

	fmt.Println("dead code check passed")
}

func runDeadcode(root string, filter string, packages []string) ([]string, string, error) {
	args := []string{
		"run",
		"golang.org/x/tools/cmd/deadcode@latest",
		"-test",
		"-filter=" + filter,
	}
	args = append(args, packages...)

	cmd := exec.Command("go", args...)
	cmd.Dir = root

	out, err := cmd.CombinedOutput()
	raw := string(out)

	var lines []string
	scanner := bufio.NewScanner(bytes.NewReader(out))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		lines = append(lines, line)
	}

	return lines, raw, err
}

func loadIgnorePatterns(root string, file string) ([]*regexp.Regexp, error) {
	patterns := []string{
		`biz/model/`,
		`biz/router/`,
		`biz/dal/model`,
		`biz/dal/query`,
		`router_gen\.go:`,
		`\.pb\.go:`,
		`(?i)code generated`,
		`(?i)/generated/`,
		// Test files — `-test` flag enables test compilation but the analyzer
		// still reports unreachable methods on fakes/stubs in *_test.go.
		// Dead code inside tests is not production reachability concern.
		`_test\.go:`,
		// hertz-generated typed client + client config (build/interface code
		// that consumers see only via interface dispatch). Belt-and-suspenders
		// alongside `hertz_gen/` ignore in .deadcodeignore.
		`hertz_gen/`,
	}

	path := filepath.Join(root, file)
	if data, err := os.ReadFile(path); err == nil {
		scanner := bufio.NewScanner(bytes.NewReader(data))
		for scanner.Scan() {
			line := strings.TrimSpace(scanner.Text())
			if line == "" || strings.HasPrefix(line, "#") {
				continue
			}
			patterns = append(patterns, line)
		}
		if err := scanner.Err(); err != nil {
			return nil, err
		}
	} else if !os.IsNotExist(err) {
		return nil, err
	}

	compiled := make([]*regexp.Regexp, 0, len(patterns))
	for _, pattern := range patterns {
		re, err := regexp.Compile(pattern)
		if err != nil {
			return nil, fmt.Errorf("invalid ignore regexp %q: %w", pattern, err)
		}
		compiled = append(compiled, re)
	}

	return compiled, nil
}

func filterDeadcodeLines(lines []string, ignores []*regexp.Regexp) []string {
	var kept []string
	for _, line := range lines {
		if strings.Contains(line, "go: downloading") {
			continue
		}

		ignored := false
		for _, re := range ignores {
			if re.MatchString(filepath.ToSlash(line)) {
				ignored = true
				break
			}
		}
		if !ignored {
			kept = append(kept, line)
		}
	}

	return kept
}

func checkNoReExportAliases(root string) ([]ReExportViolation, error) {
	var violations []ReExportViolation

	err := filepath.WalkDir(root, func(file string, d os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}

		if d.IsDir() {
			switch d.Name() {
			case ".git", "vendor", "node_modules":
				return filepath.SkipDir
			}

			rel := relPath(root, file)
			if hasPathPrefix(rel, "biz/model") ||
				hasPathPrefix(rel, "biz/router") ||
				hasPathPrefix(rel, "third_party") {
				return filepath.SkipDir
			}
			return nil
		}

		if filepath.Ext(file) != ".go" || strings.HasSuffix(file, "_test.go") {
			return nil
		}

		if isGeneratedGoFile(file) {
			return nil
		}

		fileViolations, err := checkGoFileNoReExportAliases(root, file)
		if err != nil {
			return err
		}
		violations = append(violations, fileViolations...)
		return nil
	})

	if err != nil {
		return nil, err
	}

	sort.Slice(violations, func(i, j int) bool {
		if violations[i].File == violations[j].File {
			return violations[i].Line < violations[j].Line
		}
		return violations[i].File < violations[j].File
	})

	return violations, nil
}

func checkGoFileNoReExportAliases(root string, file string) ([]ReExportViolation, error) {
	fset := token.NewFileSet()
	parsed, err := parser.ParseFile(fset, file, nil, parser.ParseComments)
	if err != nil {
		return nil, err
	}

	importAliases := map[string]string{}

	var violations []ReExportViolation

	for _, spec := range parsed.Imports {
		importPath := strings.Trim(spec.Path.Value, `"`)

		if spec.Name != nil && spec.Name.Name == "." {
			violations = append(violations, ReExportViolation{
				File:    relPath(root, file),
				Line:    fset.Position(spec.Pos()).Line,
				Message: fmt.Sprintf("dot import of %q is forbidden", importPath),
			})
			continue
		}

		local := importLocalName(spec)
		if local != "" && local != "_" {
			importAliases[local] = importPath
		}
	}

	for _, decl := range parsed.Decls {
		gen, ok := decl.(*ast.GenDecl)
		if !ok {
			continue
		}

		for _, spec := range gen.Specs {
			switch s := spec.(type) {
			case *ast.TypeSpec:
				if !s.Name.IsExported() || !s.Assign.IsValid() {
					continue
				}
				if selectorFromImport(s.Type, importAliases) {
					violations = append(violations, ReExportViolation{
						File:    relPath(root, file),
						Line:    fset.Position(s.Pos()).Line,
						Message: fmt.Sprintf("exported type alias %s re-exports another package; import the original package instead", s.Name.Name),
					})
				}

			case *ast.ValueSpec:
				for i, name := range s.Names {
					if !name.IsExported() || i >= len(s.Values) {
						continue
					}
					if selectorFromImport(s.Values[i], importAliases) {
						violations = append(violations, ReExportViolation{
							File:    relPath(root, file),
							Line:    fset.Position(name.Pos()).Line,
							Message: fmt.Sprintf("exported value %s re-exports another package; import the original package instead", name.Name),
						})
					}
				}
			}
		}
	}

	return violations, nil
}

func importLocalName(spec *ast.ImportSpec) string {
	if spec.Name != nil {
		return spec.Name.Name
	}

	importPath := strings.Trim(spec.Path.Value, `"`)
	base := path.Base(importPath)
	base = strings.TrimSuffix(base, ".git")
	return base
}

func selectorFromImport(expr ast.Expr, importAliases map[string]string) bool {
	switch e := expr.(type) {
	case *ast.SelectorExpr:
		if ident, ok := e.X.(*ast.Ident); ok {
			_, exists := importAliases[ident.Name]
			return exists
		}
	case *ast.ParenExpr:
		return selectorFromImport(e.X, importAliases)
	}
	return false
}

func isGeneratedGoFile(file string) bool {
	data, err := os.ReadFile(file)
	if err != nil {
		return false
	}

	head := string(data)
	if len(head) > 2048 {
		head = head[:2048]
	}

	return strings.Contains(head, "Code generated") && strings.Contains(head, "DO NOT EDIT")
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

func splitCSV(value string) []string {
	parts := strings.Split(value, ",")
	result := make([]string, 0, len(parts))
	for _, part := range parts {
		part = strings.TrimSpace(part)
		if part != "" {
			result = append(result, part)
		}
	}
	if len(result) == 0 {
		return []string{"./..."}
	}
	return result
}

func relPath(root string, file string) string {
	rel, err := filepath.Rel(root, file)
	if err != nil {
		return filepath.ToSlash(file)
	}
	return filepath.ToSlash(rel)
}

func hasPathPrefix(value string, prefix string) bool {
	value = strings.Trim(value, "/")
	prefix = strings.Trim(prefix, "/")
	return value == prefix || strings.HasPrefix(value, prefix+"/")
}

func printReExportViolations(violations []ReExportViolation) {
	fmt.Println("re-export check failed")
	fmt.Println()
	for _, v := range violations {
		fmt.Printf("- %s:%d: %s\n", v.File, v.Line, v.Message)
	}
	fmt.Println()
}

func printDeadcodeViolations(filter string, lines []string) {
	fmt.Println("deadcode check failed")
	fmt.Println()
	fmt.Printf("deadcode filter: %s\n", filter)
	fmt.Println()
	for _, line := range lines {
		fmt.Printf("- %s\n", line)
	}
	fmt.Println()
}

func fail(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(1)
}

var _ = errors.Is
var _ = io.EOF

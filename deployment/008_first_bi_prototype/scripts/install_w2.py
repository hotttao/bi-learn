"""Reproduce W2 locally, retaining W1's exact core pins and native server API."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    subprocess.run([str(a) for a in args], cwd=ROOT, check=True)


def main():
    run(sys.executable, ROOT/'scripts/install_w1.py')
    run(ROOT/'.local/uv', 'pip', 'sync', '--python', ROOT/'.local/wren-venv/bin/python',
        '--index-url', 'https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple',
        ROOT/'wren/requirements-mcp.lock')
    run(ROOT/'scripts/wren.sh', 'context', 'validate')
    run(ROOT/'scripts/wren.sh', 'context', 'build')
    run(sys.executable, ROOT/'scripts/configure_w2.py')
    print('W2 installed. Start a new Codex session from the prototype directory to load its MCP entry.')


if __name__ == '__main__':
    main()

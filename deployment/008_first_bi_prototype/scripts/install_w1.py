"""Install pinned W1 tooling under .local; leaves system Python untouched."""
import hashlib
from pathlib import Path
import platform
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / '.local'
UV_SHA = 'afdd00ddc25e12ed756e069090011ca55f127753e1192e51f45fa288a024f3df'
UV_URL = ('https://mirrors.tuna.tsinghua.edu.cn/pypi/web/packages/4c/4a/'
          'd5357825bb47ff73762d247b1a553a966fef6802e3ab829fe60934cbf339/'
          'uv-0.9.9-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl')


def run(args): subprocess.run([str(x) for x in args], check=True, cwd=ROOT)


def main():
    if sys.version_info < (3,11): raise SystemExit('Wren 0.16.0 requires Python >=3.11')
    if sys.platform != 'linux' or platform.machine() != 'x86_64':
        raise SystemExit('This bootstrap pins the tested Linux amd64 uv wheel')
    LOCAL.mkdir(exist_ok=True)
    wheel = LOCAL/'uv.whl'
    if not wheel.exists() or hashlib.sha256(wheel.read_bytes()).hexdigest()!=UV_SHA:
        run(['curl','--noproxy','*','-fsS','--retry','2','--connect-timeout','5','--max-time','120',UV_URL,'-o',wheel])
    if hashlib.sha256(wheel.read_bytes()).hexdigest()!=UV_SHA:raise SystemExit('uv wheel digest mismatch')
    with zipfile.ZipFile(wheel) as archive:
        target=LOCAL/'uv';target.write_bytes(archive.read('uv-0.9.9.data/scripts/uv'));target.chmod(0o755)
    python=LOCAL/'wren-venv/bin/python'
    if not python.exists():run([LOCAL/'uv','venv',LOCAL/'wren-venv','--python',sys.executable])
    run([LOCAL/'uv','pip','install','--python',python,'--index-url','https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple',
         '-r',ROOT/'wren/requirements.lock'])
    run([ROOT/'scripts/wren.sh','profile','add','bi008','--from-file',ROOT/'wren/runtime/trino-connection.json','--activate'])
    run([ROOT/'scripts/wren.sh','context','set-profile','bi008'])
    print('Project-local Wren ready. Profile validation must report success before querying.')


if __name__=='__main__': main()

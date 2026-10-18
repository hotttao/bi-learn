"""W2 compatibility adapter around Wren 0.16.0's native stdio MCP server.

Adds input qualification checks, exact Decimal output, and one startup snapshot.
This is a tool adapter, not an Agent or department authorization implementation.
"""
import atexit
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import tempfile

import sqlglot
from sqlglot import exp

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / 'wren'


class ScopeError(ValueError):
    pass


def validate_input(sql):
    try:
        statements = [s for s in sqlglot.parse(sql, read='trino') if s is not None]
    except sqlglot.errors.SqlglotError as error:
        raise ScopeError('BI008_QUERY_SCOPE: SQL parse failed') from error
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise ScopeError('BI008_QUERY_SCOPE: exactly one read-only query is required')
    for table in statements[0].find_all(exp.Table):
        if table.db or table.catalog:
            raise ScopeError('BI008_QUERY_SCOPE: use unqualified MDL models/CTEs; catalog/schema references are forbidden')
        if not isinstance(table.this, exp.Identifier):
            raise ScopeError('BI008_QUERY_SCOPE: table-valued source functions are forbidden')
    # Wren's existing strict policy still validates model/CTE visibility and readers.


def revision(project):
    entries = {}
    fixed = ['wren_project.yml','relationships.yml','AGENTS.md','target/mdl.json',
             'runtime/config.json','runtime/trino-connection.json','runtime/profiles.yml']
    files = [project / name for name in fixed]
    for directory in ['models','views','cubes','knowledge']:
        files.extend((project/directory).rglob('*'))
    for path in files:
        if path.is_symlink():
            raise ScopeError('BI008_MODEL_REVISION: symlinks are unsupported in this local snapshot')
        if path.is_file():
            entries[path.relative_to(project).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


def audit(event, sql, mdl_hash):
    path = Path(os.environ.get('BI008_MCP_AUDIT_PATH', ROOT/'.local/w2-audit.ndjson'))
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {'at_utc':datetime.now(timezone.utc).isoformat(),'pid':os.getpid(), 'event':event,
              'mdl_sha256':mdl_hash, 'sql_sha256':hashlib.sha256(sql.encode()).hexdigest()}
    fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_APPEND, 0o600)
    try: os.write(fd, (json.dumps(record)+'\n').encode())
    finally: os.close(fd)


class GuardedEngine:
    def __init__(self, native, project, source_revision, mdl_hash):
        self.native, self.project = native, project
        self.source_revision, self.mdl_hash = source_revision, mdl_hash

    def __getattr__(self, name):
        return getattr(self.native, name)

    def check(self, sql):
        try:
            if revision(self.project) != self.source_revision:
                raise ScopeError('BI008_MODEL_REVISION: project changed; validate/build/review and restart this MCP process')
            validate_input(sql)
        except ScopeError:
            audit('input_rejected', sql, self.mdl_hash)
            raise

    def dry_plan(self, sql, properties=None):
        self.check(sql)
        return self.native.dry_plan(sql, properties)

    def dry_run(self, sql, properties=None):
        self.check(sql)
        audit('dry_run_attempt', sql, self.mdl_hash)
        return self.native.dry_run(sql, properties)

    def query(self, sql, limit=None, properties=None):
        self.check(sql)
        audit('query_attempt', sql, self.mdl_hash)
        result = self.native.query(sql, limit, properties)
        audit('query_success', sql, self.mdl_hash)
        return result

    def close(self):
        self.native.close()


def make_server(project=PROJECT):
    if version('wrenai') != '0.16.0' or version('mcp') != '1.26.0':
        raise RuntimeError('Adapter requires audited wrenai==0.16.0 and mcp==1.26.0')
    from wren import context
    from wren.cli import _build_engine
    from wren.config import load_config
    from wren.engine import WrenEngine
    from wren import mcp_server as upstream
    from mcp.types import ToolAnnotations

    project = project.resolve()
    mdl_path = project/'target/mdl.json'
    if json.loads(mdl_path.read_text()) != context.build_json(project):
        raise ScopeError('BI008_MODEL_REVISION: source and compiled MDL differ; validate/build first')
    config = load_config(Path(os.environ['WREN_HOME']))
    if not config.strict_mode:
        raise ScopeError('BI008_QUERY_SCOPE: runtime strict_mode must be enabled')
    source_revision = revision(project)
    temporary = tempfile.TemporaryDirectory(prefix='bi008-wren-mcp-')
    snapshot = Path(temporary.name)/'project'
    shutil.copytree(project, snapshot, ignore=shutil.ignore_patterns('runtime','.wren','requirements.*'))
    if revision(project) != source_revision:
        temporary.cleanup()
        raise ScopeError('BI008_MODEL_REVISION: source changed during startup')
    atexit.register(temporary.cleanup)
    # Resolve credentials with Wren's own profile loader; use its existing engine.
    resolved = _build_engine(str(mdl_path), None, None)
    native = WrenEngine(resolved.manifest_str, resolved.data_source, resolved.connection_info,
                        resolved.function_path, fallback=False, config=config)
    resolved.close()
    mdl_hash = hashlib.sha256(mdl_path.read_bytes()).hexdigest()
    engine = GuardedEngine(native, project, source_revision, mdl_hash)
    atexit.register(engine.close)
    # Keep the native tools/resources/prompts. Change only Decimal serialization.
    original_normalize = upstream._normalize_value
    def normalize(value):
        return format(value, 'f') if isinstance(value, Decimal) else original_normalize(value)
    upstream._normalize_value = normalize
    server = upstream.build_server(upstream.ServeContext(snapshot, engine, allow_write=False, no_connect=False))


    cube_tool = server._tool_manager.get_tool('query_cube')
    native_cube = cube_tool.fn
    def supported_cube(*args, **kwargs):
        time_dimension = kwargs.get('time_dimension')
        if time_dimension and time_dimension.count(':') > 1:
            raise ScopeError('BI008_CUBE_RANGE: upstream Trino DATE range translation is unsupported; use run_sql with explicit DATE literals')
        return native_cube(*args, **kwargs)
    cube_tool.fn = supported_cube
    cube_tool.description += '\nAdapter capability: date ranges are unsupported here. Use run_sql with explicit DATE literals; time grouping without a range is supported.'

    @server.tool(annotations=ToolAnnotations(title='Runtime Contract', readOnlyHint=True))
    def get_runtime_contract() -> dict:
        """Read the executing model revision, adapter constraints and deployment scope."""
        return {'project':'bi008_semantics','metric':'net_revenue@1','mdl_sha256':mdl_hash,
                'source_revision':source_revision,'source_matches_startup':revision(project)==source_revision,
                'wrenai':version('wrenai'),'mcp':version('mcp'),'strict_mode':True,'fallback':False,
                'table_references':'unqualified MDL models or visible CTEs only',
                'money_json':'Decimal as exact decimal string','allow_write':False,
                'cube_date_range':'unsupported; use run_sql with explicit DATE literals',
                'department_isolation':'NOT implemented; local synthetic-data experiment',
                'transport':'stdio','superset_sync':'not implemented'}
    return server


if __name__ == '__main__':
    make_server().run(transport='stdio')

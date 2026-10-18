"""S9 topic adapter over the existing Wren MCP, not an Agent or authorization.

Conservative curated routing + dependency closure; no vector search or learning.
Only this dedicated stdio process is changed. The main/S8 MCP stays untouched.
"""
from functools import wraps
import hashlib
import json
from pathlib import Path
import re
import sys
from threading import RLock
from mcp.types import ToolAnnotations

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope
import yaml
from wren.memory.schema_indexer import describe_schema

from guarded_wren_mcp import ROOT,ScopeError,make_server,revision,validate_input

REGISTRY=ROOT/'s9/topics.yaml'
PROJECT=ROOT/'.local/s8/catalogs/500/schema_and_rules'


class Topics:
    def __init__(self,project=PROJECT,registry=REGISTRY):
        self.project,self.registry=Path(project),Path(registry)
        self.registry_hash=hashlib.sha256(self.registry.read_bytes()).hexdigest()
        self.source_revision=revision(self.project)
        self.config=yaml.safe_load(self.registry.read_text());self.manifest=json.loads((self.project/'target/mdl.json').read_text())
        self.available={m['name'] for m in self.manifest['models']}
        self.selected=[];self.allowed=set()
        for topic in self.config['topics']:self.closure([topic])

    def check_version(self):
        if hashlib.sha256(self.registry.read_bytes()).hexdigest()!=self.registry_hash or revision(self.project)!=self.source_revision:
            self.selected=[];self.allowed=set()
            raise ScopeError('BI008_TOPIC_REVISION: source or topic registry changed; review and restart')

    def closure(self,names):
        result=[];visiting=set();done=set()
        def visit(name):
            if name not in self.config['topics']:raise ScopeError('BI008_TOPIC_CONFIG: missing topic '+name)
            if name in visiting:raise ScopeError('BI008_TOPIC_CONFIG: cyclic dependency')
            if name in done:return
            visiting.add(name);topic=self.config['topics'][name]
            for dep in topic['depends_on']:visit(dep)
            if not set(topic['models'])<=self.available:raise ScopeError('BI008_TOPIC_CONFIG: missing model in '+name)
            visiting.remove(name);done.add(name);result.append(name)
        for name in names:visit(name)
        return result

    def rules(self):
        if not self.selected:return '先通过 get_context 选择已审核的净收入主题；口径或行业时点不明时澄清，不默认返回全量 schema。'
        return self.config['shared_rules']+'\n'+'\n'.join(self.config['topics'][t]['rules'] for t in self.selected)

    def narrowed_manifest(self):
        if not self.selected:raise ScopeError('BI008_TOPIC_SELECT_FIRST: select an unambiguous topic through get_context')
        return dict(self.manifest,models=[m for m in self.manifest['models'] if m['name'] in self.allowed])

    def context(self,question):
        self.check_version();self.selected=[];self.allowed=set()
        def refusal(status,message):return {'strategy':'reviewed_topic_closure','status':status,'message':message,'topic_ids':[],'model_names':[],'schema':''}
        if re.search('财务|确认收入|毛利|成本',question):
            return refusal('unavailable','当前主题没有审核过的财务确认收入或毛利定义，不用净收入替代。')
        if not re.search('净收入|净销售额|net_revenue',question,re.I):
            return refusal('needs_clarification','请明确是否查询已审核的净收入指标，以及时间和分析维度。')
        names=[]
        if '行业' in question:
            historical=bool(re.search('下单时.*行业|历史.*行业|订单.*时.*行业',question))
            current=bool(re.search('当前.*行业|目前.*行业|现行业',question))
            if not historical and not current:return refusal('needs_clarification','请明确客户当前行业，还是下单时行业。')
            if historical:names.append('historical_industry')
            if current:names.append('current_industry')
        if '标签' in question or 'VIP' in question:names.append('overlapping_tags')
        if not names:
            if re.search('库存|合同|地区|产品|利润',question):return refusal('unavailable','没有此分析维度的已审核主题，请补充定义。')
            names=['net_revenue']
        self.selected=self.closure(names)
        self.allowed={m for t in self.selected for m in self.config['topics'][t]['models']}
        schema=describe_schema(self.narrowed_manifest())
        return {'strategy':'reviewed_topic_closure','status':'ready','topic_ids':self.selected,'model_names':sorted(self.allowed),
                'schema':schema,'instructions':self.rules(),'registry_sha256':self.registry_hash,
                'source_revision':self.source_revision,'schema_utf8_bytes':len(schema.encode()),
                'scope':'curated topic selection and model-reference checks; not department authorization or vector retrieval'}

    def check_sql(self,sql):
        self.check_version()
        if not self.selected:raise ScopeError('BI008_TOPIC_SELECT_FIRST: no selected topic')
        validate_input(sql)
        tree=sqlglot.parse_one(sql,read='trino')
        referenced={s.name for scope in traverse_scope(tree) for s in scope.sources.values() if isinstance(s,exp.Table)}
        if referenced-self.allowed:raise ScopeError('BI008_TOPIC_SCOPE: models outside selected dependency closure: '+','.join(sorted(referenced-self.allowed)))


def server(project=PROJECT,registry=REGISTRY):
    topics=Topics(project,registry);mcp=make_server(Path(project))
    manager=mcp._tool_manager
    native_contract=manager.get_tool('get_runtime_contract').fn
    def contract():
        topics.check_version();result=native_contract()
        result.update(topic_registry_sha256=topics.registry_hash,context_strategy='reviewed_topic_closure',selected_topics=topics.selected,allowed_models=sorted(topics.allowed))
        return result
    manager.get_tool('get_runtime_contract').fn=contract
    def get_context(question,limit=5,item_type=None,model_name=None):return topics.context(question)
    manager.get_tool('get_context').fn=get_context
    manager.get_tool('get_context').description='Select reviewed sales-topic context from the question. Returns only dependency-closed models and rules, or clarification/unavailable. Conservative curated matching, not embeddings. Compatibility parameters limit/item_type/model_name do not truncate required dependencies.'
    def instructions():
        topics.check_version();return {'instructions':topics.rules(),'used_legacy':False,'selected_topics':topics.selected}
    manager.get_tool('get_instructions').fn=instructions
    original_models=manager.get_tool('list_models').fn
    def models():
        topics.check_version();data=original_models();return dict(data,models=[m for m in data['models'] if m['name'] in topics.allowed],selected_topics=topics.selected)
    manager.get_tool('list_models').fn=models
    original_describe=manager.get_tool('describe_model').fn
    def describe(name):
        topics.check_version()
        if name not in topics.allowed:raise ScopeError('BI008_TOPIC_SCOPE: model is not in selected dependency closure')
        return original_describe(name)
    manager.get_tool('describe_model').fn=describe
    for tool_name in ['dry_plan','dry_run','run_sql']:
        tool=manager.get_tool(tool_name);original=tool.fn
        def guarded(original):
            @wraps(original)
            def call(*args,**kwargs):
                topics.check_sql(kwargs.get('sql',args[0] if args else ''))
                return original(*args,**kwargs)
            return call
        tool.fn=guarded(original)
    # No cubes in this controlled raw-schema comparison project.
    assert not topics.manifest.get('cubes')
    @mcp.tool(annotations=ToolAnnotations(title='List Reviewed Topics',readOnlyHint=True))
    def list_topics() -> dict:
        """Discover the four reviewed synthetic sales topics before querying."""
        topics.check_version()
        return {'topics':[{'id':k,'name':v['name']} for k,v in topics.config['topics'].items()],
                'selection':'get_context(question) selects a conservative curated topic; ambiguous questions return no schema'}
    # Serialize topic changes and query checks/execution within this dedicated
    # stdio process. This is not concurrency control for Superset asset writes.
    lock=RLock()
    for name in ['list_topics','get_runtime_contract','get_context','get_instructions',
                 'list_models','describe_model','dry_plan','dry_run','run_sql']:
        tool=manager.get_tool(name)
        def serialized(original):
            @wraps(original)
            def call(*args,**kwargs):
                with lock:return original(*args,**kwargs)
            return call
        tool.fn=serialized(tool.fn)
    return mcp


if __name__=='__main__':server().run(transport='stdio')

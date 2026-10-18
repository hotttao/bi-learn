"""Drive the installed Codex product through one genuine multi-turn MCP scenario.

No development delegation: shell, web requests and multi_agent are disabled for
analysis. The runner verifies actual tools, assets and preserved fixture edits.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
import subprocess

from configure_s7 import TOOLS as BI_TOOLS
from configure_w2 import TOOLS as WREN_TOOLS
from prepare_s7 import ROOT, save, validate_binding
from s3_assets import API
from s6_assets import layout_ids

CWD=ROOT/'.local/s7-analysis'


def decode(call):
    result=call.get('result') or {}
    if result.get('structured_content') is not None:return result['structured_content']
    text='\n'.join(c['text'] for c in result.get('content',[]) if c.get('type')=='text')
    try:return json.loads(text)
    except json.JSONDecodeError:return text


def collect(prefix):
    events=[json.loads(line) for line in (ROOT/f'.local/{prefix}-events.jsonl').read_text().splitlines()]
    assert any(e['type']=='turn.completed' for e in events),'Model turn did not complete'
    items=[e['item'] for e in events if e['type']=='item.completed']
    assert all(i['type'] in ['agent_message','reasoning','mcp_tool_call'] for i in items),'Unexpected non-MCP action'
    calls=[i for i in items if i['type']=='mcp_tool_call']
    for call in calls:
        allowed={'bi008_wren':WREN_TOOLS,'bi008_superset':BI_TOOLS}
        assert call['server'] in allowed and call['tool'] in allowed[call['server']],call
        assert not call.get('error'),call
    answer=json.loads((ROOT/f'.local/{prefix}-answer.json').read_text())
    thread=next(e['thread_id'] for e in events if e['type']=='thread.started')
    return calls,answer,thread,events


def invoke(prefix,prompt,thread=None,readonly=False,allow_preview=False):
    binary=ROOT/'.local/codex-bin'
    args=[str(binary),'exec']
    if thread:args+=['resume']
    args+=['--json','--disable','shell_tool','--disable','unified_exec','--disable','multi_agent',
           '-c','approval_policy="never"','-c','sandbox_mode="read-only"',
           '--output-schema',str(ROOT/'codex/s7-answer.schema.json'),'-o',str(ROOT/f'.local/{prefix}-answer.json')]
    if readonly:
        tools=[t for t in BI_TOOLS if t not in ['generate_chart','update_chart','add_chart_to_existing_dashboard']]
        if allow_preview:tools+=['generate_chart']
        args+=['-c','mcp_servers.bi008_superset.enabled_tools='+json.dumps(tools)]
        args+=['--ephemeral']
    if thread:args+=[thread]
    args+=[prompt]
    with (ROOT/f'.local/{prefix}-events.jsonl').open('w') as out,(ROOT/f'.local/{prefix}-stderr.log').open('w') as err:
        subprocess.run(args,cwd=CWD,stdout=out,stderr=err,check=True,timeout=420)


def check_preserved(api,fixture):
    chart=api.call('GET',f"/api/v1/chart/{fixture['seed_chart_id']}")['result']
    assert chart['slice_name']==fixture['seed_chart_name']
    assert json.loads(chart['params'])==fixture['seed_chart_params'],'Other chart was overwritten'
    board=api.call('GET',f"/api/v1/dashboard/{fixture['dashboard_id']}")['result']
    position=json.loads(board['position_json'])
    assert all(position.get(k)==v for k,v in fixture['preserved_nodes'].items()),'Existing layout or manual note changed'
    return board


def saved_chart(calls):
    saved=[decode(c)['chart']['id'] for c in calls if c['server']=='bi008_superset' and c['tool']=='generate_chart' and c['arguments']['request'].get('save_chart')]
    assert len(saved)==1,'Expected one saved chart'
    return saved[0]


def assert_wren_contract(calls,binding):
    contracts=[decode(c) for c in calls if c['server']=='bi008_wren' and c['tool']=='get_runtime_contract']
    assert contracts and all(c['metric']==binding['metric'] and c['mdl_sha256']==binding['wren_mdl_sha256'] and c['source_revision']==binding['wren_source_revision'] and c['source_matches_startup'] for c in contracts)
    queries=[c for c in calls if c['server']=='bi008_wren' and c['tool']=='run_sql']
    assert queries,'Actual Wren query missing'
    for c in queries:
        sql=c['arguments']['sql']
        for tool in ['dry_plan','dry_run']:
            assert any(p['server']=='bi008_wren' and p['tool']==tool and p['arguments']['sql']==sql for p in calls[:calls.index(c)]),tool+' missing'
    results=[decode(c) for c in queries]
    assert all(isinstance(r,dict) and 'rows' in r and not r.get('truncated') for r in results),'Wren execution failed or truncated'
    return results


def manual_edit(api,cid):
    chart=api.call('GET',f'/api/v1/chart/{cid}')['result']
    params=json.loads(chart['params'])
    assert params.get('orientation','vertical')=='vertical'
    params.update({'y_axis_format':',.1f','row_limit':77,'color_scheme':'d3Category10'})
    name='008 S7 行业净收入（模拟人工改名）'
    api.call('PUT',f'/api/v1/chart/{cid}',{'slice_name':name,'params':json.dumps(params,ensure_ascii=False)})
    state={'chart_id':cid,'name':name,'params':params,'scope':'fixture simulates prior human edits; REST setup, not Agent action'}
    save(ROOT/'evidence/s7-manual-edit.json',state)
    return state


TURN_PROMPTS={
  1:'看2026年10月的净收入日趋势，并说明合计。先用Wren核对已审核口径和实际数据，再用绑定的Superset数据集生成折线预览；核对后保存到指定看板{did}，图表命名“008 S7 按日净收入”。已授权保存和加入看板，不需再次询问。保留看板已有图表和备注；保存后读回配置和数据。金额使用精确字符串，日期用YYYY-MM-DD。',
  2:'再按当前客户行业比较净销售额，加入刚才同一个看板{did}，图表命名“008 S7 行业净收入”。先用Wren核对，再预览、保存、加入并读回图表配置和数据。已授权保存，保留趋势图和原有布局。',
  3:'把刚才的行业柱状图改成横向，只改方向。期间已有模拟人工编辑：请先重新读取最新图表与看板；保留最新名称、金额格式、行数限制、配色、其他图表和既有布局。直接保存并读回确认，不新增图表，不重建看板。color_scheme不是当前工具输入字段，不要把它传入config；已部署的同类型合并适配负责保留该未映射字段。',
  4:'再保存一次刚才的行业图到同一个看板{did}。如果已经存在，直接复用现有ID，不新建、不覆盖，也不要重复加入；先读当前状态再确认。'
}


def main(turn,collect_only=False):
    fixture=json.loads((ROOT/'evidence/s7-fixture.json').read_text())
    binding=json.loads((ROOT/'evidence/s7-binding.json').read_text());api=API();validate_binding(binding,api)
    state_path=ROOT/'.local/s7-main-session.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else {'turns':{}}
    if str(turn) in state['turns'] and not collect_only:raise SystemExit('Turn already completed; use --collect-only to re-check evidence without repeating writes')
    if turn>1:assert str(turn-1) in state['turns'],'Complete the previous real conversation turn first'
    if turn==3 and not (ROOT/'evidence/s7-manual-edit.json').exists():manual_edit(api,state['bar_id'])
    prefix=f's7-turn-{turn}'
    if not collect_only:
        invoke(prefix,TURN_PROMPTS[turn].format(did=fixture['dashboard_id']),state.get('thread_id'))
    calls,answer,thread,events=collect(prefix)
    if state.get('thread_id'):assert state['thread_id']==thread,'Not the same resumed conversation'
    state['thread_id']=thread
    # Persist the session ID before assertions so a local parser fix need not replay creates.
    save(state_path,state)
    board=check_preserved(api,fixture)
    if turn in [1,2]:
        assert answer['metric']==binding['metric'] and answer['mdl_sha256']==binding['wren_mdl_sha256']
        assert answer['dashboard_id']==fixture['dashboard_id'] and answer['dataset_id']==binding['dataset_id']
        assert_wren_contract(calls,binding)
        cid=saved_chart(calls);chart=api.call('GET',f'/api/v1/chart/{cid}')['result']
        assert chart['datasource_id']==binding['dataset_id'] and cid in layout_ids(board)
        params=json.loads(chart['params'])
        assert any(c['tool']=='get_chart_data' and c['server']=='bi008_superset' and c['arguments']['request'].get('identifier')==cid for c in calls),'Saved data not read back'
        if turn==1:
            assert chart['viz_type']=='echarts_timeseries_line' and params['x_axis']=='order_date' and params['time_grain_sqla']=='P1D'
            assert Decimal(answer['total'])==245 and sorted(Decimal(r['amount']) for r in answer['daily'])==[120,125]
            state['trend_id']=cid
        else:
            assert chart['viz_type']=='echarts_timeseries_bar' and params['x_axis']=='industry'
            assert {r['industry']:Decimal(r['amount']) for r in answer['industry']}=={'软件':Decimal(120),'零售':Decimal(125)}
            state['bar_id']=cid
        assert any(c['tool']=='generate_chart' and not c['arguments']['request'].get('save_chart') for c in calls if c['server']=='bi008_superset'),'Unsaved preview missing'
    elif turn==3:
        manual=json.loads((ROOT/'evidence/s7-manual-edit.json').read_text());cid=state['bar_id']
        fresh=[decode(c) for c in calls if c['server']=='bi008_superset' and c['tool']=='get_chart_info' and c['arguments']['request'].get('identifier')==cid]
        assert fresh and fresh[0]['slice_name']==manual['name'] and fresh[0]['form_data']['row_limit']==77,'Current edit was not read'
        updates=[c for c in calls if c['server']=='bi008_superset' and c['tool']=='update_chart']
        assert updates and all(c['arguments']['request']['identifier']==cid and c['arguments']['request'].get('generate_preview') is False for c in updates)
        chart=api.call('GET',f'/api/v1/chart/{cid}')['result'];params=json.loads(chart['params'])
        assert chart['slice_name']==manual['name'] and params['orientation']=='horizontal'
        for field,value in manual['params'].items():
            if field!='orientation':assert params.get(field)==value,field+' manual edit lost'
        assert not any(c['tool']=='generate_chart' and c['arguments']['request'].get('save_chart') for c in calls if c['server']=='bi008_superset')
    else:
        assert not any(c['server']=='bi008_superset' and c['tool'] in ['generate_chart','update_chart','add_chart_to_existing_dashboard'] for c in calls),'Repeated save issued an unnecessary mutation'
    expected=[fixture['seed_chart_id']]+[state[k] for k in ['trend_id','bar_id'] if k in state]
    assert layout_ids(board)==sorted(expected),'Unexpected dashboard assets'
    previous_proof=ROOT/f'evidence/s7-turn-{turn}.json'
    actual_prompt=(json.loads(previous_proof.read_text())['prompt'] if collect_only and previous_proof.exists()
                   else TURN_PROMPTS[turn].format(did=fixture['dashboard_id']))
    proof={'verified_at_utc':datetime.now(timezone.utc).isoformat(),'turn':turn,'same_conversation':thread,
           'prompt':actual_prompt,'calls':calls,'answer':answer,
           'dashboard_id':fixture['dashboard_id'],'chart_ids':layout_ids(board),'checks_passed':True}
    save(ROOT/f'evidence/s7-turn-{turn}.json',proof)
    state['turns'][str(turn)]={'proof':f'evidence/s7-turn-{turn}.json','passed':True};save(state_path,state)
    config_path=ROOT/'evidence/s7-config.json';cfg=json.loads(config_path.read_text());cfg['actual_model_invocation_verified']=True;save(config_path,cfg)
    print('S7 real conversation turn',turn,'passed;',len(calls),'MCP calls; chart IDs',layout_ids(board),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--turn',type=int,choices=[1,2,3,4],required=True);parser.add_argument('--collect-only',action='store_true')
    args=parser.parse_args();main(args.turn,args.collect_only)

"""S7 final evidence gate: actual Codex tools, version mapping and preserved assets."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json

from prepare_s7 import ROOT, save, validate_binding
from s3_assets import API
from s6_assets import layout_ids
from verify_s7_codex import decode, check_preserved


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--partial",action="store_true",help="Audit completed work without claiming S7 acceptance")
    partial=parser.parse_args().partial
    api=API();binding=json.loads((ROOT/'evidence/s7-binding.json').read_text());validate_binding(binding,api)
    fixture=json.loads((ROOT/'evidence/s7-fixture.json').read_text())
    state=json.loads((ROOT/'.local/s7-main-session.json').read_text())
    turns=[json.loads((ROOT/f'evidence/s7-turn-{i}.json').read_text()) for i in [1,2,3,4]]
    assert all(t['checks_passed'] and t['same_conversation']==state['thread_id'] for t in turns)
    board=check_preserved(api,fixture)
    assert layout_ids(board)==sorted([fixture['seed_chart_id'],state['trend_id'],state['bar_id']])
    manual=json.loads((ROOT/'evidence/s7-manual-edit.json').read_text())
    bar=api.call('GET',f"/api/v1/chart/{state['bar_id']}")['result'];params=json.loads(bar['params'])
    assert bar['slice_name']==manual['name'] and params['orientation']=='horizontal'
    assert all(params.get(k)==v for k,v in manual['params'].items() if k!='orientation')
    saved_trend=api.call('GET',f"/api/v1/chart/{state['trend_id']}")['result']
    observed=[decode(c) for c in turns[0]['calls'] if c['server']=='bi008_superset' and c['tool']=='get_chart_info' and c['arguments']['request'].get('identifier')==state['trend_id']][-1]
    assert json.loads(saved_trend['params'])==observed['form_data'] and saved_trend['slice_name']==observed['slice_name']
    # Actual data read back through the model's native tools, independently of its prose.
    for turn,expected in [(turns[0],[120,125]),(turns[1],[120,125])]:
        rows=[decode(c)['data'] for c in turn['calls'] if c['server']=='bi008_superset' and c['tool']=='get_chart_data' and c['arguments']['request'].get('identifier') in [state['trend_id'],state['bar_id']]]
        assert rows and any(sorted(Decimal(r['net_revenue']) for r in data)==expected for data in rows)
    cases=[]
    for entry in json.loads((ROOT/'s7/case-catalog.json').read_text()):
        item=dict(entry)
        if item['kind']=='deferred':item['status']='deferred';cases.append(item);continue
        proof_path=ROOT/'evidence'/item['evidence']
        if partial and not proof_path.exists():
            item['status']='blocked' if item['id']=='Q16' else 'pending'
            item['reason']='Codex CLI usage limit; not passed'
            cases.append(item);continue
        proof=json.loads(proof_path.read_text())
        if item['kind']=='fixed_preflight_fixture_not_llm':
            assert proof['rejected'].startswith('BI008_SEMANTIC_BINDING') and not proof['bi_write_attempted']
        else:assert proof['checks_passed']
        item.update({'status':'passed','metric':binding['metric'],'dataset_id':binding['dataset_id'],
                     'model_sha256':binding['wren_mdl_sha256'],'identity_scope':'local admin synthetic experiment',
                     'tool_calls':len(proof.get('calls',[]))})
        prefix=item['evidence'][:-5]
        raw=ROOT/f'.local/{prefix}-events.jsonl'
        if raw.exists():
            events=[json.loads(line) for line in raw.read_text().splitlines()]
            item['usage']=next((e['usage'] for e in events if e['type']=='turn.completed'),None)
        if proof.get('answer'):item['observed_status']=proof['answer']['status']
        if item['id']=='Q18':item['limitation']='rule explanation only; missing-customer data fixture belongs to prior W1, not rerun as an S7 model test'
        cases.append(item)
    assert len(cases)==24
    if not partial:assert sum(c['status']=='passed' for c in cases)==22
    # Verify every exposed-path asset mutation actually stayed in the bound experiment.
    actual_turns=turns+[json.loads((ROOT/'evidence/s7-native-update-failure.json').read_text())]
    questions=json.loads((ROOT/'s7/questions.json').read_text())
    actual_turns += [json.loads((ROOT/f"evidence/s7-case-{q['id']}.json").read_text()) for q in questions if (ROOT/f"evidence/s7-case-{q['id']}.json").exists()]
    passed_questions=len(actual_turns)-len(turns)-1
    for proof in actual_turns:
        for call in proof.get('calls',[]):
            if call['server']!='bi008_superset':continue
            request=call['arguments'].get('request',{})
            if call['tool']=='generate_chart':assert request['dataset_id']==binding['dataset_id']
            if call['tool']=='update_chart':assert request['identifier']==state['bar_id']
            if call['tool']=='add_chart_to_existing_dashboard':assert request['dashboard_id']==fixture['dashboard_id'] and request['chart_id'] in [state['trend_id'],state['bar_id']]
            assert call['tool'] not in ['execute_sql','create_virtual_dataset','generate_dashboard']
    browser=json.loads((ROOT/('evidence/s7-browser-dashboard.json' if partial else 'evidence/s7-browser.json')).read_text())
    assert browser['bar']['orientation']=='horizontal'
    if not partial:assert all(p['rendered'] for p in browser['previews'].values()) and len(browser['previews'])==2
    assert layout_ids(api.call('GET','/api/v1/dashboard/1')['result'])==[1,2,3]
    assert layout_ids(api.call('GET','/api/v1/dashboard/2')['result'])==[4,5]
    assert api.call('GET','/api/v1/dataset/2')['result']['sql'].strip().rstrip(';')==(ROOT/'sql/trino/orders_enriched_v1.sql').read_text().strip().rstrip(';')
    if not partial:
        date_binding=json.loads((ROOT/'evidence/s7-date-binding.json').read_text());validate_binding(date_binding,api)
        date_proof=json.loads((ROOT/'evidence/s7-dates.json').read_text())
        assert date_proof['checks_passed'] and date_proof['source_restored']
        assert len(date_proof['baseline_ranges'])==7 and len(date_proof['changed_source_ranges'])==3
        assert json.loads((ROOT/'evidence/s7-date-codex.json').read_text())['checks_passed']
        date_ui=json.loads((ROOT/'evidence/s7-dates-browser.json').read_text())
        assert len(date_ui['ranges'])==4 and date_ui['default_restored'] and date_ui['codex_preview']['rendered']
    source=json.loads((ROOT/'evidence/s7-source-baseline.json').read_text())
    original=json.loads((ROOT/'evidence/s2-sources.json').read_text())['reconciliation']
    assert source['snapshot_sha256']==original['snapshot_sha256']
    counts={status:sum(c['status']==status for c in cases) for status in ['passed','pending','blocked','deferred']}
    save(ROOT/'evidence/s7-case-results.json',{'verified_at_utc':datetime.now(timezone.utc).isoformat(),
        'counting':'24 acceptance items, not 24 independent prompts; multiple items may share a conversation step; unfinished cases are not passes',
        'stage_complete':not partial,**counts,'cases':cases})
    final={'verified_at_utc':datetime.now(timezone.utc).isoformat(),'dashboard_url':fixture['dashboard_url'],
        'scenario_session_id':state['thread_id'],'chart_ids':{'seed':fixture['seed_chart_id'],'trend':state['trend_id'],'industry':state['bar_id']},
        'metric':binding['metric'],'wren_mdl_sha256':binding['wren_mdl_sha256'],'dataset_id':binding['dataset_id'],
        'logical_conversation_steps':4,'native_failed_attempts_retained':1,'independent_questions_passed':len(questions),
        'acceptance_items':{'passed':22,'deferred':2},'manual_settings_preserved':True,'other_charts_and_layout_preserved':True,
        'repeat_save_no_mutation':True,'same_type_update_compatibility_required':True,
        'compatibility_adapter_sha256':hashlib.sha256((ROOT/'superset_mcp/start_mcp.py').read_bytes()).hexdigest(),
        'normal_dashboard_chart_ids':[1,2,3],'s6_dashboard_chart_ids':[4,5],'source_snapshot_sha256':source['snapshot_sha256'],
        'not_verified':['production concurrent edits/CAS','real network failure recovery','department authentication/revocation (deferred)',
            'hour/minute/timezone-aware timestamp ranges (business DATE only)','automatic Wren→Superset synchronization','large-schema/large-data capacity (S8)',
            'S7 missing-customer data fixture (question covers rule explanation only; prior W1 regression exists)'],
        'duration_tracking':'end-to-end client duration not separately collected in this run; actual token usage and tool query durations retained'}
    final.update({'stage_complete':not partial,'independent_questions_passed':passed_questions,'acceptance_items':counts})
    if not partial:
        final['date_extension']={'dataset_id':2,'native_mcp_date_adapter_required':True,'business_date_ranges_verified':True,
            'live_source_changes_verified':True,'real_codex_selected_date_verified':True,'dashboard_url':date_ui['dashboard_url']}
    if partial:
        final['not_verified'].append('remaining independent questions and table/KPI preview rendering: Codex account usage limit')
    save(ROOT/('evidence/s7-progress-state.json' if partial else 'evidence/s7-final-state.json'),final)
    print('S7 progress audited (incomplete)' if partial else 'S7 final passed',counts)


if __name__=='__main__':main()

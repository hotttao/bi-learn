"""S8 final gate: deterministic correctness, actual Agent trials and untouched S7."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from prepare_s7 import ROOT,save,validate_binding
from s3_assets import API
from s6_assets import layout_ids


def main():
    plan=json.loads((ROOT/'s8/plan.json').read_text());benchmark=json.loads((ROOT/'evidence/s8-benchmark.json').read_text())
    assert benchmark['all_correct'] and len(benchmark['results'])==9
    assert all(len(r['subsequent_ms'])==5 and r['correct'] for r in benchmark['results'])
    assert {r['orders'] for r in benchmark['results']}==set(plan['order_counts'])
    catalog=json.loads((ROOT/'evidence/s8-catalog.json').read_text());assert len(catalog['contexts'])==6
    assert all(c['strategy']=='full' and not c['selected_domain_subset'] for c in catalog['contexts'])
    assert all(r.get('correct') or r.get('caught_by_golden') for r in catalog['relationship_controls'].values())
    assert catalog['history_overlap_check'][0]['overlaps']==0
    for count in plan['catalog_model_counts']:
        p=ROOT/f'.local/s8/catalogs/{count}'
        assert json.loads((p/'schema_only/target/mdl.json').read_text())==json.loads((p/'schema_and_rules/target/mdl.json').read_text()),'Conditions changed executable schema'
    comparison=json.loads((ROOT/'evidence/s8-agent-comparison.json').read_text())
    assert len(comparison['trial_files'])==12
    trials=[json.loads((ROOT/'evidence'/f).read_text()) for f in comparison['trial_files']]
    assert all(t['status']!='execution_failed' and t['plan_checks_before_execution'] for t in trials)
    for condition in plan['model_comparison']['conditions']:
        assert sum(t['condition']==condition for t in trials)==6
    # Research can complete with observed wrong answers: report them, never flip a grade.
    source=json.loads((ROOT/'evidence/s8-source-baseline.json').read_text())
    assert source['snapshot_sha256']==json.loads((ROOT/'evidence/s2-sources.json').read_text())['reconciliation']['snapshot_sha256']
    api=API();validate_binding(json.loads((ROOT/'evidence/s7-date-binding.json').read_text()),api)
    for did,ids in [(1,[1,2,3]),(2,[4,5]),(3,[6,7,8]),(4,[9,10,11])]:
        assert layout_ids(api.call('GET',f'/api/v1/dashboard/{did}')['result'])==ids
    final={'verified_at_utc':datetime.now(timezone.utc).isoformat(),'stage':'S8','research_complete':True,
        'all_numerical_benchmarks_correct':True,'latency_goal_all_met':benchmark['all_latency_goals_met'],
        'orders_per_tier':plan['order_counts'],'catalog_model_counts':plan['catalog_model_counts'],
        'comparison':comparison['conditions'],'main_model_and_source_baseline_unchanged':True,
        'baseline_snapshot_sha256':source['snapshot_sha256'],'plan_sha256':hashlib.sha256((ROOT/'s8/plan.json').read_bytes()).hexdigest(),
        'not_proven':['production capacity/concurrency','cold-cache/P95 performance','actual 500 physical-source-table onboarding',
            'vector/domain retrieval quality','company business definitions','ontology inference'],
        'still_deferred':['department authorization','automatic Wren→BI semantic sync','concurrent BI edit protection','real network-fault recovery'],
        'review_status':'uncommitted; wait for user review'}
    save(ROOT/'evidence/s8-final-state.json',final);print('S8 final gate passed; results retained, no commit')


if __name__=='__main__':main()

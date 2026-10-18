"""S9 handoff gate: file-route evidence and unchanged main BI baseline."""
from datetime import datetime,timezone
import hashlib
import json
from prepare_s7 import ROOT,save,validate_binding
from prepare_s9_wiki import verify_release
from s3_assets import API
from s6_assets import layout_ids


def main():
    manifest=verify_release()
    summary=json.loads((ROOT/'evidence/s9-wiki-comparison.json').read_text())
    assert summary['file_wiki']['trials']==summary['file_wiki']['numeric_correct']==6
    assert summary['file_wiki']['metadata_mcp_calls']==0
    controls=json.loads((ROOT/'evidence/s9-wiki-controls.json').read_text())
    assert len(controls['faults_rejected'])==4 and controls['published_release_unchanged']
    assert controls['tools']==['dry_plan','dry_run','run_sql']
    source=json.loads((ROOT/'evidence/s9-source-baseline.json').read_text())
    assert source['snapshot_sha256']==json.loads((ROOT/'evidence/s2-sources.json').read_text())['reconciliation']['snapshot_sha256']
    mdl=hashlib.sha256((ROOT/'wren/target/mdl.json').read_bytes()).hexdigest()
    assert mdl==json.loads((ROOT/'evidence/w1-final-state.json').read_text())['mdl_sha256']
    api=API();validate_binding(json.loads((ROOT/'evidence/s7-date-binding.json').read_text()),api)
    for did,ids in [(1,[1,2,3]),(2,[4,5]),(3,[6,7,8]),(4,[9,10,11])]:
        assert layout_ids(api.call('GET',f'/api/v1/dashboard/{did}')['result'])==ids
    save(ROOT/'evidence/s9-final-state.json',{'verified_at_utc':datetime.now(timezone.utc).isoformat(),
        'stage':'S9','primary_route':'file Wiki + local Skill + Wren query-only MCP',
        'summary':summary['file_wiki'],'release_version':manifest['version'],
        'main_mdl_sha256':mdl,'source_fingerprint':source['snapshot_sha256'],
        's7_binding_and_dashboard_layouts_unchanged':True,
        'limitations':['synthetic curated topics only','500 models include empty aliases, not 500 source tables',
            'Wiki rules not all enforced as executable Wren metrics','no production onboarding or automatic sync',
            'file navigation not authorization','topic-MCP comparison real-model trials incomplete'],
        'review_status':'uncommitted; wait for user review'})
    print('S9 file-route handoff gate passed; no commit')


if __name__=='__main__':main()

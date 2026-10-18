"""Add only the prototype-local HTTP MCP allowlist; preserve all existing entries."""
import json
from pathlib import Path
import subprocess
import tomllib
from configure_w2 import ROOT, codex_path

TOOLS=['health_check','get_instance_info','list_datasets','get_dataset_info','list_charts','get_chart_info','get_chart_data',
       'get_chart_type_schema','generate_chart','update_chart','add_chart_to_existing_dashboard','get_dashboard_info','list_dashboards']


def main():
    p=ROOT/'.codex/config.toml';previous=p.read_text()
    expected={'url':'http://127.0.0.1:5008/mcp','enabled_tools':TOOLS,'required':True,
              'startup_timeout_sec':30,'tool_timeout_sec':120,'default_tools_approval_mode':'approve'}
    old=tomllib.loads(previous).get('mcp_servers',{}).get('bi008_superset')
    if old is not None and old!=expected:raise SystemExit('Existing bi008_superset differs; review before replacing')
    if old is None:p.write_text(previous+'\n[mcp_servers.bi008_superset]\n'+''.join(f'{k} = {json.dumps(v)}\n' for k,v in expected.items()))
    result=subprocess.run([str(codex_path()),'mcp','list','--json'],cwd=ROOT,capture_output=True,text=True,check=True)
    entries=json.loads(result.stdout);entry=next(s for s in entries if s['name']=='bi008_superset')
    (ROOT/'evidence/s7-config.json').write_text(json.dumps({'entry':entry,'enabled_tools':TOOLS,
        'scope':'prototype-local only; global/repository-root configuration untouched','actual_model_invocation_verified':any(
            json.loads(proof.read_text()).get('checks_passed') and any(
                c.get('server')=='bi008_superset' for c in json.loads(proof.read_text()).get('calls',[]))
            for proof in (ROOT/'evidence').glob('s7-turn-*.json'))},ensure_ascii=False,indent=2)+'\n')
    local=ROOT/'.local/s7-analysis';local.mkdir(exist_ok=True)
    binding=json.loads((ROOT/'evidence/s7-binding.json').read_text());fixture=json.loads((ROOT/'evidence/s7-fixture.json').read_text())
    context=(ROOT/'s7/ANALYSIS.md').read_text()+'\n## 当前已核验绑定\n\n'+json.dumps({k:v for k,v in binding.items() if k!='comparison'},ensure_ascii=False,indent=2)
    context+='\n\n## 本次指定看板\n\n'+json.dumps({k:fixture[k] for k in ['dashboard_id','dashboard_url','seed_chart_id','manual_note_id']},ensure_ascii=False,indent=2)+'\n'
    (local/'AGENTS.md').write_text(context)
    print('Codex prototype-local HTTP MCP loaded; analysis context prepared; SQL/dataset creation tools excluded.')


if __name__=='__main__':main()

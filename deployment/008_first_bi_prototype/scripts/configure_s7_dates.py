"""Build the current all-date analysis context from an audited binding."""
import json
from prepare_s7 import ROOT,validate_binding


def main():
    binding=json.loads((ROOT/'evidence/s7-date-binding.json').read_text());validate_binding(binding)
    assets=json.loads((ROOT/'evidence/s7-date-assets.json').read_text())
    context=(ROOT/'s7/DATE_ANALYSIS.md').read_text()+'\n## 当前已核验绑定\n\n'+json.dumps(binding,ensure_ascii=False,indent=2)
    context+='\n\n## 当前指定看板\n\n'+json.dumps(assets,ensure_ascii=False,indent=2)+'\n'
    p=ROOT/'.local/s7-date-analysis';p.mkdir(exist_ok=True);(p/'AGENTS.md').write_text(context)
    print('All-date Codex analysis context prepared; existing MCP allowlists unchanged')


if __name__=='__main__':main()

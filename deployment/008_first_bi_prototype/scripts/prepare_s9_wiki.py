"""Publish an isolated file-readable release, retaining native Wren YAML."""
import hashlib
import json
from pathlib import Path
import re
import shutil
from guarded_wren_mcp import ROOT,revision,ScopeError
from prepare_s7 import save
from topic_wren_mcp import PROJECT

RELEASE=ROOT/'.local/s9-wiki-release'


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_release(root=RELEASE):
    manifest=json.loads((root/'release.json').read_text())
    if revision(PROJECT)!=manifest['wren_source_revision']:raise ScopeError('BI008_WIKI_REVISION: executing Wren source differs from published files')
    actual={p.relative_to(root).as_posix():digest(p) for p in root.rglob('*') if p.is_file() and p.name!='release.json'}
    if actual!=manifest['files']:raise ScopeError('BI008_WIKI_REVISION: release files changed; review and republish')
    return manifest


def main():
    if RELEASE.exists():
        verify_release();print('Existing file release verified; preserved');return
    RELEASE.mkdir(parents=True)
    shutil.copytree(ROOT/'s9/wiki',RELEASE/'wiki')
    shutil.copytree(PROJECT/'models',RELEASE/'wren/models')
    (RELEASE/'raw').mkdir();shutil.copyfile(ROOT/'s8/rules.md',RELEASE/'raw/reviewed-sales-rules.md')
    skill=RELEASE/'.agents/skills/bi008-semantic-context';skill.parent.mkdir(parents=True);shutil.copytree(ROOT/'s9/skills/bi008-semantic-context',skill)
    (RELEASE/'AGENTS.md').write_text('本次是只读合成销售分析。先读取 .agents/skills/bi008-semantic-context/SKILL.md，增量阅读 Wiki 和原生 Wren YAML；不读工作区外文件，不用元数据 MCP，不改任何文件。业务数据只通过查询 MCP。\n')
    files={p.relative_to(RELEASE).as_posix():digest(p) for p in RELEASE.rglob('*') if p.is_file()}
    manifest={'version':'s9-wiki-v1','status':'reviewed_synthetic_experiment','wren_source_revision':revision(PROJECT),
              'mdl_sha256':digest(PROJECT/'target/mdl.json'),'files':files,'not_an_automatic_sync':True}
    save(RELEASE/'release.json',manifest)
    for p in (RELEASE/'wiki').rglob('*.md'):
        for link in re.findall(r'\]\(([^)]+)\)',p.read_text()):
            if '://' not in link:assert (p.parent/link).exists(),(p,link)
    verify_release();save(ROOT/'evidence/s9-wiki-release.json',{'version':manifest['version'],'files':len(files),'native_models':500,
      'wren_source_revision':manifest['wren_source_revision'],'mdl_sha256':manifest['mdl_sha256'],
      'skill_path':'s9/skills/bi008-semantic-context/SKILL.md','scope':'published native YAML plus topic navigation; no answer data; no global skill installation'})
    print('Published file Wiki with native model sources and source binding')


if __name__=='__main__':main()

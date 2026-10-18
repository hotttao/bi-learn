"""Local W3 fixture publication: hashed bundles, explicit fixture review and pointer.

Not a production deployment service. The calling regression must verify the model
and pass its evidence. No actual business reviewer or enterprise release is implied.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT/'.local/w3'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def inventory(project):
    return {str(p.relative_to(project)):digest(p.read_bytes()) for p in sorted(project.rglob('*')) if p.is_file() and p.name != 'release.json' and '__pycache__' not in p.parts}


def verify_bundle(project):
    descriptor = json.loads((project/'release.json').read_text())
    if descriptor['files'] != inventory(project):
        raise ValueError('BI008_RELEASE_INTEGRITY: bundle contents differ from reviewed model/knowledge snapshot')
    return descriptor


def validate_context(candidate, metric):
    from wren import context
    manifest = json.loads((candidate/'target/mdl.json').read_text())
    if manifest != context.build_json(candidate):
        raise ValueError('BI008_RELEASE_CONTEXT: compiled MDL differs from model source')
    contract = json.loads((candidate/'semantic-contract.json').read_text())
    texts = [(candidate/'knowledge/rules/general.md').read_text(),
             (candidate/'knowledge/sql/verified.md').read_text(),
             next(c for c in manifest['cubes'] if c['name']=='net_revenue')['properties']['description']]
    if contract['metric'] != metric or not all(metric in text for text in texts):
        raise ValueError('BI008_RELEASE_CONTEXT: metric versions differ across contract/model/rules/examples')


def publish(candidate, release_id, metric, checks, review):
    if review != {'scope':'synthetic_fixture','reviewer':'regression_harness','decision':'approve'}:
        raise ValueError('W3 publisher only supports explicit synthetic-fixture review')
    if not checks or not all(checks.values()):
        raise ValueError('BI008_RELEASE_CHECKS: failed or missing gates; pointer unchanged')
    validate_context(candidate, metric)
    releases = STORE/'releases'; releases.mkdir(parents=True, exist_ok=True)
    destination = releases/release_id
    if destination.exists():
        raise ValueError('Immutable release id already exists')
    staging = releases/('.staging-'+release_id)
    if staging.exists():shutil.rmtree(staging)
    shutil.copytree(candidate, staging)
    descriptor = {'release_id':release_id,'metric':metric,'scope':'synthetic_fixture',
                  'checks':checks,'review':review,'files':inventory(staging)}
    (staging/'release.json').write_text(json.dumps(descriptor,ensure_ascii=False,indent=2)+'\n')
    verify_bundle(staging)
    staging.rename(destination)
    activate(release_id)
    return destination


def activate(release_id):
    if Path(release_id).name != release_id or release_id.startswith('.'):
        raise ValueError('Invalid release id')
    project = STORE/'releases'/release_id
    descriptor = verify_bundle(project)
    pointer = {'release_id':release_id,'descriptor_sha256':digest((project/'release.json').read_bytes())}
    temporary = STORE/'current.tmp.json'
    temporary.write_text(json.dumps(pointer,indent=2)+'\n')
    os.replace(temporary, STORE/'current.json')
    return descriptor


def resolve():
    pointer = json.loads((STORE/'current.json').read_text())
    release_id = pointer['release_id']
    if Path(release_id).name != release_id or release_id.startswith('.'):
        raise ValueError('Invalid release pointer')
    project = STORE/'releases'/release_id
    if digest((project/'release.json').read_bytes()) != pointer['descriptor_sha256']:
        raise ValueError('BI008_RELEASE_INTEGRITY: descriptor changed')
    return project, verify_bundle(project), pointer

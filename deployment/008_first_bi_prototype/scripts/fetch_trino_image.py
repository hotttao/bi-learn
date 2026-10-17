"""Optional network fallback: fetch official Trino 481 bytes via a mirror.

No root required to fetch. Every compressed blob and expanded layer is checked;
then load .local/trino-image/image.tar using sudo docker load -i FILE.
AMD64 manifest digest was obtained from Docker Hub's official 481 tag API.
"""
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

import requests

from source_db import ROOT

MANIFEST = 'sha256:4b56ba858f31c4420df762015b63b0c0bcf712109d6401356732af43581c9cd3'
BASE = 'https://docker.1ms.run'
DEST = ROOT / '.local/trino-image'


def session():
    s = requests.Session()
    s.trust_env = False
    s.proxies = {"https://cloudfront-docker-cf.mrs.1ms.run": "http://127.0.0.1:7897"}
    r = s.get(BASE + '/openapi/v1/auth/token', params={
        'service': 'docker.1ms.run', 'scope': 'repository:trinodb/trino:pull'}, timeout=30)
    r.raise_for_status()
    s.headers['Authorization'] = 'Bearer ' + r.json()['token']
    return s


def digest(path):
    with path.open('rb') as f:
        return 'sha256:' + hashlib.file_digest(f, 'sha256').hexdigest()


def fetch(blob):
    path = DEST / blob['digest'].split(':')[1]
    if path.exists() and digest(path) == blob['digest']:
        return path
    s = session()
    url = BASE + '/v2/trinodb/trino/blobs/' + blob['digest']
    if blob['size'] > 16 * 1024 * 1024:
        head = s.get(url, allow_redirects=False, timeout=30)
        head.raise_for_status()
        location = head.headers.get('Location')
        if not location:
            raise RuntimeError('Expected CDN redirect for large blob')
        chunks = [(i, min(i + 8*1024*1024 - 1, blob['size']-1)) for i in range(0, blob['size'], 8*1024*1024)]
        def part(bounds):
            first, last = bounds
            target = path.with_name(path.name + f'.part-{first}')
            if target.exists() and target.stat().st_size == last-first+1:
                return target
            client = requests.Session()
            client.trust_env = False
            client.proxies = {"https": "http://127.0.0.1:7897"}
            for attempt in range(3):
                try:
                    offset = first + (target.stat().st_size if target.exists() else 0)
                    with client.get(location, headers={'Range': f'bytes={offset}-{last}'}, stream=True, timeout=(15,60)) as r:
                        r.raise_for_status()
                        if r.status_code != 206 or r.headers.get('Content-Range', '').split('/')[0] != f'bytes {offset}-{last}':
                            raise ValueError('CDN range request not honored')
                        with target.open('ab') as f:
                            for chunk in r.iter_content(1024*1024): f.write(chunk)
                    if target.stat().st_size != last-first+1: raise ValueError('Incomplete range')
                    return target
                except (requests.RequestException, ValueError):
                    if attempt == 2: raise
        with ThreadPoolExecutor(max_workers=48) as executor:
            parts = list(executor.map(part, chunks))
        with path.open('wb') as f:
            for p in parts:
                with p.open('rb') as src: shutil.copyfileobj(src, f, 1024*1024)
        if digest(path) != blob['digest']: raise ValueError('Segmented blob digest mismatch')
        for p in parts: p.unlink()
        print('Verified blob:', blob['digest'], path.stat().st_size, flush=True)
        return path
    with s.get(url, stream=True, timeout=(15,60)) as response:
        response.raise_for_status()
        with path.open('wb') as f:
            for chunk in response.iter_content(1024 * 1024):
                f.write(chunk)
    if path.stat().st_size != blob['size'] or digest(path) != blob['digest']:
        raise ValueError('Blob digest/size mismatch: ' + blob['digest'])
    print('Verified blob:', blob['digest'], path.stat().st_size, flush=True)
    return path


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    s = session()
    response = s.get(BASE + '/v2/trinodb/trino/manifests/' + MANIFEST,
                     headers={'Accept': 'application/vnd.docker.distribution.manifest.v2+json'}, timeout=30)
    response.raise_for_status()
    if 'sha256:' + hashlib.sha256(response.content).hexdigest() != MANIFEST:
        raise ValueError('Manifest differs from official Docker Hub digest')
    manifest = response.json()
    config = fetch(manifest['config'])
    image_config = json.loads(config.read_text())
    with ThreadPoolExecutor(max_workers=4) as executor:
        layers = list(executor.map(fetch, manifest['layers']))
    expanded = []
    for index, layer in enumerate(layers):
        target = DEST / f'layer-{index}.tar'
        with gzip.open(layer, 'rb') as src, target.open('wb') as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        if digest(target) != image_config['rootfs']['diff_ids'][index]:
            raise ValueError(f'Expanded layer {index} differs from image config')
        expanded.append(target)
    save_manifest = [{'Config': config.name, 'RepoTags': ['trinodb/trino:481'], 'Layers': [p.name for p in expanded]}]
    (DEST / 'manifest.json').write_text(json.dumps(save_manifest))
    with tarfile.open(DEST / 'image.tar', 'w') as archive:
        for p in [config, DEST / 'manifest.json', *expanded]:
            archive.add(p, arcname=p.name)
    evidence = {'source': BASE, 'official_tag_api': 'https://hub.docker.com/v2/repositories/trinodb/trino/tags/481',
                'official_amd64_manifest_digest': MANIFEST, 'config_digest': manifest['config']['digest'],
                'compressed_layer_digests': [b['digest'] for b in manifest['layers']],
                'all_compressed_and_expanded_digests_checked': True}
    (ROOT / 'evidence/s4-image-download.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print('Verified archive:', DEST / 'image.tar', flush=True)


if __name__ == '__main__':
    main()

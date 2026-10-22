"""Restore the exact Chinese encoder from verified, parallel-downloadable LFS parts."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'models/pretrained/followup/bert-base-chinese/transport.json'


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def restore(manifest_path=MANIFEST, root=ROOT):
    root = Path(root).resolve()
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    def safe(relative):
        path = (root / relative).resolve()
        if root not in path.parents:
            raise ValueError('asset path outside repository')
        return path
    target = safe(manifest['target'])
    if target.exists() and target.stat().st_size == manifest['bytes'] and sha256(target) == manifest['sha256']:
        return {'ok': True, 'restored': False, 'reason': 'matching model already exists'}
    parts = []
    for entry in manifest['parts']:
        path = safe(entry['path'])
        if not path.is_file() or path.stat().st_size != entry['bytes'] or sha256(path) != entry['sha256']:
            raise ValueError('missing/corrupt part; finish git lfs pull: ' + entry['path'])
        parts.append(path)
    if sum(p.stat().st_size for p in parts) != manifest['bytes']:
        raise ValueError('invalid transport manifest size')
    temporary = target.with_name(target.name + '.restore.tmp')
    # Exclusive creation preserves any previous interrupted assembly for inspection.
    with temporary.open('xb') as output:
        for path in parts:
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
                    output.write(chunk)
    if sha256(temporary) != manifest['sha256']:
        raise ValueError('assembled model checksum mismatch; temporary file retained')
    if target.exists():
        raise ValueError('existing model differs; restored temporary file retained; preserve/inspect existing model first')
    temporary.replace(target)
    return {'ok': True, 'restored': True, 'bytes': manifest['bytes'], 'sha256': manifest['sha256']}


if __name__ == '__main__':
    print(json.dumps(restore(), indent=2))

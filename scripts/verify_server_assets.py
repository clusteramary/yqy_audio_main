"""Verify uploaded follow-up assets after LFS pull. No training or test scoring."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify(manifest):
    results = []
    for entry in manifest['files']:
        path = (ROOT / entry['path']).resolve()
        if ROOT not in path.parents:
            raise ValueError('asset outside repository')
        error = None
        if not path.is_file():
            error = 'missing; for Chinese encoder run python scripts/restore_server_assets.py after git lfs pull'
        elif path.stat().st_size != entry['bytes']:
            error = 'size mismatch; ensure git lfs pull completed'
        else:
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
                    digest.update(chunk)
            if digest.hexdigest() != entry['sha256']:
                error = 'checksum mismatch'
        if error:
            results.append({'path': entry['path'], 'error': error})
    return {'ok': not results, 'files_checked': len(manifest['files']), 'errors': results,
            'training_started': False,
            'scope': 'uploaded follow-up assets only; MELD media/encoders checked by their own manifests'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, default=ROOT / 'docs/server_sync_assets_2026-10-22.json')
    args = p.parse_args()
    report = verify(json.loads(args.manifest.read_text(encoding='utf-8')))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if report['ok'] else 1)

import hashlib
import importlib.util
import json
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location('restore_server_assets', Path(__file__).resolve().parents[1] / 'scripts/restore_server_assets.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_restore_exact_and_idempotent_and_corruption_rejected():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        parts = [b'first model bytes', b'second model bytes']
        entries = []
        for i, content in enumerate(parts):
            path = root / str(i)
            path.write_bytes(content)
            entries.append({'path': str(i), 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()})
        content = b''.join(parts)
        manifest = root / 'manifest.json'
        manifest.write_text(json.dumps({'target': 'model', 'bytes': len(content),
                            'sha256': hashlib.sha256(content).hexdigest(), 'parts': entries}))
        assert module.restore(manifest, root)['restored']
        assert (root / 'model').read_bytes() == content
        assert not module.restore(manifest, root)['restored']
        (root / 'model').rename(root / 'saved-model')
        (root / '0').write_bytes(b'bad')
        import pytest
        with pytest.raises(ValueError, match='git lfs pull'):
            module.restore(manifest, root)
        assert not (root / 'model').exists()

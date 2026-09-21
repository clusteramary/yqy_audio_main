"""Reproducibility metadata for every offline run."""
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def provenance(paths, config):
    files = {str(path): hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in paths}
    root = Path(__file__).resolve().parents[1]
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root))
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = None, None
    return {"utc": datetime.now(timezone.utc).isoformat(), "python": sys.version,
            "platform": platform.platform(), "git_commit": commit, "git_dirty": dirty,
            "input_sha256": files, "config": config}


def write_report(path, report):
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")

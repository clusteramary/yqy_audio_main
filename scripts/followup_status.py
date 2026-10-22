"""Check local innovation-two assets and checksums without training."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from interview_robot.followup_data import file_sha, load_json

if __name__ == "__main__":
    raw = Path("data/raw/followup")
    manifest = load_json(raw / "manifest.json")
    errors = []
    for name, entry in manifest["files"].items():
        if not (raw / name).exists() or file_sha(raw / name) != entry["sha256"]:
            errors.append(name)
    processed = Path("data/processed/followup")
    report = load_json(processed / "report.json")
    if report["raw_manifest_sha256"] != file_sha(raw / "manifest.json"):
        errors.append("raw manifest changed since preparation")
    for dataset in ("fq-bank", "kdconv"):
        for split, entry in report[dataset]["splits"].items():
            path = processed / dataset / f"{split}.jsonl"
            if not path.exists() or file_sha(path) != entry["sha256"]:
                errors.append(str(path))
    chinese = Path("models/pretrained/followup/bert-base-chinese")
    for name, sha in load_json(chinese / "download.json")["files"].items():
        if not (chinese / name).exists() or file_sha(chinese / name) != sha:
            errors.append(str(chinese / name))
    english = Path("models/pretrained/meld/distilbert-base-uncased")
    if not (english / "model.safetensors").exists():
        errors.append("missing English encoder: " + str(english))
    active = Path("data/processed/followup-v2-mixed")
    v2_counts = {}
    if not (active / "report.json").exists():
        errors.append("active KdConv v2 dataset missing; run scripts/prepare_followup_v2.py")
    else:
        v2 = load_json(active / "report.json")
        if v2.get("protocol") != "followup-candidates-v2":
            errors.append("invalid active candidate protocol")
        for split, entry in v2["splits"].items():
            path = active / "kdconv" / f"{split}.jsonl"
            if not path.exists() or file_sha(path) != entry["sha256"]:
                errors.append(str(path))
            v2_counts[split] = entry["rows"]
    print(json.dumps({"ok": not errors, "errors": errors,
        "active_kdconv_protocol": "v2-mixed", "active_kdconv_counts": v2_counts,
        "raw_files": len(manifest["files"]), "raw_bytes": sum(f["bytes"] for f in manifest["files"].values()),
        "splits": {d: report[d]["splits"] for d in ("fq-bank", "kdconv")},
        "server_feature_caches": {"fq-bank": Path("data/features/followup/fq-bank/manifest.json").exists(),
                                  "kdconv-v2": Path("data/features/followup-v2-mixed/kdconv/manifest.json").exists()},
        "note": "Feature extraction and training are separate explicit steps."}, ensure_ascii=False, indent=2))
    sys.exit(1 if errors else 0)

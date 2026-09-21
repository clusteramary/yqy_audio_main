"""Read-only preparation status. --verify also hashes the complete archive."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from interview_robot.meld_data import ARCHIVE_BYTES, ARCHIVE_SHA256, COUNTS, dump, media_index, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/meld/preparation_status.json")
    args = parser.parse_args()
    raw, processed = ROOT / "data/raw/meld", ROOT / "data/processed/meld"
    archive = raw / "MELD.Raw.tar.gz"
    parts = raw / "MELD.Raw.tar.gz.parts"
    downloaded = archive.stat().st_size if archive.exists() else sum(p.stat().st_size for p in parts.glob("*") if p.suffix in (".bin", ".part"))
    clips = media_index(raw)
    raw_counts = {s: sum(key[0] == s for key in clips) for s in COUNTS}
    labeled_counts, missing = {}, {}
    for split in COUNTS:
        index = processed / f"{split}.jsonl"
        rows = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()] if index.exists() else []
        labeled_counts[split] = sum(row.get("media") is not None for row in rows)
        missing[split] = [row["id"] for row in rows if row.get("media") is None]
    weights = ROOT / "models/pretrained/meld/manifest.json"
    report = {"archive_exists": archive.exists(), "downloaded_bytes": downloaded, "archive_expected_bytes": ARCHIVE_BYTES,
              "archive_checksum_verified_now": False, "raw_mp4_files_by_split": raw_counts,
              "labeled_media_present_by_split": labeled_counts, "missing_labeled_media_ids": missing,
              "encoder_manifest_exists": weights.exists(), "formal_features_ready": all((ROOT / f"data/features/meld-v1/{s}.extraction.json").exists() for s in COUNTS),
              "note": "Data/weights readiness is separate from complete server feature extraction and training."}
    if args.verify and archive.exists():
        report["archive_sha256"] = sha256(archive)
        report["archive_checksum_verified_now"] = report["archive_sha256"] == ARCHIVE_SHA256
        if not report["archive_checksum_verified_now"]:
            raise ValueError("raw archive checksum failed")
    protocol = processed / "protocol.json"
    if protocol.exists():
        report["protocol"] = json.loads(protocol.read_text(encoding="utf-8"))
    if weights.exists():
        manifest = json.loads(weights.read_text(encoding="utf-8"))
        report["encoder_bytes"] = sum(item["bytes"] for item in manifest["files"].values())
    audit = processed / "audit.json"
    if audit.exists():
        audit_value = json.loads(audit.read_text(encoding="utf-8"))
        report["known_corrupt_media_ids"] = {s: v["known_upstream_corrupt_media_ids"] for s, v in audit_value["splits"].items()}
        report["unexpected_missing_media_ids"] = {s: v["unexpected_missing_media_ids"] for s, v in audit_value["splits"].items()}
    extraction = raw / "extraction.json"
    report["raw_data_ready"] = (archive.exists() and archive.stat().st_size == ARCHIVE_BYTES and extraction.exists()
                                and all(not values for values in report.get("unexpected_missing_media_ids", {}).values()))
    dump(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

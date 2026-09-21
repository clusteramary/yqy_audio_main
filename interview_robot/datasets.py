"""Adapters for verified source formats. Never use a future answer as knowledge."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .data import require, validate_ranking, write_jsonl
from .experiment import provenance, write_report


def fqbank_record(source: dict, split: str):
    history = []
    for turn in source["dialog_history"]:
        history.extend([turn["utterance"], turn["response"]])
    history.extend([source["current_utterance"], source["current_response"]])
    identity = source["id"]
    candidates, duplicates = {}, 0
    for category, relevance in (("valid", 1), ("invalid", 0)):
        for item in source["candidate_utterances"][category]:
            text = item["utterance"].strip()
            require(bool(text), "empty FQ candidate")
            if text in candidates:
                require(candidates[text]["relevance"] == relevance, "conflicting FQ relevance for identical text")
                duplicates += 1
                continue
            cid = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
            candidates[text] = {"id": cid, "text": text, "relevance": relevance}
    row = {"schema_version": 1, "dataset": "FQ-Bank", "split": split,
           "id": f"{split}:{identity['dialogue']}:{identity['turn']}",
           "group_id": str(identity["dialogue"]), "history": history, "knowledge": [],
           "knowledge_source": "none",
           "candidates": sorted(candidates.values(), key=lambda q: q["id"])}
    validate_ranking(row)
    return row, duplicates


def convert_fqbank(input_path, output_path, split):
    require(split in {"train", "dev", "test"}, "invalid split")
    source = json.loads(Path(input_path).read_text(encoding="utf-8-sig"))
    require(isinstance(source, list) and bool(source), "expected nonempty FQ-Bank list")
    rows, duplicates, skipped = [], 0, []
    for index, item in enumerate(source):
        try:
            row, count = fqbank_record(item, split)
        except ValueError as exc:
            if "conflicting FQ relevance" not in str(exc):
                raise
            skipped.append({"index": index, "id": item.get("id"), "reason": str(exc)})
            continue
        rows.append(row)
        duplicates += count
    require(bool(rows), "conversion produced no valid rows")
    write_jsonl(output_path, rows)
    report = {"source_rows": len(source), "converted_rows": len(rows), "duplicate_candidates_removed": duplicates,
              "skipped_conflicts": skipped, "protocol": "exact-text-dedup-v1; candidate ids sorted independently of labels",
              "metadata": provenance([input_path], {"split": split, "knowledge": "none; no future answer used"})}
    write_report(str(output_path) + ".meta.json", report)
    return {"source_rows": len(source), "converted_rows": len(rows),
            "duplicate_candidates_removed": duplicates, "conflicting_rows_skipped": len(skipped),
            "audit_report": str(output_path) + ".meta.json"}

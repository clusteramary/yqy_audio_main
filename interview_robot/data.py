"""Versioned JSONL contracts. Ground truth never enters inference objects."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def number(value: Any, name: str, lower: float = 0, upper: float = math.inf) -> float:
    require(isinstance(value, (int, float)) and not isinstance(value, bool), f"{name}: expected number")
    require(math.isfinite(value) and lower <= value <= upper, f"{name}: out of range")
    return float(value)


def nonempty(value: Any, name: str) -> str:
    require(isinstance(value, str) and bool(value.strip()), f"{name}: expected nonempty string")
    return value


def read_jsonl(path: str | Path) -> list[dict]:
    records = []
    with Path(path).open(encoding="utf-8-sig") as stream:
        for line_no, line in enumerate(stream, 1):
            if line.strip():
                try:
                    row = json.loads(line)
                    require(isinstance(row, dict), "expected object")
                    records.append(row)
                except (ValueError, TypeError) as exc:
                    raise ValueError(f"{path}:{line_no}: {exc}") from exc
    require(bool(records), f"{path}: empty dataset")
    return records


def write_jsonl(path: str | Path, records) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as stream:
        for row in records:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def validate_ranking(row: dict, labeled: bool = True) -> None:
    require(row.get("schema_version") == SCHEMA_VERSION, "unsupported schema_version")
    for field in ("id", "group_id", "dataset", "split"):
        nonempty(row.get(field), field)
    for field in ("history", "knowledge"):
        values = row.get(field)
        require(isinstance(values, list), f"{field}: expected list")
        require(all(isinstance(x, str) and x.strip() for x in values), f"{field}: expected text items")
    require(bool(row["history"]), "history must not be empty")
    if row["knowledge"]:
        nonempty(row.get("knowledge_source"), "knowledge_source")
    candidates = row.get("candidates")
    require(isinstance(candidates, list) and len(candidates) >= 2, "need at least two candidates")
    ids, texts, positive = set(), set(), False
    for item in candidates:
        require(isinstance(item, dict), "candidate must be an object")
        cid = nonempty(item.get("id"), "candidate.id")
        text = nonempty(item.get("text"), "candidate.text").strip()
        require(cid not in ids and text not in texts, "duplicate candidate id/text")
        ids.add(cid)
        texts.add(text)
        if labeled:
            rel = item.get("relevance")
            require(isinstance(rel, int) and not isinstance(rel, bool) and 0 <= rel <= 3,
                    "relevance must be integer 0..3")
            positive |= rel > 0
    if labeled:
        require(positive, "evaluation requires at least one relevant candidate")


def validate_observation(row: dict) -> None:
    require(row.get("schema_version") == SCHEMA_VERSION, "unsupported schema_version")
    for field in ("session_id", "utterance_id"):
        nonempty(row.get(field), field)
    now = number(row.get("timestamp"), "timestamp")
    for field in ("robot_playing",):
        require(isinstance(row.get(field), bool), f"{field}: expected boolean")
    modalities = row.get("modalities")
    require(isinstance(modalities, dict), "modalities: expected object")
    require(not (set(modalities) - {"audio", "text", "visual"}), "unknown modality")
    for name, item in modalities.items():
        require(isinstance(item, dict), "modality must be an object")
        stamp = number(item.get("timestamp"), f"{name}.timestamp", upper=now)
        number(item.get("confidence"), f"{name}.confidence", upper=1)
        require(stamp <= now, "future evidence is not allowed")
        if name == "audio":
            require(isinstance(item.get("speaking"), bool), "audio.speaking must be boolean")
        if name == "text":
            require(isinstance(item.get("text"), str), "text.text must be string")
            require(isinstance(item.get("final"), bool), "text.final must be boolean")
        if name == "visual":
            if item.get("speaker_id") is not None:
                nonempty(item["speaker_id"], "visual.speaker_id")
            require(item.get("affect", "unknown") in {"positive", "neutral", "negative", "unknown"},
                    "invalid visual.affect")
    labels = row.get("labels", {})
    require(isinstance(labels, dict), "labels must be an object")
    if labels.get("turn") is not None:
        require(labels["turn"] in {"HOLD", "SHIFT", "IDLE", "ROBOT"}, "invalid turn label")
    if labels.get("affect") is not None:
        require(labels["affect"] in {"positive", "neutral", "negative"}, "invalid affect label")


def validate_splits(paths: list[str]) -> dict:
    """Keep every conversation in one partition; reject exact context leakage."""
    owners, contexts, ids = {}, {}, set()
    counts = {}
    for path in paths:
        rows = read_jsonl(path)
        for row in rows:
            validate_ranking(row)
            partition = (row["dataset"], row["split"])
            uid = (row["dataset"], row["id"])
            require(uid not in ids, f"duplicate sample {uid}")
            ids.add(uid)
            group = (row["dataset"], row["group_id"])
            require(group not in owners or owners[group] == partition, f"group leakage: {group}")
            owners[group] = partition
            digest = hashlib.sha256("\n".join(row["history"]).strip().encode()).hexdigest()
            require(digest not in contexts or contexts[digest] == partition,
                    "identical dialogue context appears across datasets/splits")
            contexts[digest] = partition
        counts[str(path)] = len(rows)
    return counts

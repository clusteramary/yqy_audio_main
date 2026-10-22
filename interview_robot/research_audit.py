"""Training-free candidate shortcut audits; never use test to tune the protocol."""
from collections import Counter

from .followup_data import norm, file_sha, save_json
from .data import read_jsonl, validate_ranking
from .metrics import average_metrics, ranking_metrics


def membership_audit(train, evaluation):
    if {r["split"] for r in train} != {"train"} or {r["split"] for r in evaluation} != {"dev"}:
        raise ValueError("development audit accepts train/dev only; test remains sealed")
    pool = {norm(c["text"]) for r in train for c in r["candidates"] if c["relevance"] > 0}
    counts, metrics = Counter(), []
    for row in evaluation:
        validate_ranking(row)
        for candidate in row["candidates"]:
            label = "positive" if candidate["relevance"] > 0 else "negative"
            counts[label] += 1
            counts["seen_" + label] += int(norm(candidate["text"]) in pool)
        ranked = sorted(row["candidates"], key=lambda c: (norm(c["text"]) in pool, c["id"]))
        metrics.append(ranking_metrics([c["id"] for c in ranked], {c["id"]: c["relevance"] for c in ranked}))
    rates = {label: counts["seen_" + label] / max(1, counts[label]) for label in ("positive", "negative")}
    return {"counts": dict(counts), "seen_rates": rates,
        "absolute_membership_gap": abs(rates["positive"] - rates["negative"]),
        "unseen_first_metrics": average_metrics(metrics), "uses_history_or_knowledge": False,
        "interpretation": "Diagnostic shortcut, not a trained model or proof of absence of other shortcuts."}


def audit_paths(train_path, dev_path, output):
    result = membership_audit(read_jsonl(train_path), read_jsonl(dev_path))
    result["inputs"] = {"train": {"path": str(train_path), "sha256": file_sha(train_path)},
                        "dev": {"path": str(dev_path), "sha256": file_sha(dev_path)}}
    save_json(output, result)
    return result

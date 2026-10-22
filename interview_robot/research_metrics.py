"""Uncertainty diagnostics and dialogue-cluster paired intervals, not training."""
from collections import defaultdict
import math
import numpy as np

from .metrics import ranking_metrics


def probability_report(labels, probabilities, bins=10):
    p = np.asarray(probabilities, dtype=np.float64)
    y = np.asarray(labels)
    if p.ndim != 2 or not len(p) or p.shape[1] < 2 or y.shape != (len(p),):
        raise ValueError("nonempty [N,C] probabilities and [N] labels required")
    if y.dtype.kind not in "iu" or not ((0 <= y) & (y < p.shape[1])).all():
        raise ValueError("invalid class labels")
    if type(bins) is not int or bins < 1:
        raise ValueError("bins must be a positive integer")
    if not np.isfinite(p).all() or not ((p >= 0) & (p <= 1)).all() or not np.allclose(p.sum(1), 1, atol=1e-6):
        raise ValueError("probabilities must be finite and sum to one")
    confidence = p.max(1)
    correct = p.argmax(1) == y
    assignment = np.minimum((confidence * bins).astype(int), bins - 1)
    table, ece = [], 0.0
    for b in range(bins):
        chosen = assignment == b
        count = int(chosen.sum())
        acc = float(correct[chosen].mean()) if count else None
        conf = float(confidence[chosen].mean()) if count else None
        if count:
            ece += count / len(y) * abs(acc - conf)
        table.append({"lower": b / bins, "upper": (b + 1) / bins, "count": count, "accuracy": acc, "confidence": conf})
    risks = []
    for coverage in (0.25, 0.5, 0.75, 1.0):
        threshold = float(np.sort(confidence)[::-1][max(0, math.ceil(len(y) * coverage) - 1)])
        selected = confidence >= threshold
        risks.append({"requested_coverage": coverage, "actual_coverage": float(selected.mean()),
                      "threshold": threshold, "risk": float(1 - correct[selected].mean())})
    onehot = np.eye(p.shape[1])[y]
    return {"nll": float(-np.log(np.clip(p[np.arange(len(y)), y], 1e-12, 1)).mean()),
            "brier_multiclass_sum": float(((p - onehot) ** 2).sum(1).mean()),
            "ece": ece, "bins": table, "risk_coverage": risks,
            "note": "Uncalibrated probabilities; descriptive curves, not deployment thresholds. Ties kept together."}


def paired_ranking_interval(rows, predictions_a, predictions_b, metric="MRR", repetitions=2000, seed=42):
    if repetitions < 100:
        raise ValueError("use at least 100 bootstrap repetitions")
    def index(items):
        result = {r["id"]: r for r in items}
        if len(result) != len(items):
            raise ValueError("duplicate sample ids")
        return result
    truth, left, right = index(rows), index(predictions_a), index(predictions_b)
    if not truth or set(truth) != set(left) or set(truth) != set(right):
        raise ValueError("paired predictions must contain exactly the reference sample ids")
    totals = defaultdict(lambda: [0.0, 0.0, 0])
    for identifier, row in truth.items():
        relevance = {c["id"]: c["relevance"] for c in row["candidates"]}
        a = ranking_metrics([c["id"] for c in left[identifier]["ranked"]], relevance)[metric]
        b = ranking_metrics([c["id"] for c in right[identifier]["ranked"]], relevance)[metric]
        group = (row["dataset"], row["group_id"])
        totals[group][0] += a
        totals[group][1] += b
        totals[group][2] += 1
    if len(totals) < 2:
        raise ValueError("need at least two independent dialogue groups")
    values = np.asarray([totals[g] for g in sorted(totals)])
    rng, deltas = np.random.RandomState(seed), []
    for _ in range(repetitions):
        sample = values[rng.randint(0, len(values), len(values))].sum(0)
        deltas.append((sample[1] - sample[0]) / sample[2])
    summed = values.sum(0)
    return {"metric": metric, "a": float(summed[0] / summed[2]), "b": float(summed[1] / summed[2]),
            "difference_b_minus_a": float((summed[1] - summed[0]) / summed[2]),
            "paired_cluster_percentile_ci95": np.percentile(deltas, [2.5, 97.5]).tolist(),
            "groups": len(values), "samples": len(rows), "repetitions": repetitions, "seed": seed,
            "estimand": "turn-weighted metric; resample whole dialogues jointly",
            "limits": "Conditional on these fitted models; does not include training-seed variance or correct multiple comparisons."}

"""Metrics with explicit relevance and missing-label semantics."""
import math

from .data import require


def ranking_metrics(ranked_ids: list[str], relevance: dict[str, int], ks=(1, 3)) -> dict:
    require(len(ranked_ids) == len(set(ranked_ids)), "duplicate ranked id")
    require(set(ranked_ids) == set(relevance), "ranked and labeled candidate sets differ")
    relevant_count = sum(value > 0 for value in relevance.values())
    require(relevant_count > 0, "no relevant candidates")
    values = [relevance[cid] for cid in ranked_ids]
    output = {"MRR": 1.0 / next(i for i, value in enumerate(values, 1) if value > 0)}
    for k in ks:
        require(k > 0, "k must be positive")
        output[f"Recall@{k}"] = sum(value > 0 for value in values[:k]) / relevant_count
        dcg = sum((2 ** value - 1) / math.log2(i + 2) for i, value in enumerate(values[:k]))
        ideal = sorted(relevance.values(), reverse=True)[:k]
        idcg = sum((2 ** value - 1) / math.log2(i + 2) for i, value in enumerate(ideal))
        output[f"nDCG@{k}"] = dcg / idcg
    return output


def classification_metrics(gold: list[str], predictions: list[str], labels: list[str]) -> dict:
    require(len(gold) == len(predictions) and bool(gold), "empty or mismatched labels")
    require(set(gold) <= set(labels), "unknown gold class")
    per_class = {}
    for label in labels:
        tp = sum(g == label and p == label for g, p in zip(gold, predictions))
        fp = sum(g != label and p == label for g, p in zip(gold, predictions))
        fn = sum(g == label and p != label for g, p in zip(gold, predictions))
        per_class[label] = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0
    return {"count": len(gold), "accuracy": sum(g == p for g, p in zip(gold, predictions)) / len(gold),
            "macro_f1": sum(per_class.values()) / len(labels), "per_class_f1": per_class}


def average_metrics(rows: list[dict]) -> dict:
    require(bool(rows), "cannot average empty metrics")
    return {key: sum(row[key] for row in rows) / len(rows) for key in rows[0]}

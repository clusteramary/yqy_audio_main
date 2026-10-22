"""V2 offline candidate benchmark: donor questions from the SAME partition.

Held-out pools are only used to construct fixed evaluation candidate sets. They
must never be fitted on, sent to the trainer as examples, or used as retrieved K.
"""
import random
from collections import defaultdict
from pathlib import Path

from .data import read_jsonl, write_jsonl, validate_ranking, validate_splits
from .followup_data import TextIndex, digest, norm, sample_stream, file_sha, save_json


def build_candidates(row, pool, owners, index, hard_negatives=4):
    if not 0 <= hard_negatives <= 9:
        raise ValueError("hard_negatives must be 0..9")
    positive = [c["text"] for c in row["candidates"] if c["relevance"] > 0]
    if len(positive) != 1:
        raise ValueError("KdConv v2 expects exactly one observed next question")
    positive = positive[0]
    banned = {norm(positive)} | {norm(h) for h in row["history"]}
    selected = {}
    def add(text, kind):
        canonical = norm(text)
        if canonical in banned or row["group_id"] in owners[canonical]:
            return
        banned.add(canonical)
        selected[text] = kind
    if hard_negatives:
        for i in index.top("\n".join(row["history"][-4:]), 100):
            add(pool[i], "same-split-other-dialogue-lexical")
            if len(selected) == hard_negatives:
                break
    rng = random.Random(int(digest("v2:" + row["id"])[:16], 16))
    for text in sample_stream(pool, rng):
        add(text, "same-split-other-dialogue-random")
        if len(selected) == 9:
            break
    if len(selected) != 9:
        raise ValueError("insufficient distinct other-dialogue negatives: " + row["id"])
    result = [{"id": digest(positive)[:16], "text": positive, "relevance": 1, "source": "observed-next-question"}]
    result += [{"id": digest(t)[:16], "text": t, "relevance": 0, "source": kind} for t, kind in selected.items()]
    return sorted(result, key=lambda c: c["id"])


def prepare_v2(source, destination, hard_negatives=4):
    source, destination = Path(source), Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("choose a new output directory; preserve old protocol")
    if not 0 <= hard_negatives <= 9:
        raise ValueError("hard_negatives must be 0..9")
    report = {"protocol": "followup-candidates-v2", "hard_negatives": hard_negatives,
        "negative_pool": "same split and domain; exclude entire source dialogue",
        "task": "offline fixed-candidate reranking, NOT open retrieval",
        "heldout_pool_usage": "candidate construction only; never training or knowledge retrieval",
        "label_quality": "weak observed-next versus sampled; human audit still required", "splits": {}}
    for split in ("train", "dev", "test"):
        path = source / "kdconv" / f"{split}.jsonl"
        rows = read_jsonl(path)
        if {r["split"] for r in rows} != {split}:
            raise ValueError("unexpected input split")
        pools, owners = defaultdict(set), defaultdict(lambda: defaultdict(set))
        for row in rows:
            for c in row["candidates"]:
                if c["relevance"] > 0:
                    pools[row["domain"]].add(c["text"])
                    owners[row["domain"]][norm(c["text"])].add(row["group_id"])
        pools = {d: sorted(values) for d, values in pools.items()}
        indices = {d: TextIndex(values) for d, values in pools.items()}
        for i, row in enumerate(rows):
            row["candidates"] = build_candidates(row, pools[row["domain"]], owners[row["domain"]], indices[row["domain"]], hard_negatives)
            row["dataset"] = f"KdConv-followup-v2-hard{hard_negatives}"
            row["candidate_protocol"] = "same-split-other-dialogue-v2"
            validate_ranking(row)
            if i % 5000 == 0:
                print(f"{split}: {i}/{len(rows)}", flush=True)
        dest = destination / "kdconv" / f"{split}.jsonl"
        write_jsonl(dest, rows)
        report["splits"][split] = {"rows": len(rows), "source_sha256": file_sha(path), "sha256": file_sha(dest)}
        fq = source / "fq-bank" / f"{split}.jsonl"
        write_jsonl(destination / "fq-bank" / f"{split}.jsonl", read_jsonl(fq))
        if split == "dev":
            sample = random.Random(42).sample(rows, min(100, len(rows)))
            write_jsonl(destination / "kdconv" / "human_audit.jsonl", [
                {"id": r["id"], "history": r["history"], "knowledge": r["knowledge"],
                 "candidates": [{"id": c["id"], "text": c["text"], "human_relevance": None, "repeat": None, "answerable": None} for c in r["candidates"]]}
                for r in sample])
    validate_splits([str(destination / "kdconv" / f"{s}.jsonl") for s in ("train", "dev", "test")])
    save_json(destination / "report.json", report)
    return report

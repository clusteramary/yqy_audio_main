"""Reproducible follow-up ranking data. No target annotations enter retrieval."""
from __future__ import annotations

import hashlib
import json
import random
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

import numpy as np

from .data import read_jsonl, validate_ranking, validate_splits, write_jsonl
from .datasets import fqbank_record
from .ranking import tokenize

SPLITS = ("train", "dev", "test")
SOURCES = {
    "fq-bank": ("amazon-science/fq-bank", "d164b804d29022ec1e4b79faf4603d5758c8162b"),
    "KdConv": ("thu-coai/KdConv", "653db76432de09a004ba708a68f8bbd5500e6bec"),
}


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def norm(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).casefold())


def save_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


class TextIndex:
    """Token overlap inverted index; deterministic retrieval, no fitted statistics."""
    def __init__(self, texts):
        self.texts = texts
        self.tokens = [set(tokenize(t)) for t in texts]
        self.ties = [digest(t) for t in texts]
        self.postings = defaultdict(list)
        for i, tokens in enumerate(self.tokens):
            for token in tokens:
                self.postings[token].append(i)
        self.postings = {t: np.asarray(ids, dtype=np.int32) for t, ids in self.postings.items()}
        self.lengths = np.asarray([len(t) for t in self.tokens], dtype=np.int32)
        self.tie_order = np.argsort(np.asarray(self.ties))

    def top(self, query, k):
        tokens = set(tokenize(query))
        counts = np.zeros(len(self.texts), dtype=np.int32)
        for token in tokens:
            ids = self.postings.get(token)
            if ids is not None:
                counts[ids] += 1
        ids = self.tie_order[counts[self.tie_order] > 0]
        scores = counts[ids] / np.maximum(1, len(tokens) + self.lengths[ids] - counts[ids])
        return ids[np.argsort(-scores, kind="stable")[:k]].tolist()


class KnowledgeIndex:
    def __init__(self, kb):
        triples = {tuple(map(str, t)) for values in kb.values() for t in values if len(t) == 3}
        self.by_entity = defaultdict(list)
        self.entity_prefix = defaultdict(set)
        for h, r, t in sorted(triples):
            self.by_entity[h].append(f"{h} | {r} | {t}")
            if len(h) >= 2:
                self.entity_prefix[h[:2]].add(h)

    def retrieve(self, history, k=8):
        # No conversation name, candidate question or attrs is accepted by this API.
        text = "\n".join(history)
        entities = {e for i in range(len(text) - 1) for e in self.entity_prefix.get(text[i:i+2], ()) if e in text}
        facts = sorted({t for e in entities for t in self.by_entity[e]})
        if not facts:
            return []
        index = TextIndex(facts)
        return [facts[i] for i in index.top("\n".join(history[-4:]), k)]


def is_question(text):
    return bool(re.search(r"[?？]|什么|怎么|为何|为什么|哪[里个些年种]|多少|几[个岁年部]|是否|吗[。！!\s]*$|呢[。！!\s]*$", text))


def sample_stream(items, rng):
    """Lazy Fisher-Yates: same prefix as random.sample(items, len(items))."""
    swaps = {}
    for remaining in range(len(items), 0, -1):
        j = rng.randrange(remaining)
        index = swaps.get(j, j)
        swaps[j] = swaps.get(remaining - 1, remaining - 1)
        swaps.pop(remaining - 1, None)
        yield items[index]


def clean_splits(partitions):
    """Quarantine whole held-out groups sharing an opening/context with earlier splits."""
    owners, report, result = {}, [], {}
    for split in SPLITS:
        groups = defaultdict(list)
        for row in partitions[split]:
            groups[row["group_id"]].append(row)
        kept = []
        for group, rows in groups.items():
            keys = {digest(norm("\n".join(r["history"]))) for r in rows}
            keys |= {digest(norm("\n".join(r["history"][:2]))) for r in rows}
            shared = {owners[k] for k in keys if k in owners and owners[k] != split}
            if shared:
                report.append({"split": split, "group_id": group, "rows": len(rows), "reason": "shared opening/context", "earlier_splits": sorted(shared)})
                continue
            for key in keys:
                owners[key] = split
            kept.extend(rows)
        result[split] = kept
    return result, report


def prepare(raw, output):
    raw, output = Path(raw), Path(output)
    manifest_path = raw / "manifest.json"
    if not manifest_path.exists():
        raise ValueError("download manifest missing; run prepare_followup.py --download first")
    for relative, entry in load_json(manifest_path)["files"].items():
        if file_sha(raw / relative) != entry["sha256"]:
            raise ValueError("raw file changed: " + relative)
    reports = {}
    fq, conflicts = {}, []
    for split in SPLITS:
        fq[split] = []
        for item in load_json(raw / "fq-bank" / "data" / f"{split}.json"):
            try:
                row, _ = fqbank_record(item, split)
                # Official numeric dialogue ids restart at zero in every split.
                row["group_id"] = f"{split}:{row['group_id']}"
                labels = defaultdict(set)
                for c in row["candidates"]:
                    labels[norm(c["text"])].add(c["relevance"])
                if any(len(x) > 1 for x in labels.values()):
                    raise ValueError("conflicting FQ relevance after normalization")
                row["label_source"] = "official-candidate-labels"
                row["negative_reasons"] = item["candidate_utterances"]["invalid"]
                fq[split].append(row)
            except ValueError as exc:
                if "conflicting FQ relevance" not in str(exc):
                    raise
                conflicts.append({"split": split, "id": item["id"], "reason": str(exc)})
    fq, leakage = clean_splits(fq)
    reports["fq-bank"] = {"label_conflicts": conflicts, "quarantined_groups": leakage}

    kd = {s: [] for s in SPLITS}
    kb = {}
    for domain in ("film", "music", "travel"):
        base = raw / "KdConv" / "data" / domain
        kb[domain] = KnowledgeIndex(load_json(base / f"kb_{domain}.json"))
        for split in SPLITS:
            for i, dialogue in enumerate(load_json(base / f"{split}.json")):
                messages = [m["message"].strip() for m in dialogue["messages"]]
                for turn in range(2, len(messages)):
                    if not is_question(messages[turn]) or not all(messages[:turn]):
                        continue
                    kd[split].append({"schema_version": 1, "dataset": "KdConv-followup-v1", "split": split,
                        "id": f"{domain}:{split}:{i}:{turn}", "group_id": f"{domain}:{split}:{i}",
                        "domain": domain, "history": messages[:turn], "target": messages[turn]})
    kd, leakage = clean_splits(kd)
    pools = {d: sorted({r["target"] for r in kd["train"] if r["domain"] == d}) for d in kb}
    indices = {d: TextIndex(texts) for d, texts in pools.items()}
    for split in SPLITS:
        for row_index, row in enumerate(kd[split]):
            if row_index % 1000 == 0:
                print(f"KdConv {split}: {row_index}/{len(kd[split])}", flush=True)
            positive = row.pop("target")
            pool = pools[row["domain"]]
            index = indices[row["domain"]]
            banned = {norm(positive)} | {norm(h) for h in row["history"]}
            negatives = {}
            for i in index.top("\n".join(row["history"][-4:]), 40):
                if norm(pool[i]) not in banned:
                    negatives[pool[i]] = "train-pool-lexical-hard"
                    banned.add(norm(pool[i]))
                if len(negatives) == 4:
                    break
            rng = random.Random(int(digest(row["id"])[:16], 16))
            for text in sample_stream(pool, rng):
                if norm(text) not in banned:
                    negatives[text] = "train-pool-random"
                    banned.add(norm(text))
                if len(negatives) >= 9:
                    break
            candidates = [{"id": digest(positive)[:16], "text": positive, "relevance": 1, "source": "observed-next-question"}]
            candidates += [{"id": digest(t)[:16], "text": t, "relevance": 0, "source": reason} for t, reason in negatives.items()]
            row["candidates"] = sorted(candidates, key=lambda c: c["id"])
            row["knowledge"] = kb[row["domain"]].retrieve(row["history"])
            row["knowledge_source"] = f"public-kb:{row['domain']};history-entity-only;top8-overlap-v1"
            row["label_source"] = "observed-positive+sampled-weak-negatives;not-human-relevance"
    reports["kdconv"] = {"quarantined_groups": leakage, "negative_pool": "cleaned train only", "candidate_count": 10}
    for name, partitions in (("fq-bank", fq), ("kdconv", kd)):
        stats = {}
        for split, rows in partitions.items():
            for row in rows:
                validate_ranking(row)
            dest = output / name / f"{split}.jsonl"
            write_jsonl(dest, rows)
            stats[split] = {"rows": len(rows), "groups": len({r['group_id'] for r in rows}),
                "with_knowledge": sum(bool(r["knowledge"]) for r in rows),
                "candidates": sum(len(r["candidates"]) for r in rows), "sha256": file_sha(dest)}
        validate_splits([str(output / name / f"{s}.jsonl") for s in SPLITS])
        reports[name]["splits"] = stats
        # Blinded audit sheet; annotator should not see weak labels or target location.
        sample = random.Random(42).sample(partitions["dev"], min(100, len(partitions["dev"])))
        write_jsonl(output / name / "human_audit.jsonl", [
            {"id": r["id"], "history": r["history"], "knowledge": r["knowledge"],
             "candidates": [{"id": c["id"], "text": c["text"], "human_relevance": None, "repeat": None, "answerable": None} for c in r["candidates"]]}
            for r in sample])
    reports["protocol"] = "followup-v1; official partitions + whole-group cross-split prefix quarantine"
    reports["sources"] = SOURCES
    reports["raw_manifest_sha256"] = file_sha(manifest_path)
    save_json(output / "report.json", reports)
    return {name: reports[name]["splits"] for name in ("fq-bank", "kdconv")}

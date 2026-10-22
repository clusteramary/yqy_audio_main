"""Frozen text encoder + batched follow-up ranking; training is explicit only."""
from __future__ import annotations

import copy
import json
import math
import random
from pathlib import Path
from collections import defaultdict

import numpy as np
import torch
from torch import nn

from .data import read_jsonl, validate_ranking, validate_splits
from .followup_data import digest, file_sha, load_json, save_json
from .metrics import average_metrics, ranking_metrics
from .models.ranker import KnowledgeRanker, listwise_loss
from .ranking import LexicalRanker, tokenize

PROTOCOL_VERSION = 2


def scalar_features(row):
    """Observable lexical proxies, never gold labels or negative reasons."""
    h = [set(tokenize(t)) for t in row["history"][-8:]]
    k = [set(tokenize(t)) for t in row["knowledge"]]
    union = set().union(*h)
    features = []
    for c in row["candidates"]:
        q = set(tokenize(c["text"]))
        overlap = lambda t: len(q & t) / max(1, len(q | t))
        features.append([max(map(overlap, h), default=0), len(q & union) / max(1, len(q)),
                         max(map(overlap, k), default=0), float(bool(k))])
    return features


def model_fingerprint(path):
    path = Path(path)
    files = {p.name: file_sha(p) for p in path.iterdir()
             if p.is_file() and (p.suffix in {".safetensors", ".bin"} or p.name in {"config.json", "tokenizer.json", "tokenizer_config.json", "vocab.txt"})}
    if not any(n.endswith((".bin", ".safetensors")) for n in files):
        raise ValueError("local encoder weights missing")
    return digest(json.dumps(files, sort_keys=True))


def build_cache(paths, encoder_path, destination, device="cpu", batch_size=16, max_length=128):
    from transformers import AutoModel, AutoTokenizer
    dest = Path(destination)
    if (dest / "manifest.json").exists():
        raise ValueError("cache already finalized; choose a new directory or reuse it")
    dest.mkdir(parents=True, exist_ok=True)
    rows = [r for p in paths for r in read_jsonl(p)]
    if len({Path(p).name for p in paths}) != len(paths):
        raise ValueError("cache inputs must have distinct filenames")
    for row in rows:
        validate_ranking(row, labeled=False)
    texts = sorted({t for r in rows for t in r["history"][-8:] + r["knowledge"] + [c["text"] for c in r["candidates"]]})
    encoder = AutoModel.from_pretrained(encoder_path, local_files_only=True).to(device).eval()
    tokenizer = AutoTokenizer.from_pretrained(encoder_path, local_files_only=True)
    vectors = np.lib.format.open_memmap(dest / "vectors.npy", mode="w+", dtype=np.float32,
                                       shape=(len(texts), encoder.config.hidden_size))
    with torch.no_grad():
        for start in range(0, len(texts), batch_size):
            inputs = tokenizer(texts[start:start + batch_size], padding=True, truncation=True,
                               max_length=max_length, return_tensors="pt").to(device)
            hidden = encoder(**inputs).last_hidden_state
            mask = inputs["attention_mask"].unsqueeze(-1)
            pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1)
            vectors[start:start + len(pooled)] = pooled.cpu().numpy()
            if start % (batch_size * 100) == 0:
                print(f"encoded {start}/{len(texts)}", flush=True)
    vectors.flush()
    save_json(dest / "texts.json", texts)
    save_json(dest / "manifest.json", {"format": 1, "frozen_encoder": True, "pooling": "attention-mask-mean-including-special-tokens",
        "encoder": str(encoder_path), "encoder_sha256": model_fingerprint(encoder_path),
        "max_length": max_length, "history_turns": 8, "dimension": encoder.config.hidden_size,
        "inputs": {Path(p).name: file_sha(p) for p in paths},
        "texts_sha256": file_sha(dest / "texts.json"), "vectors_sha256": file_sha(dest / "vectors.npy"), "count": len(texts)})


class Cache:
    def __init__(self, path):
        path = Path(path)
        self.meta = load_json(path / "manifest.json")
        for name, key in (("texts.json", "texts_sha256"), ("vectors.npy", "vectors_sha256")):
            if file_sha(path / name) != self.meta[key]:
                raise ValueError("cache checksum mismatch: " + name)
        self.index = {t: i for i, t in enumerate(load_json(path / "texts.json"))}
        self.vectors = np.load(path / "vectors.npy", mmap_mode="r", allow_pickle=False)
        self.dim = self.vectors.shape[1]

    def check_input(self, path):
        if self.meta["inputs"].get(Path(path).name) != file_sha(path):
            raise ValueError("data changed after feature extraction: " + str(path))

    def get(self, texts):
        return torch.from_numpy(np.array(self.vectors[[self.index[t] for t in texts]], copy=True))

    def close(self):
        """Release the mmap explicitly before removing a temporary cache on Windows."""
        self.vectors._mmap.close()


def collate(rows, cache, device, knowledge_dropout=0.0, rng=None):
    if not 0 <= knowledge_dropout <= 1:
        raise ValueError("knowledge dropout must be in [0,1]")
    rng = rng if rng is not None else random
    b, d = len(rows), cache.dim
    max_h = max(min(8, len(r["history"])) for r in rows)
    max_k = max(1, max(len(r["knowledge"]) for r in rows))
    max_c = max(len(r["candidates"]) for r in rows)
    h, k, q = torch.zeros(b, max_h, d), torch.zeros(b, max_k, d), torch.zeros(b, max_c, 1, d)
    hm, km, qm = torch.zeros(b, max_h, dtype=torch.bool), torch.zeros(b, max_k, dtype=torch.bool), torch.zeros(b, max_c, 1, dtype=torch.bool)
    features, labels = torch.zeros(b, max_c, 4), torch.full((b, max_c), -1, dtype=torch.long)
    for i, original in enumerate(rows):
        r = dict(original)
        if knowledge_dropout > 0 and rng.random() < knowledge_dropout:
            r["knowledge"] = []
        history, knowledge, candidates = r["history"][-8:], r["knowledge"], r["candidates"]
        h[i, :len(history)] = cache.get(history)
        hm[i, :len(history)] = True
        if knowledge:
            k[i, :len(knowledge)] = cache.get(knowledge)
            km[i, :len(knowledge)] = True
        q[i, :len(candidates), 0] = cache.get([c["text"] for c in candidates])
        qm[i, :len(candidates)] = True
        features[i, :len(candidates)] = torch.tensor(scalar_features(r))
        labels[i, :len(candidates)] = torch.tensor([c.get("relevance", -1) for c in candidates])
    return [x.to(device) for x in (h, hm, k, km, q, qm, features)], labels.to(device)


class FollowupRanker(nn.Module):
    def __init__(self, dimension, hidden=128, heads=4, variant="coverage", history_position=False):
        super().__init__()
        if variant not in {"history", "fixed", "gated", "coverage"}:
            raise ValueError("unknown variant")
        self.variant = variant
        self.core = KnowledgeRanker(dimension, hidden, heads,
            use_knowledge=variant != "history", use_gate=variant != "fixed", history_position=history_position)
        self.coverage = nn.Sequential(nn.Linear(4, 16), nn.Tanh(), nn.Linear(16, 1))

    def forward(self, h, hm, k, km, q, qm, features):
        scores = self.core(h, hm, k, km, q, qm)
        if self.variant == "coverage":
            scores = scores + self.coverage(features).squeeze(-1)
        return scores


def perturb(rows, mode, seed=42):
    if mode == "original":
        return rows
    result = copy.deepcopy(rows)
    if mode == "missing":
        for r in result:
            r["knowledge"] = []
    elif mode == "shuffled":
        groups = defaultdict(list)
        for i, r in enumerate(rows):
            groups[r["group_id"]].append(i)
        if len(groups) < 2:
            raise ValueError("shuffled knowledge requires at least two dialogue groups")
        order = sorted(groups)
        random.Random(seed).shuffle(order)
        for i, group in enumerate(order):
            donors = sorted(groups[order[(i + 1) % len(order)]], key=lambda j: rows[j]["id"])
            for current in groups[group]:
                donor = donors[int(digest(f"{seed}:{rows[current]['id']}")[:16], 16) % len(donors)]
                result[current]["knowledge"] = list(rows[donor]["knowledge"])
                result[current]["knowledge_donor_id"] = rows[donor]["id"]
                result[current]["knowledge_donor_group"] = rows[donor]["group_id"]
    else:
        raise ValueError("unknown knowledge condition")
    return result


def evaluate(model, rows, cache, device, batch_size, labeled=True):
    model.eval()
    metrics, predictions = [], []
    with torch.no_grad():
        for start in range(0, len(rows), batch_size):
            batch = rows[start:start + batch_size]
            args, _ = collate(batch, cache, device)
            scores = model(*args).cpu().tolist()
            for r, values in zip(batch, scores):
                ordered = sorted(zip(r["candidates"], values), key=lambda pair: (-pair[1], pair[0]["id"]))
                if labeled:
                    metrics.append(ranking_metrics([c["id"] for c, _ in ordered], {c["id"]: c["relevance"] for c in r["candidates"]}))
                predictions.append({"id": r["id"], "group_id": r.get("group_id"),
                    "knowledge_donor_id": r.get("knowledge_donor_id"),
                    "ranked": [{"id": c["id"], "text": c["text"], "score": s} for c, s in ordered]})
    return {"count": len(rows), "metrics": average_metrics(metrics) if metrics else None}, predictions


def train(config_path, output, device="cuda", seed=42, variant=None, no_history_position=False):
    config = load_json(config_path)
    if variant:
        config["model"]["variant"] = variant
    if no_history_position:
        config["model"]["history_position"] = False
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("output must be empty; do not overwrite an existing experiment")
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    train_path, dev_path = Path(config["data"]) / "train.jsonl", Path(config["data"]) / "dev.jsonl"
    validate_splits([str(train_path), str(dev_path)])
    train_rows, dev_rows = read_jsonl(train_path), read_jsonl(dev_path)
    if len({r["dataset"] for r in train_rows + dev_rows}) != 1:
        raise ValueError("train/dev must use the same dataset and candidate protocol")
    if any(r["dataset"].startswith("KdConv") and r.get("candidate_protocol") != "same-split-other-dialogue-v2"
           for r in train_rows + dev_rows):
        raise ValueError("KdConv v1 has a confirmed candidate-membership shortcut; prepare v2 first")
    if {r["split"] for r in train_rows} != {"train"} or {r["split"] for r in dev_rows} != {"dev"}:
        raise ValueError("train/dev labels required; test must never select model")
    cache = Cache(config["cache"])
    cache.check_input(train_path)
    cache.check_input(dev_path)
    model = FollowupRanker(cache.dim, **config["model"]).to(device)
    settings = config["training"]
    if any(type(settings[k]) is not int or settings[k] < 1 for k in ("epochs", "batch_size", "patience")):
        raise ValueError("epochs, batch_size and patience must be positive integers")
    if not math.isfinite(settings["learning_rate"]) or settings["learning_rate"] <= 0:
        raise ValueError("learning_rate must be positive and finite")
    train_rng = random.Random(seed)
    optimizer = torch.optim.AdamW(model.parameters(), lr=settings["learning_rate"], weight_decay=0.01)
    best, bad, history = -1, 0, []
    for epoch in range(settings["epochs"]):
        model.train()
        order = list(range(len(train_rows)))
        train_rng.shuffle(order)
        total = 0.0
        for start in range(0, len(order), settings["batch_size"]):
            batch = [train_rows[i] for i in order[start:start + settings["batch_size"]]]
            args, labels = collate(batch, cache, device, settings["knowledge_dropout"], rng=train_rng)
            optimizer.zero_grad(set_to_none=True)
            loss = listwise_loss(model(*args), labels)
            if not torch.isfinite(loss):
                raise ValueError("non-finite training loss")
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total += loss.item() * len(batch)
        report, _ = evaluate(model, dev_rows, cache, device, settings["batch_size"])
        mrr = report["metrics"]["MRR"]
        history.append({"epoch": epoch + 1, "train_loss": total / len(train_rows), "dev": report})
        print(history[-1], flush=True)
        if mrr > best:
            best, bad = mrr, 0
            torch.save({"protocol_version": PROTOCOL_VERSION, "model": model.state_dict(), "config": config, "dimension": cache.dim,
                "cache_meta": cache.meta, "seed": seed, "epoch": epoch + 1,
                "data_sha256": {"train": file_sha(train_path), "dev": file_sha(dev_path)}}, output / "best.pt")
        else:
            bad += 1
        save_json(output / "report.json", {"config": config, "seed": seed, "history": history,
            "protocol_version": PROTOCOL_VERSION,
            "implementation_sha256": {p: file_sha(Path(__file__).resolve().parents[1] / p) for p in (
                "interview_robot/followup_training.py", "interview_robot/models/ranker.py")},
            "best_dev_MRR": best, "torch": torch.__version__, "device": device,
            "protocol": "frozen sentence encoder; best dev MRR; no test access"})
        if bad >= settings["patience"]:
            break


def load_checkpoint(path, device):
    try:
        checkpoint = torch.load(path, map_location=device, weights_only=True)
    except TypeError:
        checkpoint = torch.load(path, map_location=device)
    if checkpoint.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("checkpoint protocol differs; v2 aligns history scope and versioned model inputs")
    model = FollowupRanker(checkpoint["dimension"], **checkpoint["config"]["model"]).to(device)
    model.load_state_dict(checkpoint["model"])
    return model, checkpoint


def lexical_evaluate(path):
    rows = read_jsonl(path)
    ranker = LexicalRanker()
    values = []
    for r in rows:
        validate_ranking(r)
        ranked = ranker.rank(r["history"], [], r["candidates"])
        values.append(ranking_metrics([c["id"] for c in ranked], {c["id"]: c["relevance"] for c in r["candidates"]}))
    return {"count": len(rows), "metrics": average_metrics(values), "data_sha256": file_sha(path), "model": "BM25-history-only;no-training"}


def random_metrics(relevance):
    """Exact metric expectations over uniform candidate permutations."""
    n, positives = len(relevance), sum(r > 0 for r in relevance)
    if positives == 0:
        raise ValueError("random baseline requires positives")
    survival, mrr = 1.0, 0.0
    for rank in range(1, n - positives + 2):
        probability = survival * positives / (n - rank + 1)
        mrr += probability / rank
        survival *= (n - positives - rank + 1) / (n - rank + 1)
    gains = sorted([2 ** r - 1 for r in relevance], reverse=True)
    metrics = {"MRR": mrr}
    for k in (1, 3):
        discounts = [1 / math.log2(i + 2) for i in range(min(k, n))]
        ideal = sum(g * d for g, d in zip(gains, discounts))
        metrics[f"Recall@{k}"] = min(k, n) / n
        metrics[f"nDCG@{k}"] = sum(gains) / n * sum(discounts) / ideal
    return metrics


def random_evaluate(path):
    rows = read_jsonl(path)
    for r in rows:
        validate_ranking(r)
    return {"count": len(rows), "metrics": average_metrics([random_metrics([c["relevance"] for c in r["candidates"]]) for r in rows]),
            "data_sha256": file_sha(path), "model": "uniform-random-exact-expectation;no-training"}

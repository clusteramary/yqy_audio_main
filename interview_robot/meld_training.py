"""Batched MELD training/evaluation from frozen features. Import does not train."""
from __future__ import annotations

import copy
import hashlib
import json
import random
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset

from .meld_data import COUNTS, EMOTIONS, SENTIMENTS, ROOT, dump, sha256
from .meld_features import get_config, read_rows
from .models.state_model import MultimodalStateModel
from .research_metrics import probability_report

MODALITIES = ("text", "audio", "visual")
SCENARIOS = ("clean", "missing_text", "missing_audio", "missing_visual", "random_missing_0.1", "random_missing_0.3", "random_missing_0.5", "all_missing")
VARIANTS = ("full", "text_only", "audio_only", "visual_only", "concat_mlp", "no_gate", "no_temporal", "no_dropout", "uniform_quality")
QUALITY_STRESS = ("quality_ones", "quality_inverted", "quality_rotated")


def perturb_quality(values, available, mode="native", quality_mode="heuristic"):
    """Evaluation-only metadata stress; never changes embeddings or availability.

    Rotation is within the current utterance, in text/audio/visual order. It is
    not a shuffled dataset or a physical sensor degradation simulation.
    """
    if mode not in ("native",) + QUALITY_STRESS:
        raise ValueError("unknown quality stress mode")
    if quality_mode not in ("heuristic", "uniform"):
        raise ValueError("unknown training quality mode")
    q = np.array(values, dtype="float32", copy=True)
    if mode == "quality_ones" or (mode == "native" and quality_mode == "uniform"):
        q[:] = 1
    elif mode == "quality_inverted":
        q = 1 - q
    elif mode == "quality_rotated":
        # Only rotate observed modalities: missing-slot zero is not a quality value.
        indices = np.flatnonzero(available)
        q[indices] = np.roll(q[indices], 1)
    return q * available


def resolved(path):
    path = Path(path)
    return path if path.is_absolute() else ROOT / path


def configure(path, variant="full", seed=None, device=None):
    config = copy.deepcopy(get_config(path))
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant: {variant}")
    config["variant"] = variant
    if config["class_weight"] != "inverse_sqrt_train_frequency" or config["selection_metric"] != "emotion.macro_f1":
        raise ValueError("this protocol fixes train-frequency weights and dev emotion Macro-F1 selection")
    if config["window"] < 1 or config["epochs"] < 1 or config["patience"] < 1 or config["batch_size"] < 1:
        raise ValueError("window, epochs, patience and batch size must be positive")
    if seed is not None:
        config["seed"] = seed
    if device is not None:
        config["device"] = device
    model = config["model"]
    if variant.endswith("_only"):
        name = variant[:-5]
        model["input_dims"] = {name: model["input_dims"][name]}
        model.update(use_gate=False, modality_dropout=0.0)
    elif variant == "concat_mlp":
        model.update(fusion="concat", use_temporal=False, use_gate=False, modality_dropout=0.0)
    elif variant == "no_gate":
        model["use_gate"] = False
    elif variant == "no_temporal":
        model["use_temporal"] = False
    elif variant == "no_dropout":
        model["modality_dropout"] = 0.0
    elif variant == "uniform_quality":
        config["quality_mode"] = "uniform"
    return config


def load_split(config, split):
    root = resolved(config["feature_dir"])
    extraction = get_config(root / f"{split}.extraction.json")
    if extraction["limited_debug_subset"] or extraction["count"] != COUNTS[split]:
        raise ValueError("formal runs require complete official feature indices, not debug subsets")
    rows = read_rows(root / f"{split}.jsonl")
    # A failed encoder must not quietly turn a full multimodal experiment into text-only.
    if extraction.get("samples_with_failed_modalities", 0) > max(1, int(COUNTS[split] * .01)):
        raise ValueError("more than 1% of utterances have failed modalities; inspect extraction report and repair before formal training")
    if len(rows) != COUNTS[split] or len({r["id"] for r in rows}) != len(rows):
        raise ValueError("invalid split count or duplicate ids")
    groups = set(get_config(resolved(config["protocol"]))["excluded_groups"])
    ids = {r["id"] for r in rows}
    by_id = {r["id"]: r for r in rows}
    positions = {r["id"]: i for i, r in enumerate(rows)}
    for row in rows:
        if row["split"] != split or not row["id"].startswith(f"meld:{split}:") or row["group_id"] != row["id"].rsplit(":", 1)[0]:
            raise ValueError("cross-partition sample")
        for task, count in (("emotion", 7), ("affect", 3)):
            if type(row["labels"][task]) is not int or not 0 <= row["labels"][task] < count:
                raise ValueError("invalid MELD labels")
        for past in row["past_ids"]:
            if past not in ids or by_id[past]["group_id"] != row["group_id"] or positions[past] >= positions[row["id"]]:
                raise ValueError("future or cross-dialogue history")
    return [row for row in rows if row["group_id"] not in groups]


def load_feature(root, row, dims):
    path = (Path(root) / row["feature"]).resolve()
    if Path(root).resolve() not in path.parents:
        raise ValueError("feature outside cache root")
    if "feature_sha256" in row and sha256(path) != row["feature_sha256"]:
        raise ValueError(f"feature checksum failed: {row['id']}")
    with np.load(path, allow_pickle=False) as f:
        values = {key: f[key].copy() for key in (*MODALITIES, "available", "confidence", "age")}
    for name in MODALITIES:
        if values[name].shape != (dims[name],) or not np.isfinite(values[name]).all():
            raise ValueError(f"invalid {name} features: {row['id']}")
    mask, confidence, age = (values[k] for k in ("available", "confidence", "age"))
    if mask.shape != (3,) or mask.dtype != np.bool_ or confidence.shape != (3,) or age.shape != (3,):
        raise ValueError("invalid feature metadata shape/dtype")
    if not np.isfinite(confidence).all() or not ((confidence >= 0) & (confidence <= 1)).all():
        raise ValueError("invalid quality proxy")
    if not np.isfinite(age).all() or (age < 0).any():
        raise ValueError("invalid feature age")
    return values


def fit_normalization(config, rows):
    """Fit only on retained TRAIN utterances once, not repeated history windows."""
    root = resolved(config["feature_dir"])
    dims = get_config(root / "provenance.json")["feature_dimensions"]
    sums = {m: np.zeros(dims[m], dtype=np.float64) for m in MODALITIES}
    squares = {m: np.zeros(dims[m], dtype=np.float64) for m in MODALITIES}
    counts = {m: 0 for m in MODALITIES}
    for row in rows:
        if row["split"] != "train":
            raise ValueError("normalization may only fit on training data")
        f = load_feature(root, row, dims)
        for i, name in enumerate(MODALITIES):
            if f["available"][i]:
                x = f[name].astype(np.float64)
                sums[name] += x
                squares[name] += x * x
                counts[name] += 1
    state = {}
    for name in MODALITIES:
        if counts[name] == 0:
            raise ValueError(f"no training features for modality {name}")
        mean = sums[name] / counts[name]
        std = np.sqrt(np.maximum(squares[name] / counts[name] - mean ** 2, 1e-6))
        state[name] = {"mean": mean.astype("float32"), "std": std.astype("float32"), "count": counts[name]}
    return state


def stable_missing(identifier, probability, seed=2026):
    digest = hashlib.sha256(f"{seed}:{identifier}".encode()).digest()
    rng = np.random.RandomState(int.from_bytes(digest[:4], "little"))
    return rng.rand(3) < probability


class MeldWindows(Dataset):
    def __init__(self, root, rows, normalization, window=8, names=MODALITIES, quality_mode="heuristic", scenario="clean", quality_stress="native"):
        if window < 1 or scenario not in SCENARIOS or quality_mode not in ("heuristic", "uniform"):
            raise ValueError("invalid window, scenario or quality mode")
        self.rows, self.by_id, self.root = rows, {r["id"]: r for r in rows}, Path(root)
        self.normalization, self.window = normalization, window
        self.names, self.indices = tuple(names), [MODALITIES.index(m) for m in names]
        self.quality_mode, self.scenario = quality_mode, scenario
        if quality_stress not in ("native",) + QUALITY_STRESS:
            raise ValueError("unknown quality stress mode")
        self.quality_stress = quality_stress
        self.cache = {}

    def __len__(self):
        return len(self.rows)

    def feature(self, row):
        if row["id"] not in self.cache:
            dims = {m: len(self.normalization[m]["mean"]) for m in MODALITIES}
            self.cache[row["id"]] = load_feature(self.root, row, dims)
        return self.cache[row["id"]]

    def __getitem__(self, index):
        row = self.rows[index]
        prior = row["past_ids"][-(self.window - 1):] if self.window > 1 else []
        sequence = [self.by_id[key] for key in prior] + [row]
        features = {m: [] for m in self.names}
        masks, qualities, ages = [], [], []
        for step in sequence:
            f = self.feature(step)
            mask = f["available"].copy()
            if self.scenario.startswith("missing_"):
                mask[MODALITIES.index(self.scenario[8:])] = False
            elif self.scenario.startswith("random_missing_"):
                mask &= ~stable_missing(step["id"], float(self.scenario.rsplit("_", 1)[1]))
            elif self.scenario == "all_missing":
                mask[:] = False
            for m in self.names:
                norm = self.normalization[m]
                x = (f[m] - norm["mean"]) / norm["std"]
                features[m].append(x if mask[MODALITIES.index(m)] else np.zeros_like(x))
            masks.append(mask[self.indices])
            q = perturb_quality(f["confidence"], mask, self.quality_stress, self.quality_mode)
            qualities.append(q[self.indices])
            ages.append(f["age"][self.indices])
        return {"id": row["id"], "features": {m: np.stack(v) for m, v in features.items()},
                "available": np.stack(masks), "confidence": np.stack(qualities), "age": np.stack(ages),
                "labels": row["labels"]}


def collate(rows):
    lengths = [len(r["available"]) for r in rows]
    maxlen = max(lengths)
    def pad(values):
        target = np.zeros((len(rows), maxlen) + values[0].shape[1:], dtype=values[0].dtype)
        for i, value in enumerate(values):
            target[i, :len(value)] = value
        return torch.from_numpy(target)
    return {"ids": [r["id"] for r in rows], "lengths": torch.tensor(lengths),
            "features": {m: pad([r["features"][m] for r in rows]) for m in rows[0]["features"]},
            **{key: pad([r[key] for r in rows]) for key in ("available", "confidence", "age")},
            "labels": {task: torch.tensor([r["labels"][task] for r in rows]) for task in ("emotion", "affect")}}


def forward_batch(model, batch, device):
    return model({m: x.to(device) for m, x in batch["features"].items()},
                 batch["available"].to(device), batch["confidence"].to(device), batch["age"].to(device),
                 lengths=batch["lengths"].to(device))


def classification_report(gold, predictions, labels):
    matrix = np.zeros((len(labels), len(labels)), dtype=np.int64)
    for g, p in zip(gold, predictions):
        matrix[g, p] += 1
    support = matrix.sum(axis=1)
    tp = matrix.diagonal()
    precision = tp / np.maximum(matrix.sum(axis=0), 1)
    recall = tp / np.maximum(support, 1)
    f1 = 2 * precision * recall / np.maximum(precision + recall, 1e-12)
    return {"count": int(matrix.sum()), "accuracy": float(tp.sum() / max(matrix.sum(), 1)),
            "macro_f1": float(f1.mean()), "weighted_f1": float((f1 * support).sum() / max(support.sum(), 1)),
            "confusion_matrix": matrix.tolist(), "label_order": labels,
            "per_class": {name: {"precision": float(precision[i]), "recall": float(recall[i]), "f1": float(f1[i]),
                                  "support": int(support[i])} for i, name in enumerate(labels)}}


def make_loader(config, rows, norm, shuffle=False, scenario="clean", quality_stress="native"):
    dataset = MeldWindows(resolved(config["feature_dir"]), rows, norm, config["window"],
                          tuple(config["model"]["input_dims"]), config["quality_mode"], scenario, quality_stress)
    generator = torch.Generator().manual_seed(config["seed"])
    return DataLoader(dataset, batch_size=config["batch_size"], shuffle=shuffle, collate_fn=collate,
                      num_workers=config["num_workers"], generator=generator)


def evaluate_loader(model, loader, device):
    model.eval()
    gold, guesses, records = {"emotion": [], "affect": []}, {"emotion": [], "affect": []}, []
    with torch.inference_mode():
        for batch in loader:
            output = forward_batch(model, batch, device)
            probs = {task: output[task].softmax(-1).cpu().numpy() for task in gold}
            for task in gold:
                if not np.isfinite(probs[task]).all():
                    raise ValueError("non-finite model probabilities")
                gold[task].extend(batch["labels"][task].tolist())
                guesses[task].extend(probs[task].argmax(-1).tolist())
            for i, identifier in enumerate(batch["ids"]):
                records.append({"id": identifier, "probabilities": {task: p[i].tolist() for task, p in probs.items()}})
    return {task: {**classification_report(gold[task], guesses[task], labels),
                   "probability_quality": probability_report(gold[task], [r["probabilities"][task] for r in records])}
            for task, labels in (("emotion", EMOTIONS), ("affect", SENTIMENTS))}, records


def preflight(config, forward=False):
    """Audit completeness and optionally do a random-weight forward pass; no optimizer."""
    root = resolved(config["feature_dir"])
    result = {"training_started": False, "cuda_available": torch.cuda.is_available(), "torch": torch.__version__,
              "config": config, "errors": [], "splits": {}}
    for split in COUNTS:
        try:
            rows = load_split(config, split)
            dims = get_config(root / "provenance.json")["feature_dimensions"]
            if any(dims[m] != d for m, d in config["model"]["input_dims"].items()):
                raise ValueError("model and cached feature dimensions do not match")
            missing = np.zeros(3, dtype=np.int64)
            for row in rows:
                f = load_feature(root, row, dims)
                missing += ~f["available"]
            result["splits"][split] = {"count": len(rows), "missing_modalities": dict(zip(MODALITIES, missing.tolist()))}
        except (OSError, ValueError, KeyError) as error:
            result["errors"].append(f"{split}: {error}")
    if not result["errors"] and forward:
        norm = fit_normalization(config, load_split(config, "train"))
        batch = next(iter(make_loader(config, load_split(config, "dev"), norm)))
        model = MultimodalStateModel(**config["model"]).eval()
        with torch.inference_mode():
            out = forward_batch(model, batch, "cpu")
        result["forward_shapes"] = {k: list(v.shape) for k, v in out.items()}
        if not all(torch.isfinite(v).all() for v in out.values()):
            result["errors"].append("non-finite forward pass")
    result["ready"] = not result["errors"]
    return result


def seed_all(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True


def train(config, output_dir):
    """Explicitly invoked only on the server. Validation chooses checkpoints; test stays unopened."""
    output_dir = Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("training output must be new or empty; existing runs are preserved")
    device = config["device"]
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise ValueError("CUDA requested but unavailable; install compatible torch/driver or explicitly use cpu")
    train_rows, dev_rows = load_split(config, "train"), load_split(config, "dev")
    norm = fit_normalization(config, train_rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    seed_all(config["seed"])
    model = MultimodalStateModel(**config["model"]).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config["learning_rate"], weight_decay=config["weight_decay"])
    losses = {}
    for task, n in (("emotion", 7), ("affect", 3)):
        counts = np.bincount([r["labels"][task] for r in train_rows], minlength=n)
        weights = 1 / np.sqrt(np.maximum(counts, 1))
        weights /= weights.mean()
        losses[task] = nn.CrossEntropyLoss(weight=torch.tensor(weights, dtype=torch.float32, device=device))
    train_loader, dev_loader = make_loader(config, train_rows, norm, True), make_loader(config, dev_rows, norm)
    best, stale, history = -1.0, 0, []
    root = resolved(config["feature_dir"])
    provenance = {"feature_provenance_sha256": sha256(root / "provenance.json"),
                  "protocol_sha256": sha256(resolved(config["protocol"])),
                  "train_index_sha256": sha256(root / "train.jsonl"), "dev_index_sha256": sha256(root / "dev.jsonl"),
                  "torch": str(torch.__version__), "numpy": np.__version__,
                  "train_count": len(train_rows), "dev_count": len(dev_rows),
                  "parameters": sum(p.numel() for p in model.parameters())}
    provenance["implementation_sha256"] = {name: sha256(ROOT / name) for name in (
        "interview_robot/meld_training.py", "interview_robot/meld_features.py",
        "interview_robot/meld_data.py", "interview_robot/models/state_model.py")}
    norm_lists = {m: {k: v.tolist() if isinstance(v, np.ndarray) else v for k, v in stat.items()} for m, stat in norm.items()}
    dump(output_dir / "normalization.json", norm_lists)
    dump(output_dir / "config.json", config)
    for epoch in range(1, config["epochs"] + 1):
        model.train()
        start, total, examples = time.monotonic(), 0.0, 0
        for batch in train_loader:
            optimizer.zero_grad(set_to_none=True)
            out = forward_batch(model, batch, device)
            loss = losses["emotion"](out["emotion"], batch["labels"]["emotion"].to(device))
            loss = loss + config["auxiliary_affect_weight"] * losses["affect"](out["affect"], batch["labels"]["affect"].to(device))
            if not torch.isfinite(loss):
                raise ValueError("non-finite training loss")
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), config["gradient_clip"], error_if_nonfinite=True)
            optimizer.step()
            total += float(loss.detach()) * len(batch["ids"])
            examples += len(batch["ids"])
        metrics, _ = evaluate_loader(model, dev_loader, device)
        row = {"epoch": epoch, "train_loss": total / examples, "dev": metrics, "seconds": time.monotonic() - start}
        history.append(row)
        score = metrics["emotion"]["macro_f1"]
        if score > best:
            best, stale = score, 0
            checkpoint = {"config": config, "model": {k: v.detach().cpu() for k, v in model.state_dict().items()},
                          "normalization": norm_lists, "epoch": epoch, "provenance": provenance}
            temp = output_dir / "best.pt.tmp"
            torch.save(checkpoint, temp)
            temp.replace(output_dir / "best.pt")
        else:
            stale += 1
        dump(output_dir / "report.json", {"config": config, "provenance": provenance, "history": history,
                                          "best_dev_emotion_macro_f1": best, "test_evaluated": False})
        print(f"epoch={epoch} loss={row['train_loss']:.4f} dev_emotion_macro_f1={score:.4f}", flush=True)
        if stale >= config["patience"]:
            break
    return {"output": str(output_dir), "best_dev_emotion_macro_f1": best, "epochs": len(history), "test_evaluated": False}


def evaluate_checkpoint(checkpoint_path, output_dir, split="test", device="cpu", feature_dir=None, quality_stress=False):
    try:
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    except TypeError:
        checkpoint = torch.load(checkpoint_path, map_location="cpu")
    config = copy.deepcopy(checkpoint["config"])
    if feature_dir is not None:
        config["feature_dir"] = str(feature_dir)
    root = resolved(config["feature_dir"])
    if sha256(root / "provenance.json") != checkpoint["provenance"]["feature_provenance_sha256"]:
        raise ValueError("checkpoint/features provenance mismatch")
    if sha256(resolved(config["protocol"])) != checkpoint["provenance"]["protocol_sha256"]:
        raise ValueError("checkpoint/data split protocol mismatch")
    output_dir = Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("evaluation output must be new or empty")
    model = MultimodalStateModel(**config["model"]).to(device)
    model.load_state_dict(checkpoint["model"])
    norm = {m: {k: np.asarray(v, dtype="float32") if k in ("mean", "std") else v for k, v in stat.items()}
            for m, stat in checkpoint["normalization"].items()}
    rows = load_split(config, split)
    report = {"checkpoint_sha256": sha256(checkpoint_path), "feature_provenance_sha256": sha256(root / "provenance.json"),
              "protocol_sha256": sha256(resolved(config["protocol"])),
              "evaluation_index_sha256": sha256(root / f"{split}.jsonl"), "split": split, "count": len(rows),
              "variant": config["variant"], "seed": config["seed"], "perturbation_seed": 2026, "scenarios": {}}
    report["quality_protocol"] = {
        "version": "metadata-stress-v1", "enabled": quality_stress,
        "training_quality_mode": config["quality_mode"],
        "stress_source": "cached heuristic quality; overrides training quality mode for stress runs",
        "rotation": "one-place cyclic rotation among observed modalities, in text/audio/visual order",
        "scope": "metadata only; clean embeddings; no physical corruption; no future or donor utterances",
        "implementation_sha256": sha256(ROOT / "interview_robot/meld_training.py"),
    }
    for scenario in SCENARIOS + (QUALITY_STRESS if quality_stress else ()):
        is_quality = scenario in QUALITY_STRESS
        metrics, records = evaluate_loader(model, make_loader(
            config, rows, norm, scenario="clean" if is_quality else scenario,
            quality_stress=scenario if is_quality else "native"), device)
        report["scenarios"][scenario] = metrics
        dump(output_dir / f"{scenario}.predictions.json", records)
    dump(output_dir / "evaluation.json", report)
    return report


def plan(config_path, output, seeds=(42, 2026, 3407)):
    output = Path(output)
    commands = []
    for variant in VARIANTS:
        for seed in seeds:
            run = f"outputs/meld/{variant}-seed{seed}"
            commands.append(f"python scripts/train_meld.py train --config {Path(config_path).as_posix()} --variant {variant} --seed {seed} --output {run}")
    result = {"runs": len(commands), "seeds": list(seeds), "variants": list(VARIANTS), "commands": commands,
              "evaluation": "After freezing experiments, use evaluate on each best.pt; train never opens the test features."}
    dump(output, result)
    return result

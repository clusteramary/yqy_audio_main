"""Small, inspectable feature-level training loop; no encoder download."""
from __future__ import annotations

import copy
import json
import math
import random
from pathlib import Path

from .data import read_jsonl, require
from .experiment import provenance, write_report


def validate_feature_splits(train, validation, task):
    seen_groups, seen_ids = set(), set()
    for split, rows in (("train", train), ("validation", validation)):
        require(bool(rows), f"{split} is empty")
        for row in rows:
            require(row.get("schema_version") == 1 and row.get("task") == task, "feature schema/task mismatch")
            require(row.get("split") in ({"train"} if split == "train" else {"dev", "validation"}),
                    "train/validation partitions must be explicitly identified")
            require(bool(row.get("dataset")) and bool(row.get("group_id")) and bool(row.get("id")),
                    "features require dataset/group_id/id")
            require(bool(row.get("feature_source")), "features require encoder/source provenance")
            key = (row["dataset"], row["id"])
            require(key not in seen_ids, "duplicate feature id")
            seen_ids.add(key)
            group = (row["dataset"], row["group_id"])
            if split == "train":
                seen_groups.add(group)
            else:
                require(group not in seen_groups, "train/validation group leakage")


def prepare(row, task, model_config, device):
    import torch
    tensor = lambda value, dtype=torch.float32: torch.tensor(value, dtype=dtype, device=device).unsqueeze(0)
    if task == "state":
        features = {name: tensor(row["features"][name]) for name in model_config["input_dims"]}
        inputs = (features, tensor(row["available"], torch.bool), tensor(row["confidence"]),
                  tensor(row["age"]))
        labels = {name: tensor([row.get("labels", {}).get(name, -100)], torch.long).reshape(1)
                  for name in ("turn", "affect", "speaker_change")}
        require(any(value.item() != -100 for value in labels.values()), "sample has no supervised state labels")
    else:
        width = model_config["feature_dim"]
        history = tensor(row["history_features"])
        knowledge = tensor(row["knowledge_features"]) if row["knowledge_features"] else torch.zeros(1, 0, width, device=device)
        questions = row["question_features"]
        require(len(questions) >= 2 and all(len(q) for q in questions), "need two nonempty question feature lists")
        max_length = max(map(len, questions))
        padded = torch.zeros(1, len(questions), max_length, width, device=device)
        qmask = torch.zeros(1, len(questions), max_length, dtype=torch.bool, device=device)
        for i, sequence in enumerate(questions):
            padded[0, i, :len(sequence)] = torch.tensor(sequence, device=device)
            qmask[0, i, :len(sequence)] = True
        inputs = (history, torch.ones(history.shape[:2], dtype=torch.bool, device=device),
                  knowledge, torch.ones(knowledge.shape[:2], dtype=torch.bool, device=device), padded, qmask)
        labels = tensor(row["relevance"])
    return inputs, labels


def train_features(train_path, validation_path, config_path, output_dir):
    import torch
    from .models.ranker import KnowledgeRanker, listwise_loss
    from .models.state_model import MultimodalStateModel, partial_state_loss

    config = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
    task = config["task"]
    require(task in {"state", "ranking"}, "unknown learning task")
    training = config["training"]
    seed = int(training["seed"])
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True)
    device = torch.device(training["device"])
    if device.type == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA unavailable; choose cpu or configure the server environment")
    train_rows, val_rows = read_jsonl(train_path), read_jsonl(validation_path)
    validate_feature_splits(train_rows, val_rows, task)
    model = (MultimodalStateModel if task == "state" else KnowledgeRanker)(**config["model"]).to(device)
    loss_fn = partial_state_loss if task == "state" else listwise_loss
    learning_rate = float(training["learning_rate"])
    epochs = int(training["epochs"])
    require(epochs > 0 and math.isfinite(learning_rate) and learning_rate > 0, "invalid training schedule")
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    output = Path(output_dir)
    require(not output.exists() or not any(output.iterdir()), "use a new empty run directory")
    output.mkdir(parents=True, exist_ok=True)
    metadata = provenance([train_path, validation_path, config_path], config)
    metadata["torch"] = torch.__version__
    metadata["purpose"] = "feature-level development training; scientific validity depends on source data"
    best, best_state, history = math.inf, None, []
    for epoch in range(epochs):
        model.train()
        order = list(range(len(train_rows)))
        random.shuffle(order)
        train_loss = 0.0
        for index in order:
            inputs, labels = prepare(train_rows[index], task, config["model"], device)
            optimizer.zero_grad()
            loss = loss_fn(model(*inputs), labels)
            if not torch.isfinite(loss):
                raise ValueError("non-finite training loss")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_loss += loss.item()
        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for row in val_rows:
                inputs, labels = prepare(row, task, config["model"], device)
                loss = loss_fn(model(*inputs), labels)
                if not torch.isfinite(loss):
                    raise ValueError("non-finite validation loss")
                val_loss += loss.item()
        val_loss /= len(val_rows)
        history.append({"epoch": epoch + 1, "train_loss": train_loss / len(train_rows), "validation_loss": val_loss})
        if val_loss < best:
            best = val_loss
            best_state = copy.deepcopy({key: value.detach().cpu() for key, value in model.state_dict().items()})
    torch.save({"model": best_state, "config": config}, output / "best.pt")
    report = {"metadata": metadata, "train_count": len(train_rows), "validation_count": len(val_rows),
              "best_validation_loss": best, "epochs": history}
    write_report(output / "report.json", report)
    return report


def evaluate_features(data_path, checkpoint_path, output_path, device_name="cpu"):
    """Checkpoint must be a trusted local output of train_features."""
    import torch
    from .metrics import average_metrics, classification_metrics, ranking_metrics
    from .models.ranker import KnowledgeRanker
    from .models.state_model import MultimodalStateModel

    device = torch.device(device_name)
    # weights_only exists in newer PyTorch; the 1.12 development environment lacks it.
    try:
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    except TypeError:
        checkpoint = torch.load(checkpoint_path, map_location="cpu")
    config = checkpoint["config"]
    task = config["task"]
    model = (MultimodalStateModel if task == "state" else KnowledgeRanker)(**config["model"]).to(device)
    model.load_state_dict(checkpoint["model"])
    model.eval()
    rows = read_jsonl(data_path)
    predictions, ranking_rows, gold, guessed = [], [], {}, {}
    with torch.no_grad():
        for row in rows:
            require(row.get("task") == task and row.get("schema_version") == 1, "feature task/schema mismatch")
            inputs, labels = prepare(row, task, config["model"], device)
            output = model(*inputs)
            if task == "state":
                values = {name: int(output[name].argmax(dim=-1).item()) for name in labels}
                for name, target in labels.items():
                    if target.item() != -100:
                        gold.setdefault(name, []).append(str(target.item()))
                        guessed.setdefault(name, []).append(str(values[name]))
                predictions.append({"id": row["id"], "predictions": values})
            else:
                scores = output[0].cpu().tolist()
                ids = row.get("candidate_ids", [str(i) for i in range(len(scores))])
                require(len(ids) == len(scores) and len(set(ids)) == len(ids), "invalid feature candidate ids")
                order = sorted(range(len(scores)), key=lambda i: (-scores[i], ids[i]))
                metrics = ranking_metrics([ids[i] for i in order], dict(zip(ids, row["relevance"])))
                ranking_rows.append(metrics)
                predictions.append({"id": row["id"], "ranked_ids": [ids[i] for i in order], "scores": scores})
    classes = {"turn": ["0", "1"], "affect": ["0", "1", "2"], "speaker_change": ["0", "1"]}
    metrics = (average_metrics(ranking_rows) if task == "ranking" else
               {name: classification_metrics(values, guessed[name], classes[name]) for name, values in gold.items()})
    report = {"metadata": provenance([data_path, checkpoint_path], config), "count": len(rows),
              "datasets": sorted({row["dataset"] for row in rows}), "metrics": metrics, "predictions": predictions}
    write_report(output_path, report)
    return {"count": len(rows), "metrics": metrics, "output": str(output_path)}

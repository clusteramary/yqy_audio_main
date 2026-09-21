"""Offline entry points. No live hardware is invoked by these commands."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .data import read_jsonl, validate_observation, validate_ranking, validate_splits, write_jsonl
from .experiment import provenance, write_report
from .metrics import average_metrics, classification_metrics, ranking_metrics
from .ranking import LexicalRanker, TOKENIZER
from .runtime import InterviewController
from .state import RuleStateEstimator

ROOT = Path(__file__).resolve().parents[1]


def load_config(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def evaluate_ranking(args):
    rows = read_jsonl(args.data)
    ranker = LexicalRanker(args.knowledge_weight)
    results, predictions = [], []
    for row in rows:
        validate_ranking(row)
        candidates = [{"id": item["id"], "text": item["text"]} for item in row["candidates"]]
        ranked = ranker.rank(row["history"], row["knowledge"], candidates)
        metrics = ranking_metrics([item["id"] for item in ranked],
                                  {item["id"]: item["relevance"] for item in row["candidates"]})
        results.append(metrics)
        predictions.append({"id": row["id"], "ranking": ranked, "metrics": metrics})
    report = {"metadata": provenance([args.data], {"model": "candidate-set-bm25",
              "tokenizer": TOKENIZER, "knowledge_weight": args.knowledge_weight}),
              "count": len(rows), "datasets": sorted({row["dataset"] for row in rows}),
              "metrics": average_metrics(results), "predictions": predictions}
    write_report(args.output, report)
    return {"count": len(rows), "metrics": report["metrics"], "output": str(args.output)}


def evaluate_state(args):
    config = load_config(args.config)
    estimator = RuleStateEstimator(**config["state"])
    rows = read_jsonl(args.data)
    predictions, gold, guessed = [], {}, {}
    for row in rows:
        state = estimator.predict(row)
        predictions.append(state.to_dict())
        for task in ("turn", "affect"):
            if row.get("labels", {}).get(task) is not None:
                gold.setdefault(task, []).append(row["labels"][task])
                guessed.setdefault(task, []).append(getattr(state, task))
    classes = {"turn": ["HOLD", "SHIFT", "IDLE", "ROBOT"],
               "affect": ["positive", "neutral", "negative"]}
    metrics = {task: classification_metrics(values, guessed[task], classes[task]) for task, values in gold.items()}
    report = {"metadata": provenance([args.data, args.config], config),
              "count": len(rows), "metrics": metrics, "predictions": predictions}
    write_report(args.output, report)
    return {"count": len(rows), "metrics": metrics, "output": str(args.output)}


def replay(args):
    config = load_config(args.config)
    estimator = RuleStateEstimator(**config["state"])
    scenarios = read_jsonl(args.questions)
    for row in scenarios:
        validate_ranking(row, labeled=False)
    by_id = {row["id"]: row for row in scenarios}
    if len(by_id) != len(scenarios):
        raise ValueError("duplicate scenario id")
    controllers, trace = {}, []
    for observation in read_jsonl(args.data):
        state = estimator.predict(observation)
        scenario = by_id[observation["ranking_id"]]
        controller = controllers.setdefault(state.session_id, InterviewController(
            LexicalRanker(**config["ranking"]), **config["controller"]))
        decision = controller.step(state, scenario["history"], scenario["knowledge"],
                                   [{"id": q["id"], "text": q["text"]} for q in scenario["candidates"]])
        trace.append({"state": state.to_dict(), "decision": decision.to_dict(),
                      "phase": controller.phase, "execution": "dry-run"})
    write_jsonl(args.output, trace)
    write_report(str(args.output) + ".meta.json",
                 provenance([args.data, args.questions, args.config], config))
    return {"frames": len(trace), "questions_selected": sum(row["decision"]["action"] == "ASK" for row in trace),
            "output": str(args.output), "execution": "dry-run"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("validate-ranking", help="validate schema and cross-split conversation separation")
    p.add_argument("paths", nargs="+")
    p = sub.add_parser("evaluate-ranking")
    p.add_argument("--data", default=ROOT / "data/examples/ranking.jsonl")
    p.add_argument("--knowledge-weight", type=float, default=0.0)
    p.add_argument("--output", default=ROOT / "outputs/ranking.json")
    for command in ("evaluate-state", "replay"):
        p = sub.add_parser(command)
        p.add_argument("--data", default=ROOT / "data/examples/observations.jsonl")
        p.add_argument("--config", default=ROOT / "configs/local.json")
        p.add_argument("--output", default=ROOT / ("outputs/trace.jsonl" if command == "replay" else "outputs/state.json"))
        if command == "replay":
            p.add_argument("--questions", default=ROOT / "data/examples/ranking.jsonl")
    p = sub.add_parser("train", help="train from cached features; optional torch dependency")
    for field in ("train", "validation", "config", "output"):
        p.add_argument("--" + field, required=True)
    p = sub.add_parser("evaluate-model", help="evaluate a locally trained checkpoint on cached features")
    for field in ("data", "checkpoint", "output"):
        p.add_argument("--" + field, required=True)
    p.add_argument("--device", default="cpu")
    p = sub.add_parser("convert-fqbank", help="convert the official FQ-Bank JSON to ranking JSONL")
    p.add_argument("--input", required=True)
    p.add_argument("--split", choices=["train", "dev", "test"], required=True)
    p.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.command == "validate-ranking":
            result = validate_splits(args.paths)
        elif args.command == "evaluate-ranking":
            result = evaluate_ranking(args)
        elif args.command == "evaluate-state":
            result = evaluate_state(args)
        elif args.command == "replay":
            result = replay(args)
        elif args.command == "convert-fqbank":
            from .datasets import convert_fqbank
            result = convert_fqbank(args.input, args.output, args.split)
        elif args.command == "evaluate-model":
            from .train import evaluate_features
            result = evaluate_features(args.data, args.checkpoint, args.output, args.device)
        else:
            from .train import train_features
            result = train_features(args.train, args.validation, args.config, args.output)
    except (ValueError, KeyError, TypeError, OSError, ImportError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))

"""Aggregate completed evaluation reports by model/condition across seeds."""
import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default="outputs/followup")
    args = p.parse_args()
    groups = defaultdict(list)
    for path in Path(args.root).rglob("test*.json"):
        report = json.loads(path.read_text(encoding="utf-8"))
        if "metrics" not in report or "seed" not in report:
            continue
        key = (path.parent.parent.name, path.parent.name.rsplit("-", 1)[0], report["condition"])
        groups[key].append(report)
    for group, reports in sorted(groups.items()):
        if len({r["seed"] for r in reports}) != len(reports):
            raise ValueError("duplicate seed in " + str(group))
        if len({r["input_sha256"] for r in reports}) != 1:
            raise ValueError("different evaluation data in " + str(group))
        for field in ("protocol_version", "model_config", "training_config", "training_data_sha256", "encoder_sha256"):
            if any(field not in r for r in reports) or len({json.dumps(r[field], sort_keys=True) for r in reports}) != 1:
                raise ValueError("missing or inconsistent " + field + " in " + str(group))
        print(" / ".join(group), "seeds:", [r["seed"] for r in reports])
        for metric in reports[0]["metrics"]:
            values = [r["metrics"][metric] for r in reports]
            print(f"  {metric}: {statistics.mean(values):.4f} +/- {statistics.stdev(values) if len(values)>1 else 0:.4f}")
    if not groups:
        print("No completed evaluation reports found; no scores to summarize.")

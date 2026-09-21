"""Aggregate completed experiments, keeping incompatible protocols separate."""
import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from interview_robot.meld_data import dump


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=ROOT / "outputs/meld")
    p.add_argument("--output", type=Path, default=ROOT / "outputs/meld/summary.json")
    args = p.parse_args()
    grouped, seen, protocols = defaultdict(list), set(), set()
    for path in args.root.glob("*-seed*/test/evaluation.json"):
        report = json.loads(path.read_text(encoding="utf-8"))
        protocols.add((report["feature_provenance_sha256"], report["protocol_sha256"], report["evaluation_index_sha256"], report["split"], report["count"]))
        key = (report["variant"], report["seed"])
        if key in seen:
            raise ValueError(f"duplicate run: {key}")
        seen.add(key)
        for scenario, metrics in report["scenarios"].items():
            for task in ("emotion", "affect"):
                for metric in ("macro_f1", "weighted_f1", "accuracy"):
                    grouped[(report["variant"], scenario, task, metric)].append(metrics[task][metric])
    if not seen:
        raise ValueError("no completed test evaluations; no results were invented")
    if len(protocols) != 1:
        raise ValueError("cannot aggregate different datasets/feature versions/split counts")
    rows = [{"variant": key[0], "scenario": key[1], "task": key[2], "metric": key[3],
             "seeds": len(v), "mean": statistics.mean(v), "sample_std": statistics.stdev(v) if len(v) > 1 else None}
            for key, v in sorted(grouped.items())]
    dump(args.output, {"runs": len(seen), "values_are_fractions": True, "summary": rows})
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()

"""Generate explicitly synthetic feature fixtures to exercise the training CLI."""
import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from interview_robot.data import write_jsonl


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="outputs/smoke-features")
    args = parser.parse_args()
    rng = random.Random(42)
    def features(length):
        return [[rng.uniform(-1, 1) for _ in range(8)] for _ in range(length)]
    for task in ("state", "ranking"):
        for split, count in (("train", 4), ("dev", 2)):
            rows = []
            for index in range(count):
                row = {"schema_version": 1, "task": task, "id": f"{task}-{split}-{index}",
                       "group_id": f"{split}-{index}", "dataset": "synthetic-smoke",
                       "split": split, "feature_source": "seed42-random-no-real-encoder"}
                if task == "state":
                    row.update(features={m: features(3) for m in ("audio", "text", "visual")},
                               available=[[True, True, index % 2 == 0]] * 3,
                               confidence=[[0.9, 0.8, 0.7]] * 3, age=[[0.0, 0.1, 0.2]] * 3,
                               labels={"turn": index % 2, "affect": -100, "speaker_change": -100})
                else:
                    row.update(history_features=features(3), knowledge_features=features(2) if index % 2 else [],
                               question_features=[features(2), features(3), features(1)],
                               relevance=[0, 1, 0])
                rows.append(row)
            write_jsonl(Path(args.output) / task / f"{split}.jsonl", rows)
    print("Synthetic fixtures written. These are only for software verification.")


if __name__ == "__main__":
    main()

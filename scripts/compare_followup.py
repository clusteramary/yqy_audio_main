"""Compare paired predictions with dialogue-cluster confidence intervals."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from interview_robot.data import read_jsonl
from interview_robot.followup_data import file_sha, save_json
from interview_robot.research_metrics import paired_ranking_interval

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("reference", "a", "b", "output"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--metric", default="MRR", choices=["MRR", "Recall@1", "Recall@3", "nDCG@1", "nDCG@3"])
    p.add_argument("--repetitions", type=int, default=2000)
    p.add_argument("--seed", type=int, default=42)
    a = p.parse_args()
    result = paired_ranking_interval(read_jsonl(a.reference), read_jsonl(a.a), read_jsonl(a.b), a.metric, a.repetitions, a.seed)
    result["inputs_sha256"] = {p: file_sha(p) for p in (a.reference, a.a, a.b)}
    save_json(a.output, result)
    print(result)

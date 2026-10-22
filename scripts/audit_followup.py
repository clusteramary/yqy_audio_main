"""Audit train/dev candidate membership shortcuts without feature extraction."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from interview_robot.research_audit import audit_paths

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", default="data/processed/followup/kdconv")
    p.add_argument("--output", required=True)
    a = p.parse_args()
    print(audit_paths(Path(a.data) / "train.jsonl", Path(a.data) / "dev.jsonl", a.output))

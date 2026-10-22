import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from interview_robot.followup_protocol import prepare_v2

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Prepare an isolated v2 weak-label ranking benchmark; no training")
    p.add_argument("--source", default="data/processed/followup")
    p.add_argument("--output", default="data/processed/followup-v2-mixed")
    p.add_argument("--hard-negatives", type=int, default=4)
    a = p.parse_args()
    print(prepare_v2(a.source, a.output, a.hard_negatives))

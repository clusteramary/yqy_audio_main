"""Explicit batch runner. Use --phase plan to print commands without executing."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from interview_robot.meld_training import VARIANTS


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--phase", choices=["plan", "train", "evaluate"], default="plan")
    p.add_argument("--variants", nargs="+", choices=VARIANTS, default=list(VARIANTS))
    p.add_argument("--seeds", nargs="+", type=int, default=[42, 2026, 3407])
    p.add_argument("--device", default="cuda")
    args = p.parse_args()
    commands = []
    for variant in args.variants:
        for seed in args.seeds:
            run = f"outputs/meld/{variant}-seed{seed}"
            command = [sys.executable, "scripts/train_meld.py"]
            if args.phase == "evaluate":
                command += ["evaluate", "--checkpoint", run + "/best.pt", "--output", run + "/test"]
            else:
                command += ["train", "--variant", variant, "--seed", str(seed), "--output", run]
            command += ["--device", args.device]
            commands.append(command)
            if args.phase != "plan":
                subprocess.run(command, cwd=ROOT, check=True)
    if args.phase == "plan":
        print(json.dumps({"executed": False, "commands": commands}, indent=2))


if __name__ == "__main__":
    main()

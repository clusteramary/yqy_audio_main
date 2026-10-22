import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from interview_robot.meld_data import ROOT, dump


def main():
    parser = argparse.ArgumentParser(description="MELD experiment tools. Training occurs only with the train subcommand.")
    parser.add_argument("command", choices=["preflight", "plan", "train", "evaluate"])
    parser.add_argument("--config", type=Path, default=ROOT / "configs/meld_train.json")
    parser.add_argument("--variant", default="full")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--device")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--feature-dir", type=Path)
    parser.add_argument("--split", choices=["dev", "test"], default="test")
    parser.add_argument("--forward", action="store_true", help="preflight only: run an untrained forward pass")
    parser.add_argument("--quality-stress", action="store_true", help="evaluate only: additionally perturb quality metadata on clean cached embeddings")
    args = parser.parse_args()
    if args.quality_stress and args.command != "evaluate":
        parser.error("--quality-stress is only valid with evaluate")
    from interview_robot.meld_training import configure, preflight, train, evaluate_checkpoint, plan
    config = configure(args.config, args.variant, args.seed, args.device)
    if args.feature_dir:
        config["feature_dir"] = str(args.feature_dir)
    if args.command == "preflight":
        result = preflight(config, args.forward)
        if args.output:
            dump(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ready"] else 2
    if args.command == "plan":
        result = plan(args.config, args.output or ROOT / "outputs/meld/experiment_plan.json")
    else:
        if not args.output:
            parser.error("--output is required")
        if args.command == "train":
            result = train(config, args.output)
        else:
            if not args.checkpoint:
                parser.error("--checkpoint is required")
            result = evaluate_checkpoint(args.checkpoint, args.output, args.split, args.device or "cpu", args.feature_dir, args.quality_stress)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

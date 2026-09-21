import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from interview_robot.meld_data import ROOT
from interview_robot.meld_features import download_encoders, extract_features


def main():
    parser = argparse.ArgumentParser(description="Download frozen encoders or extract MELD features. No training.")
    parser.add_argument("command", choices=["download-models", "extract"])
    parser.add_argument("--config", type=Path, default=ROOT / "configs/meld_features.json")
    parser.add_argument("--models", type=Path, default=ROOT / "models/pretrained/meld")
    parser.add_argument("--raw", type=Path, default=ROOT / "data/raw/meld")
    parser.add_argument("--processed", type=Path, default=ROOT / "data/processed/meld")
    parser.add_argument("--output", type=Path, default=ROOT / "data/features/meld-v1")
    parser.add_argument("--splits", nargs="+", choices=["train", "dev", "test"], default=["train", "dev", "test"])
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--limit", type=int, help="debug subset only; use a separate output folder")
    parser.add_argument("--retry-failed", action="store_true", help="recompute cached utterances that had failed modalities")
    args = parser.parse_args()
    if args.command == "download-models":
        result = download_encoders(args.config, args.models)
    else:
        if args.limit is not None and (args.limit < 1 or args.output.resolve() == (ROOT / "data/features/meld-v1").resolve()):
            parser.error("--limit requires a positive count and separate --output directory")
        result = extract_features(args.processed, args.raw, args.output, args.config, args.models,
                                  args.splits, args.device, args.limit, args.retry_failed)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

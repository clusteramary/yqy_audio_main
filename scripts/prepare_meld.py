"""Run from repository root: python scripts/prepare_meld.py --help."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from interview_robot.meld_data import ROOT, acquire, prepare, unpack


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["download", "extract", "prepare"])
    parser.add_argument("--raw", type=Path, default=ROOT / "data/raw/meld")
    parser.add_argument("--processed", type=Path, default=ROOT / "data/processed/meld")
    parser.add_argument("--media", action="store_true", help="download the complete 10.88 GB audio/video archive")
    parser.add_argument("--require-media", action="store_true")
    args = parser.parse_args()
    if args.command == "download":
        result = acquire(args.raw, args.media)
    elif args.command == "extract":
        result = unpack(args.raw)
    else:
        result = prepare(args.raw, args.processed, args.require_media)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

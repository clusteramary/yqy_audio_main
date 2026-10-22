"""Explicit preflight/train/evaluate entry point; never downloads models."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from interview_robot import cross_encoder as ce
from interview_robot.followup_data import load_json, save_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['preflight', 'train', 'evaluate'])
    p.add_argument('--config', default='configs/cross_encoder_kdconv.json')
    p.add_argument('--output')
    p.add_argument('--device', default='cpu')
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--forward', action='store_true')
    p.add_argument('--checkpoint')
    p.add_argument('--input')
    a = p.parse_args()
    if a.forward and a.command != 'preflight':
        p.error('--forward is only for preflight')
    if a.command == 'preflight':
        report = ce.preflight(load_json(a.config), a.forward)
        if a.output:
            save_json(a.output, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif a.command == 'train':
        if not a.output:
            p.error('--output required')
        ce.train(load_json(a.config), a.output, a.device, a.seed)
    else:
        if not all((a.output, a.checkpoint, a.input)):
            p.error('--checkpoint, --input and --output required')
        print(ce.evaluate_saved(a.checkpoint, a.input, a.output, a.device))


if __name__ == '__main__':
    main()

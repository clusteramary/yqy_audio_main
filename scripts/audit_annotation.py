import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from interview_robot.annotation_audit import audit, review_pack, agreement
from interview_robot.data import read_jsonl, write_jsonl
from interview_robot.followup_data import file_sha, save_json


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Read-only heuristic audit or blinded dev annotation pack; never trains/filters.')
    p.add_argument('command', choices=['audit', 'pack', 'agreement'])
    p.add_argument('--input', required=True)
    p.add_argument('--second')
    p.add_argument('--output', required=True)
    p.add_argument('--count', type=int, default=50)
    args = p.parse_args()
    output = Path(args.output)
    if output.exists():
        p.error('choose a new output; preserve existing annotations')
    rows = read_jsonl(args.input)
    if args.command == 'audit':
        report = {**audit(rows), 'input_sha256': file_sha(args.input)}
        save_json(output, report)
    elif args.command == 'pack':
        if Path(str(output) + '.manifest.json').exists():
            p.error('pack manifest already exists')
        pack = review_pack(rows, args.count)
        write_jsonl(output, pack)
        report = {'contexts': len(pack), 'candidates': sum(len(r['candidates']) for r in pack),
                  'source_sha256': file_sha(args.input), 'pack_sha256': file_sha(output),
                  'seed': 2026, 'sampling': 'balanced domains, distinct dialogues; not prevalence sampling',
                  'human_review_complete': False}
        save_json(str(output) + '.manifest.json', report)
    else:
        if not args.second:
            p.error('--second required')
        report = agreement(rows, read_jsonl(args.second))
        report['input_sha256'] = [file_sha(args.input), file_sha(args.second)]
        save_json(output, report)
    print({k: v for k, v in report.items() if k != 'examples'})

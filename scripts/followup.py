"""Innovation-two tools: cache, train, evaluate, predict and BM25 baseline."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest="command", required=True)
    cache = commands.add_parser("cache")
    cache.add_argument("--input", nargs="+", required=True)
    cache.add_argument("--encoder", required=True)
    cache.add_argument("--output", required=True)
    cache.add_argument("--device", default="cpu")
    cache.add_argument("--batch-size", type=int, default=16)
    cache.add_argument("--max-length", type=int, default=128)
    train = commands.add_parser("train")
    train.add_argument("--config", required=True)
    train.add_argument("--output", required=True)
    train.add_argument("--device", default="cuda")
    train.add_argument("--seed", type=int, default=42)
    train.add_argument("--variant", choices=["history", "fixed", "gated", "coverage"])
    train.add_argument("--no-history-position", action="store_true", help="explicit order-encoding ablation")
    for name in ("evaluate", "predict"):
        sub = commands.add_parser(name)
        sub.add_argument("--checkpoint", required=True)
        sub.add_argument("--input", required=True)
        sub.add_argument("--cache", required=True)
        sub.add_argument("--output", required=True)
        sub.add_argument("--device", default="cpu")
        sub.add_argument("--batch-size", type=int, default=32)
        sub.add_argument("--knowledge", choices=["original", "missing", "shuffled"], default="original")
    bm25 = commands.add_parser("bm25")
    bm25.add_argument("--input", required=True)
    bm25.add_argument("--output", required=True)
    random = commands.add_parser("random")
    random.add_argument("--input", required=True)
    random.add_argument("--output", required=True)
    args = p.parse_args()
    from interview_robot import followup_training as f
    from interview_robot.followup_data import save_json
    if args.command == "cache":
        f.build_cache(args.input, args.encoder, args.output, args.device, args.batch_size, args.max_length)
    elif args.command == "train":
        f.train(args.config, args.output, args.device, args.seed, args.variant, args.no_history_position)
    elif args.command in {"bm25", "random"}:
        report = (f.lexical_evaluate if args.command == "bm25" else f.random_evaluate)(args.input)
        save_json(args.output, report)
        print(report)
    else:
        from interview_robot.data import read_jsonl, validate_ranking, write_jsonl
        cache = f.Cache(args.cache)
        cache.check_input(args.input)
        model, checkpoint = f.load_checkpoint(args.checkpoint, args.device)
        for key in ("encoder_sha256", "max_length", "pooling", "history_turns"):
            if cache.meta[key] != checkpoint["cache_meta"][key]:
                raise ValueError("encoder protocol differs from checkpoint: " + key)
        rows = read_jsonl(args.input)
        for row in rows:
            validate_ranking(row, labeled=args.command == "evaluate")
        report, predictions = f.evaluate(model, f.perturb(rows, args.knowledge), cache, args.device,
                                         args.batch_size, labeled=args.command == "evaluate")
        report.update({"condition": args.knowledge, "checkpoint": args.checkpoint, "input": args.input,
                       "protocol_version": checkpoint["protocol_version"],
                       "model_config": checkpoint["config"]["model"],
                       "training_config": checkpoint["config"]["training"],
                       "training_data_sha256": checkpoint["data_sha256"],
                       "encoder_sha256": checkpoint["cache_meta"]["encoder_sha256"],
                       "checkpoint_sha256": f.file_sha(args.checkpoint),
                       "input_sha256": f.file_sha(args.input), "seed": checkpoint["seed"]})
        save_json(args.output, report)
        write_jsonl(args.output + ".predictions.jsonl", predictions)
        print(report)


if __name__ == "__main__":
    main()

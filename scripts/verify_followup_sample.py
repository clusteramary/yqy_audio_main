"""Real encoder/cache/checkpoint forward smoke test; never updates parameters."""
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import torch
from interview_robot.data import write_jsonl
from interview_robot.followup_training import build_cache, Cache, FollowupRanker, evaluate, load_checkpoint

if __name__ == "__main__":
    torch.set_num_threads(2)
    for name, encoder, history, questions in (
        ("english", "models/pretrained/meld/distilbert-base-uncased", ["We discussed music."], ["Who composed it?", "How tall is that building?"]),
        ("chinese", "models/pretrained/followup/bert-base-chinese", ["我们刚聊了这首歌。"], ["这首歌是谁作曲的？", "这栋楼有多高？"]),
    ):
        with tempfile.TemporaryDirectory(prefix="followup-forward-") as temporary:
            root = Path(temporary)
            row = {"schema_version": 1, "dataset": "software-fixture", "split": "dev", "id": "one", "group_id": "one",
                "history": history, "knowledge": [], "knowledge_source": "none",
                "candidates": [{"id": str(i), "text": q, "relevance": int(i == 0)} for i, q in enumerate(questions)]}
            source = root / "dev.jsonl"
            write_jsonl(source, [row])
            build_cache([source], encoder, root / "cache", batch_size=2)
            cache = Cache(root / "cache")
            try:
                cache.check_input(source)
                model = FollowupRanker(cache.dim, hidden=8, heads=2)
                checkpoint = root / "untrained.pt"
                torch.save({"protocol_version": 2, "model": model.state_dict(), "dimension": cache.dim,
                    "config": {"model": {"hidden": 8, "heads": 2, "variant": "coverage"}}}, checkpoint)
                restored, _ = load_checkpoint(checkpoint, "cpu")
                report, predictions = evaluate(restored, [row], cache, "cpu", 2)
                assert report["count"] == 1 and len(predictions[0]["ranked"]) == 2
            finally:
                cache.close()
        print(f"PASS {name}: local encoder -> cache -> untrained checkpoint -> ranking; no optimizer used")

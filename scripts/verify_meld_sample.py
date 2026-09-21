"""Verify frozen encoders and a random-weight fusion forward pass; never train."""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    import numpy as np
    import torch
    from interview_robot.meld_data import dump, sha256
    from interview_robot.meld_features import FrozenEncoders, encoder_fingerprint, get_config, read_rows
    from interview_robot.meld_training import configure
    from interview_robot.models.state_model import MultimodalStateModel

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--clip", type=Path, required=True)
    p.add_argument("--split", choices=["train", "dev", "test"], default="train")
    p.add_argument("--output", type=Path, default=ROOT / "outputs/meld/sample_verification.json")
    args = p.parse_args()
    match = re.fullmatch(r"dia(\d+)_utt(\d+)\.mp4", args.clip.name)
    if not match:
        p.error("clip filename must identify the MELD dialogue and utterance")
    rows = read_rows(ROOT / f"data/processed/meld/{args.split}.jsonl")
    row = next(r for r in rows if r["dialogue_id"] == int(match[1]) and r["utterance_id"] == int(match[2]))
    torch.set_num_threads(2)
    torch.manual_seed(42)
    config = get_config(ROOT / "configs/meld_features.json")
    model_root = ROOT / "models/pretrained/meld"
    fingerprint = encoder_fingerprint(model_root, config)
    encoders = FrozenEncoders(config, model_root, "cpu")
    text, tq = encoders.text(row["text"])
    audio, aq, duration = encoders.audio(args.clip)
    visual, vq = encoders.visual(args.clip, duration)
    features = {"text": text, "audio": audio, "visual": visual}
    for name, feature in features.items():
        if feature.shape != (config[name]["dimension"],) or not np.isfinite(feature).all():
            raise ValueError(f"invalid real-media encoder output: {name}")
    model = MultimodalStateModel(**configure(ROOT / "configs/meld_train.json")["model"]).eval()
    before = {key: value.clone() for key, value in model.state_dict().items()}
    with torch.inference_mode():
        output = model({name: torch.from_numpy(value)[None, None, :] for name, value in features.items()},
                       torch.ones(1, 1, 3, dtype=torch.bool), torch.tensor([[[tq, aq, vq]]]), torch.zeros(1, 1, 3))
    unchanged = all(torch.equal(before[key], value) for key, value in model.state_dict().items())
    if not unchanged or not all(torch.isfinite(value).all() for value in output.values()):
        raise ValueError("invalid forward pass or parameters were modified")
    report = {"training_started": False, "optimizer_steps": 0, "fusion_weights_unchanged": unchanged,
              "sample_id": row["id"], "clip_sha256": sha256(args.clip), "duration_seconds": duration,
              "encoder_manifest_sha256": fingerprint, "feature_shapes": {m: list(v.shape) for m, v in features.items()},
              "output_shapes": {name: list(value.shape) for name, value in output.items()},
              "quality_proxies": {"text": tq, "audio": aq, "visual": vq},
              "note": "Random-weight fusion forward validation only; no performance result."}
    dump(args.output, report)
    print(report)


if __name__ == "__main__":
    main()

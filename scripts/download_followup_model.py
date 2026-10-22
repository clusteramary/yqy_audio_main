"""Fetch the fixed Chinese encoder; English encoder already used by MELD."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from huggingface_hub import snapshot_download
from interview_robot.followup_data import file_sha, save_json

if __name__ == "__main__":
    target = Path("models/pretrained/followup/bert-base-chinese")
    revision = "8f23c25b06e129b6c986331a13d8d025a92cf0ea"
    snapshot_download("google-bert/bert-base-chinese", revision=revision, local_dir=str(target),
        allow_patterns=["config.json", "tokenizer.json", "tokenizer_config.json", "vocab.txt", "model.safetensors", "README.md", "LICENSE"], max_workers=2)
    save_json(target / "download.json", {"repo": "google-bert/bert-base-chinese", "revision": revision,
        "files": {p.name: file_sha(p) for p in target.iterdir() if p.is_file() and p.name != "download.json"}})
    print(target)

"""Download fixed upstream snapshots and prepare innovation-two data (no training)."""
import argparse
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from interview_robot.followup_data import SOURCES, file_sha, load_json, prepare, save_json


def download(root):
    manifest_path = root / "manifest.json"
    previous = load_json(manifest_path).get("files", {}) if manifest_path.exists() else {}
    files = {}
    for name, (repo, revision) in SOURCES.items():
        paths = ["README.md", "LICENSE"]
        if name == "fq-bank":
            paths += [f"data/{s}.json" for s in ("train", "dev", "test")]
        else:
            paths += [f"data/{d}/{s}.json" for d in ("film", "music", "travel") for s in ("train", "dev", "test", f"kb_{d}")]
        for relative in paths:
            key = f"{name}/{relative}"
            dest = root / key
            url = f"https://raw.githubusercontent.com/{repo}/{revision}/{relative}"
            # Existing unmanifested clones must be verified against the pinned revision.
            if not (dest.exists() and previous.get(key, {}).get("sha256") == file_sha(dest) and previous[key].get("url") == url):
                dest.parent.mkdir(parents=True, exist_ok=True)
                for attempt in range(3):
                    try:
                        with urllib.request.urlopen(url, timeout=90) as source, dest.with_suffix(dest.suffix + ".part").open("wb") as target:
                            while True:
                                chunk = source.read(1024 * 1024)
                                if not chunk:
                                    break
                                target.write(chunk)
                        partial = dest.with_suffix(dest.suffix + ".part")
                        if dest.suffix == ".json":
                            load_json(partial)
                        partial.replace(dest)
                        break
                    except Exception:
                        if attempt == 2:
                            raise
                        time.sleep(2 ** attempt)
            files[key] = {"url": url, "bytes": dest.stat().st_size, "sha256": file_sha(dest)}
            print(key, files[key]["bytes"], flush=True)
    save_json(manifest_path, {"sources": SOURCES, "files": files})


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--raw", default="data/raw/followup")
    p.add_argument("--output", default="data/processed/followup")
    p.add_argument("--download", action="store_true")
    p.add_argument("--download-only", action="store_true")
    args = p.parse_args()
    if args.download or args.download_only:
        download(Path(args.raw))
    if not args.download_only:
        print(prepare(args.raw, args.output))

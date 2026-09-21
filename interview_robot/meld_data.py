"""MELD acquisition and annotation audit; standard library only, no training."""
from __future__ import annotations

import csv
import difflib
import hashlib
import json
import shutil
import tarfile
import time
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath

EMOTIONS = ["neutral", "surprise", "fear", "sadness", "joy", "disgust", "anger"]
SENTIMENTS = ["positive", "neutral", "negative"]
COUNTS = {"train": 9989, "dev": 1109, "test": 2610}
# These are defects of the pinned official raw archive, not download failures.
# See declare-lab/MELD issue #22 and the independent MELD-FAIR audit.
EXPECTED_MISSING_MEDIA = {"train": set(), "dev": {"meld:dev:110:7"}, "test": set()}
EXPECTED_CORRUPT_MEDIA = {"train": {"meld:train:125:3"}, "dev": set(), "test": set()}
ARCHIVE_SHA256 = "a56b4407d574195cbce470d86f9c9d72fcfea59b0e34502ecd4babee4a5c613e"
ARCHIVE_BYTES = 10878146150
HF_REVISION = "9abc51ee7903424ffb971297608aa6d3d0de3bfa"
ANNOTATION_REVISION = "e8cedf27b5d2877e198332c957127e16eb214afe"
ANNOTATION_SHA256 = {
    "train_sent_emo.csv": "d2fa2d6529cf03cac2989efec05c9b27d8fd2f4c8fc5974c7ae88aa537fa02db",
    "dev_sent_emo.csv": "2e89c6f8aa182d6f62f8c6331aece905ac7273ca4999660bfb5213e1d0370c1c",
    "test_sent_emo.csv": "8d37103938f7067600839fe29d5a114a6cd1bcdafb75bec101e06464c5006888",
}
ARCHIVE_URL = f"https://huggingface.co/datasets/declare-lab/MELD/resolve/{HF_REVISION}/MELD.Raw.tar.gz"
ROOT = Path(__file__).resolve().parents[1]


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url, destination, expected_sha=None, expected_bytes=None):
    """Resume HTTP ranges. A partial download is NEVER renamed before verification."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        actual = sha256(destination)
        if expected_sha and actual != expected_sha:
            raise ValueError(f"checksum mismatch: {destination}; preserve it for investigation")
        if expected_bytes and destination.stat().st_size != expected_bytes:
            raise ValueError(f"size mismatch: {destination}")
        return {"url": url, "bytes": destination.stat().st_size, "sha256": actual}
    part = destination.with_name(destination.name + ".part")
    for attempt in range(8):
        offset = part.stat().st_size if part.exists() else 0
        if expected_bytes and offset == expected_bytes:
            break
        headers = {"User-Agent": "thesis-meld-preparation/1.0", "Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
                mode = "ab" if response.status == 206 else "wb"
                if mode == "ab" and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                    raise ValueError("server returned an incorrect resume range")
                total = expected_bytes or (int(response.headers.get("Content-Length", 0)) + (offset if mode == "ab" else 0))
                last = time.monotonic()
                with part.open(mode) as output:
                    while True:
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        output.write(block)
                        if time.monotonic() - last > 20:
                            output.flush()
                            print(f"{destination.name}: {output.tell():,} / {total:,} bytes", flush=True)
                            last = time.monotonic()
            if total and part.stat().st_size != total:
                raise OSError("incomplete response")
            break
        except (OSError, TimeoutError) as error:
            print(f"download retry {attempt + 1}: {type(error).__name__}: {error}", flush=True)
            if attempt == 7:
                raise
            time.sleep(min(2 ** attempt, 15))
    actual = sha256(part)
    if expected_sha and actual != expected_sha:
        raise ValueError(f"SHA256 mismatch; kept partial file: {part}")
    if expected_bytes and part.stat().st_size != expected_bytes:
        raise ValueError("download size mismatch")
    part.replace(destination)
    info = {"url": url, "bytes": destination.stat().st_size, "sha256": actual}
    dump(destination.with_name(destination.name + ".download.json"), info)
    return info


def acquire(raw, media=False):
    raw = Path(raw)
    source = f"https://raw.githubusercontent.com/declare-lab/MELD/{ANNOTATION_REVISION}/"
    assets = {f"{split}_sent_emo.csv": source + f"data/MELD/{split}_sent_emo.csv" for split in COUNTS}
    assets["UPSTREAM_README.md"] = source + "README.md"
    assets["UPSTREAM_LICENSE"] = source + "LICENSE"
    records = {}
    for name, url in assets.items():
        records[name] = download(url, raw / name, ANNOTATION_SHA256.get(name))
        print(f"ready: {name}", flush=True)
    if media:
        records["MELD.Raw.tar.gz"] = download_parallel(ARCHIVE_URL, raw / "MELD.Raw.tar.gz", ARCHIVE_SHA256, ARCHIVE_BYTES)
    manifest = raw / "download_manifest.json"
    previous = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
    previous.update(records)
    dump(manifest, previous)
    return records


def download_parallel(url, destination, expected_sha, total, workers=24):
    """Fixed, validated byte ranges; completed chunks survive interruptions."""
    destination = Path(destination)
    if destination.exists():
        return download(url, destination, expected_sha, total)
    parts = destination.with_name(destination.name + ".parts")
    parts.mkdir(parents=True, exist_ok=True)
    chunk_size = 64 * 1024 * 1024
    ranges = [(i, start, min(start + chunk_size, total) - 1) for i, start in enumerate(range(0, total, chunk_size))]

    def fetch(item):
        i, start, end = item
        target = parts / f"{i:04d}.bin"
        size = end - start + 1
        if target.exists() and target.stat().st_size == size:
            return size
        temp = target.with_suffix(".part")
        for attempt in range(8):
            offset = temp.stat().st_size if temp.exists() else 0
            if offset == size:
                temp.replace(target)
                return size
            req = urllib.request.Request(url + f"?chunk={i}&offset={offset}", headers={
                "Range": f"bytes={start + offset}-{end}", "Accept-Encoding": "identity"})
            try:
                with urllib.request.urlopen(req, timeout=60) as response:
                    if response.status != 206 or response.headers.get("Content-Range") != f"bytes {start + offset}-{end}/{total}":
                        raise ValueError("source does not honor the exact byte range")
                    with temp.open("ab") as stream:
                        while True:
                            block = response.read(1024 * 1024)
                            if not block:
                                break
                            stream.write(block)
                if temp.stat().st_size != size:
                    raise OSError("short range download")
                temp.replace(target)
                return size
            except (OSError, TimeoutError):
                if attempt == 7:
                    raise
                time.sleep(min(2 ** attempt, 10))
        raise OSError("range failed")

    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch, item) for item in ranges]
        for future in as_completed(futures):
            completed += future.result()
            print(f"completed ranges: {completed:,} / {total:,} bytes", flush=True)
            dump(parts / "progress.json", {"completed_bytes": completed, "total_bytes": total, "workers": workers})
    assembling = destination.with_name(destination.name + ".assembling")
    with assembling.open("wb") as output:
        for i, _, _ in ranges:
            with (parts / f"{i:04d}.bin").open("rb") as stream:
                shutil.copyfileobj(stream, output, length=8 * 1024 * 1024)
    if sha256(assembling) != expected_sha:
        raise ValueError("assembled archive SHA256 mismatch; keep parts for investigation")
    assembling.replace(destination)
    result = {"url": url, "bytes": total, "sha256": expected_sha}
    dump(destination.with_name(destination.name + ".download.json"), result)
    return result


def extract_safe(archive, destination):
    """Stream files, reject traversal/links/devices; do not run archive contents."""
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    count = 0
    with tarfile.open(archive, "r|gz") as tar:
        for member in tar:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or "\\" in member.name or ":" in member.name:
                raise ValueError(f"unsafe archive path: {member.name}")
            target = (destination / member.name).resolve()
            if destination != target and destination not in target.parents:
                raise ValueError("archive path outside extraction root")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                # Streaming the archive still validates its gzip CRC at EOF.
                if target.exists() and target.stat().st_size == member.size:
                    continue
                temp = target.with_name(target.name + ".extracting")
                with tar.extractfile(member) as src, temp.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                if temp.stat().st_size != member.size:
                    raise ValueError("truncated tar member")
                temp.replace(target)
                count += 1
                if count % 2000 == 0:
                    print(f"extracted {count} files from {Path(archive).name}", flush=True)
            else:
                raise ValueError(f"unsupported archive member: {member.name}")
    return count


def unpack(raw):
    raw = Path(raw)
    archive = raw / "MELD.Raw.tar.gz"
    if sha256(archive) != ARCHIVE_SHA256:
        raise ValueError("official raw archive checksum failed")
    extracted = raw / "extracted"
    result = {"outer": extract_safe(archive, extracted)}
    for inner in sorted(extracted.rglob("*.tar.gz")):
        result[str(inner.relative_to(raw))] = extract_safe(inner, inner.parent / inner.name[:-7])
    dump(raw / "extraction.json", {"archive_sha256": ARCHIVE_SHA256, "files_written": result})
    return result


def seconds(value):
    hh, mm, rest = value.strip().replace(",", ".").split(":")
    return int(hh) * 3600 + int(mm) * 60 + float(rest)


def read_annotations(path, split):
    result, seen = [], set()
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            dialogue, utterance = int(row["Dialogue_ID"]), int(row["Utterance_ID"])
            key = (dialogue, utterance)
            if key in seen:
                raise ValueError(f"duplicate utterance key in {split}: {key}")
            seen.add(key)
            start, end = seconds(row["StartTime"]), seconds(row["EndTime"])
            result.append({"schema_version": 1, "dataset": "MELD", "split": split,
                "id": f"meld:{split}:{dialogue}:{utterance}", "group_id": f"meld:{split}:{dialogue}",
                "dialogue_id": dialogue, "utterance_id": utterance,
                "text": row["Utterance"].strip(), "speaker_metadata_only": row["Speaker"],
                "season": row["Season"], "episode": row["Episode"],
                "start_seconds": start, "end_seconds": end,
                "labels": {"emotion": EMOTIONS.index(row["Emotion"].lower()),
                           "affect": SENTIMENTS.index(row["Sentiment"].lower())},
                "label_source": "official Emotion and Sentiment columns; no turn labels",
                "clip_name": f"dia{dialogue}_utt{utterance}.mp4"})
    return sorted(result, key=lambda r: (r["dialogue_id"], r["utterance_id"]))


def media_index(raw):
    index = {}
    for path in (Path(raw) / "extracted").rglob("*.mp4"):
        parts = "/".join(path.relative_to(raw).parts[:-1]).lower()
        if "train" in parts:
            split = "train"
        elif "dev" in parts:
            split = "dev"
        elif "test" in parts:
            split = "test"
        else:
            continue
        key = (split, path.name)
        if key in index:
            raise ValueError(f"ambiguous duplicate media: {key}")
        index[key] = path
    return index


def prepare(raw, processed, require_media=False):
    raw, processed = Path(raw), Path(processed)
    processed.mkdir(parents=True, exist_ok=True)
    clips, all_rows, stats, annotation_hashes = media_index(raw), {}, {}, {}
    locations, dialogue_hashes = defaultdict(set), defaultdict(set)
    for split, expected in COUNTS.items():
        source = raw / f"{split}_sent_emo.csv"
        rows = read_annotations(source, split)
        if len(rows) != expected:
            raise ValueError(f"unexpected official {split} size: {len(rows)} != {expected}")
        annotation_hashes[split] = sha256(source)
        missing, bad_times, overlapping = [], [], []
        groups = defaultdict(list)
        for row in rows:
            clip = clips.get((split, row["clip_name"]))
            row["media"] = str(clip.relative_to(raw)).replace("\\", "/") if clip else None
            if not clip:
                missing.append(row["id"])
            if row["end_seconds"] <= row["start_seconds"]:
                bad_times.append(row["id"])
            # Timestamp+episode audit is independent of partition-local dialogue IDs.
            key = (row["season"], row["episode"], row["start_seconds"], row["end_seconds"])
            locations[key].add(split)
            groups[row["group_id"]].append(row)
        for group in groups.values():
            text = "\n".join(" ".join(r["text"].lower().split()) for r in group)
            dialogue_hashes[hashlib.sha256(text.encode()).hexdigest()].add(split)
            for i, row in enumerate(group):
                # Include earlier turns only when the entire clip has ended by this target.
                # Current utterance is always last; CSV timestamps are not used as age.
                row["past_ids"] = [p["id"] for p in group[:i]
                    if row["end_seconds"] > row["start_seconds"] and p["end_seconds"] > p["start_seconds"]
                    and p["end_seconds"] <= row["end_seconds"]]
                if i and row["start_seconds"] < group[i - 1]["end_seconds"]:
                    overlapping.append(row["id"])
        stats[split] = {"utterances": len(rows), "dialogues": len(groups),
            "emotion": dict(Counter(EMOTIONS[r["labels"]["emotion"]] for r in rows)),
            "sentiment": dict(Counter(SENTIMENTS[r["labels"]["affect"]] for r in rows)),
            "missing_media_count": len(missing), "missing_media_ids": missing,
            "invalid_annotation_time_ids": bad_times, "overlapping_utterances": len(overlapping),
            "texts_with_replacement_character": sum("\ufffd" in r["text"] for r in rows)}
        unexpected_missing = set(missing) - EXPECTED_MISSING_MEDIA[split]
        expected_not_missing = EXPECTED_MISSING_MEDIA[split] - set(missing)
        stats[split]["expected_upstream_missing_media_ids"] = sorted(set(missing) & EXPECTED_MISSING_MEDIA[split])
        stats[split]["known_upstream_corrupt_media_ids"] = sorted(EXPECTED_CORRUPT_MEDIA[split])
        stats[split]["unexpected_missing_media_ids"] = sorted(unexpected_missing)
        if require_media and (unexpected_missing or expected_not_missing):
            raise ValueError(f"{split}: media inventory differs from the pinned protocol; "
                             f"unexpected missing={sorted(unexpected_missing)}, expected missing but present={sorted(expected_not_missing)}")
        all_rows[split] = rows
    collisions = [list(key) for key, splits in locations.items() if len(splits) > 1]
    repeated_dialogues = [key for key, splits in dialogue_hashes.items() if len(splits) > 1]
    # An exact short greeting in another episode is NOT evidence of leakage.
    # Require matching episode, overlapping time, and substantial similar text.
    by_episode, duplicates, excluded = defaultdict(list), [], set()
    for split in ("test", "dev", "train"):
        for row in all_rows[split]:
            episode = (row["season"], row["episode"])
            text = " ".join(row["text"].casefold().split())
            for other, other_text in by_episode[episode]:
                overlap = min(row["end_seconds"], other["end_seconds"]) - max(row["start_seconds"], other["start_seconds"])
                if other["split"] == split or overlap <= 0 or min(len(text), len(other_text)) < 20:
                    continue
                similar = difflib.SequenceMatcher(None, text, other_text).ratio()
                if similar >= .85 or text in other_text or other_text in text:
                    duplicates.append({"earlier_partition_id": row["id"], "holdout_id": other["id"], "text_similarity": similar})
                    excluded.add(row["group_id"])
            by_episode[episode].append((row, text))
    protocol = {"name": "meld-scene-dedup-v1", "excluded_groups": sorted(excluded), "evidence": duplicates,
                "rule": "exclude lower-priority whole dialogue: same episode, overlapping timestamps, >=20-character texts with ratio>=.85 or containment; priority test>dev>train",
                "counts_after_exclusion": {s: sum(r["group_id"] not in excluded for r in rr) for s, rr in all_rows.items()}}
    dump(processed / "protocol.json", protocol)
    audit = {"protocol": "meld-official-completed-utterance-v1", "annotation_sha256": annotation_hashes,
        "labels": {"emotion": EMOTIONS, "affect": SENTIMENTS}, "splits": stats,
        "cross_split_same_episode_timestamps": collisions, "cross_split_identical_dialogue_sha256": repeated_dialogues,
        "deduplicated_protocol": protocol,
        "notes": ["Official manifests are preserved; a separate protocol excludes evidenced scene overlap. Speakers are not identity-disjoint.",
                  "MELD has no HOLD/SHIFT, active-speaker or willingness ground truth.",
                  "Overlapping previous clips that end after the target are excluded from history."]}
    for split, rows in all_rows.items():
        (processed / f"{split}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    dump(processed / "audit.json", audit)
    return {"splits": {s: {k: v for k, v in t.items() if not k.endswith("_ids")} for s, t in stats.items()},
            "cross_split_same_episode_timestamps": len(collisions), "cross_split_identical_dialogues": len(repeated_dialogues)}

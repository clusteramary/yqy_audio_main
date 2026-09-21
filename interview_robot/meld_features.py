"""Frozen raw-media encoders; caches contain features, never label-trained embeddings."""
from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path

from .meld_data import ROOT, download, download_parallel, dump, sha256


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def get_config(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def download_encoders(config_path, model_root):
    """Pin upstream revisions and record hashes. No GPU or torch required."""
    config, model_root = get_config(config_path), Path(model_root)
    existing_manifest = model_root / "manifest.json"
    if existing_manifest.exists():
        try:
            encoder_fingerprint(model_root, config)
        except FileNotFoundError:
            pass
        else:
            existing = get_config(existing_manifest)["files"]
            return {"files": len(existing), "bytes": sum(r["bytes"] for r in existing.values()),
                    "reused_local_verified_files": True}
    manifest = {}
    for modality in ("text", "audio"):
        spec = config[modality]
        metadata_url = f"https://huggingface.co/api/models/{spec['repo']}/revision/{spec['revision']}?blobs=true"
        with urllib.request.urlopen(metadata_url, timeout=60) as response:
            metadata = json.load(response)
        files = {r["rfilename"]: r for r in metadata["siblings"]}
        for filename in spec["files"]:
            url = f"https://huggingface.co/{spec['repo']}/resolve/{spec['revision']}/{filename}"
            path = model_root / spec["folder"] / filename
            lfs = files[filename].get("lfs")
            if lfs:
                record = download_parallel(url, path, lfs["sha256"], lfs["size"], workers=6)
            else:
                record = download(url, path)
            manifest[str(path.relative_to(model_root)).replace("\\", "/")] = record
            print(f"encoder file ready: {path.name}", flush=True)
    spec = config["visual"]
    record = download(spec["url"], model_root / spec["filename"])
    if not record["sha256"].startswith("f37072fd"):
        raise ValueError("ResNet18 upstream hash prefix failed")
    manifest[spec["filename"]] = record
    dump(model_root / "manifest.json", {"feature_config": config, "files": manifest})
    return {"files": len(manifest), "bytes": sum(r["bytes"] for r in manifest.values())}


def encoder_fingerprint(model_root, config, verify=True):
    model_root = Path(model_root)
    path = model_root / "manifest.json"
    manifest = get_config(path)
    if manifest["feature_config"] != config:
        raise ValueError("encoder manifest/config mismatch; use a new feature directory")
    if verify:
        for name, record in manifest["files"].items():
            if sha256(model_root / name) != record["sha256"]:
                raise ValueError(f"encoder checksum failed: {name}")
    return sha256(path)


class FrozenEncoders:
    def __init__(self, config, model_root, device="cpu"):
        import torch
        import imageio_ffmpeg
        from transformers import AutoTokenizer, AutoModel, AutoFeatureExtractor
        from torchvision.models import resnet18, ResNet18_Weights

        self.torch, self.config, self.device = torch, config, device
        self.ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        model_root = Path(model_root)
        text_path = model_root / config["text"]["folder"]
        audio_path = model_root / config["audio"]["folder"]
        self.tokenizer = AutoTokenizer.from_pretrained(text_path, local_files_only=True, clean_up_tokenization_spaces=False)
        text_model, text_info = AutoModel.from_pretrained(text_path, local_files_only=True, use_safetensors=True, output_loading_info=True)
        if text_info["missing_keys"] or text_info["mismatched_keys"]:
            raise ValueError("text encoder has uninitialized or mismatched weights")
        self.text_model = text_model.to(device).eval()
        self.audio_processor = AutoFeatureExtractor.from_pretrained(audio_path, local_files_only=True)
        # SpecAugment is a training-only operation. Disable it explicitly so no
        # unused random masked_spec_embed parameter is constructed during inference.
        audio_model, audio_info = AutoModel.from_pretrained(audio_path, local_files_only=True, use_safetensors=True,
            output_loading_info=True, mask_time_prob=0.0, mask_feature_prob=0.0, apply_spec_augment=False)
        if audio_info["missing_keys"] or audio_info["mismatched_keys"]:
            raise ValueError("audio encoder has uninitialized or mismatched weights")
        self.audio_model = audio_model.to(device).eval()
        self.visual_model = resnet18(weights=None)
        weight_path = model_root / config["visual"]["filename"]
        try:
            state = torch.load(weight_path, map_location="cpu", weights_only=True)
        except TypeError:  # Only the pinned, locally checksummed upstream file on torch 1.12.
            state = torch.load(weight_path, map_location="cpu")
        self.visual_model.load_state_dict(state)
        self.visual_model.fc = torch.nn.Identity()
        self.visual_model.to(device).eval()
        self.visual_transform = ResNet18_Weights.IMAGENET1K_V1.transforms()
        for model in (self.text_model, self.audio_model, self.visual_model):
            for parameter in model.parameters():
                parameter.requires_grad_(False)

    def text(self, value):
        torch = self.torch
        maximum = self.config["text"]["max_tokens"]
        full = self.tokenizer(value, add_special_tokens=True, truncation=False)["input_ids"]
        tokenized = self.tokenizer(value, truncation=True, max_length=maximum, return_tensors="pt").to(self.device)
        with torch.inference_mode():
            hidden = self.text_model(**tokenized).last_hidden_state
            mask = tokenized["attention_mask"].unsqueeze(-1)
            pooled = (hidden * mask).sum(1) / mask.sum(1).clamp_min(1)
        # Retained-token ratio is a heuristic, NOT ASR confidence or probability.
        quality = min(1.0, maximum / max(len(full), 1))
        return pooled[0].cpu().numpy(), quality

    def audio(self, path):
        import numpy as np
        torch = self.torch
        rate = self.config["audio"]["sample_rate"]
        result = subprocess.run([self.ffmpeg, "-v", "error", "-i", str(path), "-vn", "-ac", "1",
                                 "-ar", str(rate), "-f", "f32le", "pipe:1"], capture_output=True, timeout=120)
        if result.returncode:
            raise ValueError("audio decode: " + result.stderr.decode(errors="replace")[-300:])
        wave = np.frombuffer(result.stdout, dtype="<f4").copy()
        if len(wave) < 400 or not np.isfinite(wave).all():
            raise ValueError("audio too short or invalid")
        chunk = int(rate * self.config["audio"]["chunk_seconds"])
        pooled, sizes = [], []
        with torch.inference_mode():
            for begin in range(0, len(wave), chunk):
                signal = wave[begin:begin + chunk]
                if len(signal) < 400:
                    signal = np.pad(signal, (0, 400 - len(signal)))
                inputs = self.audio_processor(signal, sampling_rate=rate, return_tensors="pt")
                hidden = self.audio_model(inputs.input_values.to(self.device)).last_hidden_state
                pooled.append(hidden.mean(1)[0].cpu().numpy())
                sizes.append(min(chunk, len(wave) - begin))
        # Clipping and near-silence proxy, not a speech emotion confidence score.
        rms = float(np.sqrt(np.mean(wave ** 2)))
        quality = float(np.clip(min(1.0, rms / .01) * (1 - np.mean(np.abs(wave) >= .99)), .05, 1))
        return np.average(pooled, axis=0, weights=sizes).astype("float32"), quality, len(wave) / rate

    def visual(self, path, duration):
        import io
        import numpy as np
        from PIL import Image

        frames, quality = [], []
        count = self.config["visual"]["frames"]
        for i in range(count):
            at = max(0, duration * (i + .5) / count)
            result = subprocess.run([self.ffmpeg, "-v", "error", "-ss", str(at), "-i", str(path),
                                     "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
                                    capture_output=True, timeout=60)
            if result.returncode or not result.stdout:
                continue
            with Image.open(io.BytesIO(result.stdout)) as image:
                image = image.convert("RGB")
                small = np.asarray(image.resize((64, 64)).convert("L"), dtype=np.float32)
                quality.append(float(np.clip(small.std() / 40, .05, 1)))
                frames.append(self.visual_transform(image))
        if not frames:
            raise ValueError("no decodable video frame")
        with self.torch.inference_mode():
            features = self.visual_model(self.torch.stack(frames).to(self.device)).mean(0).cpu().numpy()
        # Image contrast and decoded-frame ratio, NOT face detection confidence.
        return features, float(sum(quality) / count)


def extract_features(processed, raw, output, config_path, model_root, splits, device="cpu", limit=None, retry_failed=False):
    import numpy as np

    config = get_config(config_path)
    fingerprint = encoder_fingerprint(model_root, config)
    output, processed, raw = Path(output), Path(processed), Path(raw)
    output.mkdir(parents=True, exist_ok=True)
    source = {"config": config, "encoder_manifest_sha256": fingerprint,
              "extractor_sha256": sha256(Path(__file__)),
              "annotation_manifest_sha256": {s: sha256(processed / f"{s}.jsonl") for s in ("train", "dev", "test")},
              "feature_dimensions": {m: config[m]["dimension"] for m in ("text", "audio", "visual")},
              "quality": "heuristic signal quality; no calibrated confidence", "prediction_time": "completed utterance"}
    provenance = output / "provenance.json"
    if provenance.exists() and get_config(provenance) != source:
        raise ValueError("feature directory belongs to a different encoder/data version")
    dump(provenance, source)
    encoders = FrozenEncoders(config, model_root, device)
    summary = {}
    for split in splits:
        rows = read_rows(processed / f"{split}.jsonl")
        if limit is not None:
            rows = rows[:limit]
        folder = output / split
        folder.mkdir(parents=True, exist_ok=True)
        index, errors = [], []
        for i, row in enumerate(rows):
            key = f"dia{row['dialogue_id']}_utt{row['utterance_id']}"
            target = folder / f"{key}.npz"
            cached_meta = folder / f"{key}.json"
            complete = target.exists() and cached_meta.exists()
            if not complete or (retry_failed and get_config(cached_meta)["failures"]):
                values = {m: np.zeros(config[m]["dimension"], dtype="float32") for m in ("text", "audio", "visual")}
                available, quality, failures = np.zeros(3, dtype=bool), np.zeros(3, dtype="float32"), {}
                duration = max(.1, row["end_seconds"] - row["start_seconds"])
                media = raw / row["media"] if row.get("media") else None
                for j, modality in enumerate(("text", "audio", "visual")):
                    try:
                        if modality == "text":
                            if not row["text"]:
                                raise ValueError("empty text")
                            value, q = encoders.text(row["text"])
                        elif media is None or not media.is_file():
                            raise ValueError("missing media")
                        elif modality == "audio":
                            value, q, duration = encoders.audio(media)
                        else:
                            value, q = encoders.visual(media, duration)
                        if value.shape != values[modality].shape or not np.isfinite(value).all():
                            raise ValueError("invalid encoder output")
                        values[modality], available[j], quality[j] = value.astype("float32"), True, q
                    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                        failures[modality] = str(error)
                temp = target.with_suffix(".tmp")
                with temp.open("wb") as stream:
                    np.savez_compressed(stream, **values, available=available, confidence=quality, age=np.zeros(3, dtype="float32"))
                temp.replace(target)
                dump(cached_meta, {"id": row["id"], "failures": failures, "feature_sha256": sha256(target)})
            meta = get_config(cached_meta)
            if meta["id"] != row["id"] or sha256(target) != meta["feature_sha256"]:
                raise ValueError(f"cached feature checksum failed: {target}")
            if meta["failures"]:
                errors.append(meta)
            # Text, speaker, emotion names and other metadata are not model inputs.
            index.append({k: row[k] for k in ("id", "group_id", "split", "labels", "past_ids")})
            index[-1]["feature"] = f"{split}/{target.name}"
            index[-1]["feature_sha256"] = meta["feature_sha256"]
            if (i + 1) % 100 == 0:
                print(f"features {split}: {i + 1}/{len(rows)}", flush=True)
        (output / f"{split}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in index), encoding="utf-8")
        summary[split] = {"count": len(index), "limited_debug_subset": limit is not None,
                          "samples_with_failed_modalities": len(errors), "failures": errors}
        dump(output / f"{split}.extraction.json", summary[split])
    return summary

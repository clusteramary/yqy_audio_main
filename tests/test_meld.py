"""Data invariants and forward-only checks. These tests never update weights."""
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from interview_robot.meld_data import extract_safe, read_annotations

try:
    import numpy as np
    import torch
except ImportError:
    torch = None


class MeldAnnotationTests(unittest.TestCase):
    def test_official_labels_are_not_turn_labels(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.csv"
            path.write_text('Utterance,Speaker,Emotion,Sentiment,Dialogue_ID,Utterance_ID,Season,Episode,StartTime,EndTime\nHello,Joey,joy,positive,0,0,1,1,"00:00:01,000","00:00:02,200"\n', encoding="utf-8")
            row = read_annotations(path, "train")[0]
            self.assertEqual(row["labels"], {"emotion": 4, "affect": 0})
            self.assertNotIn("turn", row["labels"])
            self.assertAlmostEqual(row["end_seconds"], 2.2)

    def test_archive_rejects_traversal_and_symlink(self):
        for name, kind in (("../outside.txt", tarfile.REGTYPE), ("link", tarfile.SYMTYPE)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "bad.tar.gz"
                with tarfile.open(path, "w:gz") as archive:
                    member = tarfile.TarInfo(name)
                    member.type, member.size = kind, 0
                    archive.addfile(member, io.BytesIO())
                with self.assertRaises(ValueError):
                    extract_safe(path, Path(temp) / "out")
                self.assertFalse((Path(temp) / "outside.txt").exists())


@unittest.skipIf(torch is None, "optional torch/numpy are not installed")
class MeldFeatureTests(unittest.TestCase):
    def setUp(self):
        from interview_robot.meld_training import MODALITIES
        torch.set_num_threads(1)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.dims = {name: 4 for name in MODALITIES}
        self.norm = {name: {"mean": np.zeros(4, dtype="float32"), "std": np.ones(4, dtype="float32")}
                     for name in MODALITIES}
        self.rows = []
        for i in range(3):
            name = f"{i}.npz"
            np.savez(self.root / name, **{m: np.full(4, i + 1, dtype="float32") for m in MODALITIES},
                     available=np.ones(3, dtype=bool), confidence=np.ones(3, dtype="float32"), age=np.zeros(3, dtype="float32"))
            self.rows.append({"id": f"meld:train:0:{i}", "group_id": "meld:train:0", "split": "train", "feature": name,
                              "past_ids": [f"meld:train:0:{j}" for j in range(i)], "labels": {"emotion": i, "affect": i}})
        (self.root / "provenance.json").write_text(json.dumps({"feature_dimensions": self.dims}), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_windows_only_use_past_and_current_features(self):
        from interview_robot.meld_training import MeldWindows
        dataset = MeldWindows(self.root, self.rows, self.norm, window=2)
        first, second, third = dataset[0], dataset[1], dataset[2]
        self.assertEqual(first["features"]["text"].shape, (1, 4))
        self.assertEqual(second["features"]["text"][:, 0].tolist(), [1, 2])
        self.assertEqual(third["features"]["text"][:, 0].tolist(), [2, 3])
        # Different labels do not enter the model input.
        self.rows[1]["labels"] = {"emotion": 6, "affect": 0}
        self.assertTrue(np.array_equal(second["features"]["text"], dataset[1]["features"]["text"]))

    def test_missing_masks_are_fixed_across_windows_and_variants(self):
        from interview_robot.meld_training import MeldWindows, stable_missing
        all_names = MeldWindows(self.root, self.rows, self.norm, scenario="random_missing_0.5")
        text_only = MeldWindows(self.root, self.rows, self.norm, names=("text",), scenario="random_missing_0.5")
        self.assertTrue(np.array_equal(all_names[1]["available"][:, 0], text_only[1]["available"][:, 0]))
        self.assertTrue(np.array_equal(all_names[1]["available"][0], all_names[2]["available"][0]))
        self.assertTrue(stable_missing("fixed", 1).all())
        self.assertFalse(stable_missing("fixed", 0).any())

    def test_normalization_train_only_and_once_per_utterance(self):
        from interview_robot.meld_training import fit_normalization
        norm = fit_normalization({"feature_dir": str(self.root)}, self.rows)
        self.assertTrue(np.allclose(norm["text"]["mean"], 2))
        self.assertEqual(norm["text"]["count"], 3)
        with self.assertRaises(ValueError):
            fit_normalization({"feature_dir": str(self.root)}, [{**self.rows[0], "split": "dev"}])

    def test_quality_stress_preserves_features_and_causal_window_identity(self):
        from interview_robot.meld_training import MeldWindows, QUALITY_STRESS
        for i in range(3):
            np.savez(self.root / f"{i}.npz", **{m: np.full(4, i + 1, dtype="float32") for m in self.dims},
                     available=np.array([True, True, False]), confidence=np.array([.2, .8, 0], dtype="float32"),
                     age=np.zeros(3, dtype="float32"))
        clean = MeldWindows(self.root, self.rows, self.norm)
        for mode in QUALITY_STRESS:
            changed = MeldWindows(self.root, self.rows, self.norm, quality_stress=mode)
            self.assertTrue(np.array_equal(changed[1]['confidence'][0], changed[2]['confidence'][0]))
            self.assertTrue(np.array_equal(changed[2]['available'], clean[2]['available']))
            for m in self.dims:
                self.assertTrue(np.array_equal(changed[2]['features'][m], clean[2]['features'][m]))
            self.assertTrue((changed[2]['confidence'][:, 2] == 0).all())
            self.assertEqual(changed[2]['labels'], clean[2]['labels'])
        rotated = MeldWindows(self.root, self.rows, self.norm, quality_stress='quality_rotated')
        self.assertTrue(np.allclose(rotated[0]['confidence'][0], [.8, .2, 0]))

    def test_quality_modes_do_not_mutate_cache_or_create_missing_evidence(self):
        from interview_robot.meld_training import perturb_quality
        q = np.array([.1, .4, .9], dtype='float32')
        original = q.copy()
        mask = np.ones(3, dtype=bool)
        self.assertTrue(np.allclose(perturb_quality(q, mask, 'quality_inverted'), [.9, .6, .1]))
        self.assertTrue(np.allclose(perturb_quality(q, mask, 'quality_rotated'), [.9, .1, .4]))
        self.assertTrue(np.allclose(perturb_quality(q, mask, 'quality_ones'), [1, 1, 1]))
        self.assertTrue(np.allclose(perturb_quality(q, mask, quality_mode='uniform'), [1, 1, 1]))
        self.assertTrue(np.allclose(perturb_quality(q, mask, 'quality_inverted', 'uniform'), [.9, .6, .1]))
        self.assertTrue(np.array_equal(original, q))
        self.assertFalse(perturb_quality(q, np.zeros(3, dtype=bool), 'quality_ones').any())
        with self.assertRaises(ValueError):
            perturb_quality(q, mask, 'invalid')

    def test_future_history_is_rejected(self):
        from interview_robot.meld_training import load_split
        self.rows[0]["past_ids"] = [self.rows[2]["id"]]
        (self.root / "train.jsonl").write_text("\n".join(json.dumps(r) for r in self.rows), encoding="utf-8")
        (self.root / "train.extraction.json").write_text(json.dumps({"count": 3, "limited_debug_subset": False}), encoding="utf-8")
        (self.root / "protocol.json").write_text('{"excluded_groups": []}', encoding="utf-8")
        with patch("interview_robot.meld_training.COUNTS", {"train": 3}), self.assertRaises(ValueError):
            load_split({"feature_dir": str(self.root), "protocol": str(self.root / "protocol.json")}, "train")

    def test_all_variants_forward_without_optimizer(self):
        from interview_robot.meld_training import VARIANTS, MeldWindows, collate, configure, forward_batch, ROOT
        from interview_robot.models.state_model import MultimodalStateModel
        with patch("torch.optim.AdamW", side_effect=AssertionError("training is forbidden in preparation tests")):
            for variant in VARIANTS:
                config = configure(ROOT / "configs/meld_train.json", variant)
                config["model"]["input_dims"] = {m: 4 for m in config["model"]["input_dims"]}
                model = MultimodalStateModel(**config["model"]).eval()
                before = {k: v.clone() for k, v in model.state_dict().items()}
                for scenario in ("clean", "all_missing"):
                    data = MeldWindows(self.root, self.rows, self.norm, names=tuple(config["model"]["input_dims"]), scenario=scenario)
                    batch = collate([data[0], data[2]])
                    with torch.inference_mode():
                        out = forward_batch(model, batch, "cpu")
                    self.assertEqual(tuple(out["emotion"].shape), (2, 7))
                    self.assertEqual(tuple(out["affect"].shape), (2, 3))
                    self.assertNotIn("turn", out)
                    self.assertTrue(all(torch.isfinite(value).all() for value in out.values()))
                self.assertTrue(all(torch.equal(value, before[key]) for key, value in model.state_dict().items()))

    def test_classification_macro_f1_includes_absent_classes(self):
        from interview_robot.meld_training import classification_report
        result = classification_report([0, 0], [0, 0], ["a", "b"])
        self.assertEqual(result["accuracy"], 1)
        self.assertEqual(result["macro_f1"], .5)
        self.assertEqual(result["weighted_f1"], 1)

    def test_checkpoint_evaluation_all_scenarios_without_training(self):
        from interview_robot.meld_training import configure, evaluate_checkpoint, ROOT
        from interview_robot.meld_data import sha256
        from interview_robot.models.state_model import MultimodalStateModel
        config = configure(ROOT / "configs/meld_train.json", device="cpu")
        config["feature_dir"], config["protocol"] = str(self.root), str(self.root / "protocol.json")
        config["model"]["input_dims"] = self.dims
        rows = json.loads(json.dumps(self.rows).replace("meld:train:", "meld:test:").replace('"split": "train"', '"split": "test"'))
        (self.root / "test.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        (self.root / "test.extraction.json").write_text(json.dumps({"count": 3, "limited_debug_subset": False}), encoding="utf-8")
        (self.root / "protocol.json").write_text('{"excluded_groups": []}', encoding="utf-8")
        model = MultimodalStateModel(**config["model"]).eval()
        norm = {m: {k: v.tolist() for k, v in stat.items()} for m, stat in self.norm.items()}
        checkpoint = {"config": config, "model": model.state_dict(), "normalization": norm,
                      "provenance": {"feature_provenance_sha256": sha256(self.root / "provenance.json"),
                                     "protocol_sha256": sha256(self.root / "protocol.json")}}
        torch.save(checkpoint, self.root / "random.pt")
        with patch("interview_robot.meld_training.COUNTS", {"test": 3}), patch("torch.optim.AdamW", side_effect=AssertionError):
            report = evaluate_checkpoint(self.root / "random.pt", self.root / "evaluation", device="cpu")
        self.assertEqual(len(report["scenarios"]), 8)
        self.assertEqual(report["count"], 3)
        self.assertTrue((self.root / "evaluation/evaluation.json").exists())
        with patch("interview_robot.meld_training.COUNTS", {"test": 3}), patch("torch.optim.AdamW", side_effect=AssertionError):
            stressed = evaluate_checkpoint(self.root / "random.pt", self.root / "stress", device="cpu", quality_stress=True)
        self.assertEqual(len(stressed['scenarios']), 11)
        self.assertEqual(stressed['scenarios']['clean'], report['scenarios']['clean'])
        self.assertTrue(stressed['quality_protocol']['enabled'])
        self.assertTrue((self.root / 'stress/quality_rotated.predictions.json').exists())


if __name__ == "__main__":
    unittest.main()

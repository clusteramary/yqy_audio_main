import copy
import unittest

from interview_robot.train import validate_feature_splits


class FeatureSplitTests(unittest.TestCase):
    def setUp(self):
        self.train = {"schema_version": 1, "task": "ranking", "id": "1",
                      "dataset": "fixture", "group_id": "g1", "split": "train", "feature_source": "test"}

    def test_feature_group_leakage_rejected(self):
        val = dict(self.train, id="2", split="dev")
        with self.assertRaisesRegex(ValueError, "leakage"):
            validate_feature_splits([self.train], [val], "ranking")

    def test_test_set_cannot_select_checkpoint(self):
        val = dict(self.train, id="2", group_id="g2", split="test")
        with self.assertRaisesRegex(ValueError, "partitions"):
            validate_feature_splits([self.train], [val], "ranking")

    def test_disjoint_validation_is_accepted(self):
        val = dict(self.train, id="2", group_id="g2", split="dev")
        validate_feature_splits([self.train], [val], "ranking")

    def test_feature_source_required(self):
        self.train["feature_source"] = ""
        val = dict(self.train, id="2", group_id="g2", split="dev")
        with self.assertRaisesRegex(ValueError, "provenance"):
            validate_feature_splits([self.train], [val], "ranking")

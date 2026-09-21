import copy
import json
import math
from pathlib import Path
import tempfile
import unittest

from interview_robot.data import read_jsonl, validate_observation, validate_ranking, validate_splits, write_jsonl
from interview_robot.datasets import fqbank_record
from interview_robot.metrics import classification_metrics, ranking_metrics
from interview_robot.ranking import LexicalRanker
from interview_robot.runtime import InterviewController, LegacyKeywordAdapter
from interview_robot.state import InteractionState, RuleStateEstimator

ROOT = Path(__file__).resolve().parents[1]


def observation(t, speaking=False, text="answer", final=True, utterance="u", playing=False):
    return {"schema_version": 1, "session_id": "s", "utterance_id": utterance,
            "timestamp": t, "robot_playing": playing,
            "modalities": {"audio": {"timestamp": t, "confidence": 1.0, "speaking": speaking},
                           "text": {"timestamp": t, "confidence": 1.0, "text": text, "final": final}}}


def state(t, turn="SHIFT", utterance="u"):
    return InteractionState("s", utterance, t, None, turn, "unknown", (), {}, "test")


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.row = read_jsonl(ROOT / "data/examples/ranking.jsonl")[0]

    def test_examples_valid(self):
        validate_ranking(self.row)
        for row in read_jsonl(ROOT / "data/examples/observations.jsonl"):
            validate_observation(row)

    def test_reject_duplicate_candidate(self):
        self.row["candidates"].append(copy.deepcopy(self.row["candidates"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_ranking(self.row)

    def test_reject_no_positive(self):
        for q in self.row["candidates"]:
            q["relevance"] = 0
        with self.assertRaisesRegex(ValueError, "relevant"):
            validate_ranking(self.row)

    def test_reject_future_and_nan(self):
        row = observation(1)
        row["modalities"]["audio"]["timestamp"] = 2
        with self.assertRaises(ValueError):
            validate_observation(row)
        row = observation(1)
        row["modalities"]["audio"]["confidence"] = math.nan
        with self.assertRaises(ValueError):
            validate_observation(row)

    def test_missing_knowledge_source_rejected(self):
        del self.row["knowledge_source"]
        with self.assertRaises(ValueError):
            validate_ranking(self.row)

    def test_cross_split_leakage(self):
        other = copy.deepcopy(self.row)
        other.update(id="other-id", split="test", history=["different text"])
        with tempfile.TemporaryDirectory() as temp:
            a, b = Path(temp) / "a.jsonl", Path(temp) / "b.jsonl"
            write_jsonl(a, [self.row])
            write_jsonl(b, [other])
            with self.assertRaisesRegex(ValueError, "group leakage"):
                validate_splits([str(a), str(b)])

    def test_duplicate_context_with_different_group_rejected(self):
        other = copy.deepcopy(self.row)
        other.update(id="other-id", group_id="other", split="test")
        with tempfile.TemporaryDirectory() as temp:
            a, b = Path(temp) / "a.jsonl", Path(temp) / "b.jsonl"
            write_jsonl(a, [self.row])
            write_jsonl(b, [other])
            with self.assertRaisesRegex(ValueError, "context"):
                validate_splits([str(a), str(b)])


class StateTests(unittest.TestCase):
    def test_partial_transcript_holds_during_pause(self):
        model = RuleStateEstimator()
        model.predict(observation(0, speaking=True))
        self.assertEqual(model.predict(observation(1, final=False)).turn, "HOLD")

    def test_shift_needs_observed_speech_and_silence(self):
        model = RuleStateEstimator()
        self.assertEqual(model.predict(observation(0)).turn, "HOLD")
        self.assertEqual(model.predict(observation(0.2, speaking=True)).turn, "HOLD")
        self.assertEqual(model.predict(observation(0.5)).turn, "HOLD")
        self.assertEqual(model.predict(observation(1.1)).turn, "SHIFT")

    def test_missing_audio_does_not_count_as_silence(self):
        model = RuleStateEstimator()
        model.predict(observation(0, speaking=True))
        row = observation(1)
        del row["modalities"]["audio"]
        self.assertEqual(model.predict(row).turn, "IDLE")

    def test_stale_visual_is_unknown(self):
        row = observation(5)
        row["modalities"]["visual"] = {"timestamp": 1, "confidence": 1, "speaker_id": "old", "affect": "positive"}
        result = RuleStateEstimator().predict(row)
        self.assertIsNone(result.speaker_id)
        self.assertEqual(result.affect, "unknown")

    def test_playback_overrides_user_speech(self):
        self.assertEqual(RuleStateEstimator().predict(observation(1, speaking=True, playing=True)).turn, "ROBOT")

    def test_causal_order_and_session_isolation(self):
        model = RuleStateEstimator()
        model.predict(observation(1, speaking=True))
        with self.assertRaisesRegex(ValueError, "increase"):
            model.predict(observation(1))
        other = observation(0)
        other["session_id"] = "another"
        self.assertEqual(model.predict(other).turn, "HOLD")

    def test_no_ground_truth_read_by_inference(self):
        a, b = observation(1), observation(1)
        a["labels"], b["labels"] = {"turn": "HOLD"}, {"turn": "SHIFT"}
        self.assertEqual(RuleStateEstimator().predict(a), RuleStateEstimator().predict(b))


class RankingTests(unittest.TestCase):
    def test_query_overlap_and_empty_knowledge(self):
        ranker = LexicalRanker()
        candidates = [{"id": "x", "text": "career growth"}, {"id": "y", "text": "dinner menu"}]
        self.assertEqual(ranker.rank(["career"], [], candidates)[0]["id"], "x")

    def test_tie_order_independent_of_input_position(self):
        candidates = [{"id": "z", "text": "apple"}, {"id": "a", "text": "orange"}]
        ranker = LexicalRanker()
        self.assertEqual(ranker.rank(["unknown"], [], candidates),
                         ranker.rank(["unknown"], [], list(reversed(candidates))))

    def test_labels_do_not_affect_ranking(self):
        row = read_jsonl(ROOT / "data/examples/ranking.jsonl")[0]
        before = LexicalRanker().rank(row["history"], row["knowledge"], row["candidates"])
        for item in row["candidates"]:
            item["relevance"] = 99
        self.assertEqual(before, LexicalRanker().rank(row["history"], row["knowledge"], row["candidates"]))

    def test_multi_positive_recall_and_mrr(self):
        m = ranking_metrics(["c", "a", "b"], {"a": 1, "b": 1, "c": 0})
        self.assertEqual(m["MRR"], 0.5)
        self.assertEqual(m["Recall@1"], 0)
        self.assertEqual(m["Recall@3"], 1)
        self.assertTrue(0 < m["nDCG@3"] < 1)

    def test_perfect_ndcg(self):
        self.assertEqual(ranking_metrics(["a", "b"], {"a": 3, "b": 1})["nDCG@3"], 1)

    def test_unknown_prediction_is_counted_wrong(self):
        result = classification_metrics(["positive"], ["unknown"], ["positive", "neutral", "negative"])
        self.assertEqual(result["accuracy"], 0)
        self.assertEqual(result["macro_f1"], 0)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.controller = InterviewController(LexicalRanker())
        self.questions = [{"id": "q1", "text": "career growth"}, {"id": "q2", "text": "career learning"}]

    def step(self, value):
        return self.controller.step(value, ["career"], [], self.questions)

    def test_no_interrupt_and_exactly_once(self):
        self.assertEqual(self.step(state(0, "HOLD")).action, "LISTEN")
        self.assertEqual(self.step(state(1)).action, "ASK")
        self.assertEqual(self.step(state(2)).action, "WAIT")

    def test_playback_ack_and_cooldown(self):
        self.step(state(0))
        self.step(state(1, "ROBOT"))
        self.assertEqual(self.step(state(2)).action, "WAIT")
        self.assertEqual(self.step(state(2.6)).action, "LISTEN")
        self.assertEqual(self.step(state(3, utterance="u2")).action, "ASK")

    def test_no_repeat_when_candidate_id_changes(self):
        first = self.step(state(0))
        self.controller.cancel_pending()
        self.questions = [{"id": "different", "text": first.question}]
        self.assertEqual(self.step(state(1, utterance="u2")).action, "WAIT")

    def test_dry_run_protocol_has_legacy_shape(self):
        result = LegacyKeywordAdapter().send("nod")
        self.assertFalse(result["sent"])
        self.assertEqual(result["index"], 8)
        self.assertEqual(result["payload"]["type"], "voice_keyword")
        with self.assertRaises(ValueError):
            LegacyKeywordAdapter().send("unknown")


class AdapterTests(unittest.TestCase):
    def test_fqbank_dedup_and_no_future_answer_leakage(self):
        raw = {"id": {"dialogue": 1, "turn": 2},
               "dialog_history": [{"utterance": "past Q", "response": "past A"}],
               "current_utterance": "current Q", "current_response": "current A",
               "candidate_utterances": {"valid": [{"utterance": "next Q", "response": "FUTURE SECRET"}],
                                        "invalid": [{"utterance": "wrong"}, {"utterance": "wrong"}]}}
        converted, duplicates = fqbank_record(raw, "dev")
        self.assertEqual(duplicates, 1)
        self.assertEqual(len(converted["candidates"]), 2)
        self.assertNotIn("FUTURE SECRET", json.dumps(converted))
        self.assertEqual(converted["knowledge"], [])

    def test_conflicting_positive_negative_rejected(self):
        raw = {"id": {"dialogue": 1, "turn": 2}, "dialog_history": [],
               "current_utterance": "Q", "current_response": "A",
               "candidate_utterances": {"valid": [{"utterance": "same"}], "invalid": [{"utterance": "same"}]}}
        with self.assertRaisesRegex(ValueError, "conflicting"):
            fqbank_record(raw, "dev")


if __name__ == "__main__":
    unittest.main()

import copy
import random
from collections import defaultdict

import numpy as np
import pytest

from interview_robot.followup_data import TextIndex, norm
from interview_robot.followup_protocol import build_candidates
from interview_robot.research_audit import membership_audit
from interview_robot.research_metrics import probability_report, paired_ranking_interval


def test_donor_negatives_exclude_whole_dialogue():
    pool = [f"question {i}?" for i in range(20)]
    owners = {norm(t): {"own" if i < 3 else f"other-{i}"} for i, t in enumerate(pool)}
    row = {"id": "dev:one", "group_id": "own", "history": ["context"],
           "candidates": [{"text": pool[0], "relevance": 1}]}
    for hard in (0, 4):
        candidates = build_candidates(row, pool, owners, TextIndex(pool), hard)
        assert len(candidates) == 10
        assert sum(c["relevance"] for c in candidates) == 1
        assert all("own" not in owners[norm(c["text"])] for c in candidates if c["relevance"] == 0)
        assert candidates == build_candidates(row, pool, owners, TextIndex(pool), hard)


def test_shortcut_audit_never_opens_test_metrics():
    with pytest.raises(ValueError, match="sealed"):
        membership_audit([{"split": "train"}], [{"split": "test"}])


def test_probability_scores_and_tied_coverage():
    perfect = probability_report([0, 1], [[1, 0], [0, 1]])
    assert perfect["ece"] == perfect["brier_multiclass_sum"] == perfect["nll"] == 0
    assert perfect["risk_coverage"][0]["actual_coverage"] == 1
    uncertain = probability_report([0, 1], [[.5, .5], [.5, .5]])
    assert uncertain["nll"] == pytest.approx(np.log(2))
    assert uncertain["brier_multiclass_sum"] == .5
    with pytest.raises(ValueError, match="sum to one"):
        probability_report([0], [[.5, .7]])


def test_paired_intervals_keep_groups_and_align_ids():
    rows = [{"id": str(i), "dataset": "fixture", "group_id": str(i // 2),
             "candidates": [{"id": "yes", "relevance": 1}, {"id": "no", "relevance": 0}]} for i in range(4)]
    a = [{"id": str(i), "ranked": [{"id": "no"}, {"id": "yes"}]} for i in range(4)]
    b = [{"id": str(i), "ranked": [{"id": "yes"}, {"id": "no"}]} for i in range(4)]
    result = paired_ranking_interval(rows, list(reversed(a)), b, repetitions=100)
    assert result["groups"] == 2
    assert result["difference_b_minus_a"] == .5
    assert result["paired_cluster_percentile_ci95"] == [.5, .5]
    assert paired_ranking_interval(rows, a, a, repetitions=100)["paired_cluster_percentile_ci95"] == [0, 0]
    with pytest.raises(ValueError, match="exactly"):
        paired_ranking_interval(rows, a[:-1], b)


def test_history_position_breaks_set_invariance_but_ignores_padding():
    torch = pytest.importorskip("torch")
    from interview_robot.models.ranker import KnowledgeRanker
    torch.manual_seed(7)
    model = KnowledgeRanker(8, hidden=8, heads=2, history_position=False).eval()
    h, hm = torch.randn(1, 3, 8), torch.ones(1, 3, dtype=torch.bool)
    tail = (torch.zeros(1, 0, 8), torch.zeros(1, 0, dtype=torch.bool),
            torch.randn(1, 2, 1, 8), torch.ones(1, 2, 1, dtype=torch.bool))
    with torch.no_grad():
        assert torch.allclose(model(h, hm, *tail), model(h.flip(1), hm, *tail), atol=1e-6)
        model.history_position = True
        ordered = model(h, hm, *tail)
        assert not torch.allclose(ordered, model(h.flip(1), hm, *tail), atol=1e-6)
        padded = torch.cat([h, torch.randn(1, 2, 8)], 1)
        mask = torch.cat([hm, torch.zeros(1, 2, dtype=torch.bool)], 1)
        assert torch.allclose(ordered, model(padded, mask, *tail), atol=1e-6)


def test_coverage_history_scope_and_eval_rng_independence():
    torch = pytest.importorskip("torch")
    from interview_robot.followup_training import scalar_features, collate
    row = {"history": ["old unique words"] + ["recent"] * 8, "knowledge": [],
           "candidates": [{"text": "old unique words", "relevance": 1}]}
    assert scalar_features(row) == scalar_features(dict(row, history=row["history"][-8:]))
    class Cache:
        dim = 8
        def get(self, texts):
            return torch.zeros(len(texts), 8)
    state = random.getstate()
    collate([row], Cache(), "cpu")
    assert state == random.getstate()


def test_shuffled_knowledge_never_uses_same_dialogue():
    pytest.importorskip("torch")
    from interview_robot.followup_training import perturb
    rows = [{"id": str(i), "group_id": str(i // 3), "knowledge": [str(i)]} for i in range(9)]
    expected = perturb(rows, "shuffled")
    by_id = {r["id"]: r for r in perturb(list(reversed(rows)), "shuffled")}
    assert all(r["knowledge_donor_group"] != r["group_id"] for r in expected)
    assert all(r["knowledge"] == by_id[r["id"]]["knowledge"] for r in expected)
    with pytest.raises(ValueError, match="two dialogue"):
        perturb(rows[:3], "shuffled")


def test_old_candidate_protocol_rejected_before_training(tmp_path):
    pytest.importorskip("torch")
    from interview_robot.followup_training import train
    from interview_robot.followup_data import save_json
    from interview_robot.data import write_jsonl
    for split in ("train", "dev"):
        write_jsonl(tmp_path / f"{split}.jsonl", [{"schema_version": 1,
            "dataset": "KdConv-followup-v1", "id": split, "group_id": split, "split": split,
            "history": [split], "knowledge": [],
            "candidates": [{"id": "a", "text": "yes", "relevance": 1}, {"id": "b", "text": "no", "relevance": 0}]}])
    save_json(tmp_path / "config.json", {"data": str(tmp_path), "model": {}})
    with pytest.raises(ValueError, match="confirmed candidate-membership shortcut"):
        train(tmp_path / "config.json", tmp_path / "rejected", device="cpu")
    assert not list((tmp_path / "rejected").iterdir())


def test_old_checkpoint_rejected_explicitly(tmp_path):
    torch = pytest.importorskip("torch")
    from interview_robot.followup_training import load_checkpoint
    torch.save({"model": {}}, tmp_path / "legacy.pt")
    with pytest.raises(ValueError, match="checkpoint protocol differs"):
        load_checkpoint(tmp_path / "legacy.pt", "cpu")

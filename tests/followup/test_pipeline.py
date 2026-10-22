"""Optional ML tests; no optimizer steps or corpus training (pytest only)."""
import copy
import random
import numpy as np
import pytest

from interview_robot.followup_data import KnowledgeIndex, TextIndex, clean_splits, digest, file_sha, save_json


def test_lazy_sampling_preserves_full_permutation():
    from interview_robot.followup_data import sample_stream
    for size in (0, 1, 10, 1000):
        items = list(range(size))
        assert list(sample_stream(items, random.Random(42))) == random.Random(42).sample(items, len(items))


def test_vectorized_retrieval_matches_exact_overlap():
    from interview_robot.ranking import tokenize
    texts = ["a b", "a c", "x y", "a b c", "a", ""]
    index = TextIndex(texts)
    for query in ("a b", "a", "missing", ""):
        q = set(tokenize(query))
        expected = sorted([i for i, t in enumerate(index.tokens) if q & t],
            key=lambda i: (-len(q & index.tokens[i]) / len(q | index.tokens[i]), digest(texts[i])))[:3]
        assert index.top(query, 3) == expected


def test_retrieval_uses_only_observed_text():
    kb = KnowledgeIndex({"张三": [["张三", "职业", "演员"]], "李四": [["李四", "职业", "导演"]]})
    assert kb.retrieve(["张三是谁？"]) == ["张三 | 职业 | 演员"]
    assert kb.retrieve(["你好"]) == []


def test_shared_prefix_quarantines_entire_dialogue():
    def row(group, history):
        return {"group_id": group, "history": history}
    parts = {"train": [row("train:0", ["你好Ａ", "世界"])],
             "dev": [row("dev:0", ["你好A", "世 界"]), row("dev:0", ["独立内容", "仍属于同组"])],
             "test": [row("test:0", ["不同", "历史"])]}
    clean, excluded = clean_splits(parts)
    assert not clean["dev"] and len(clean["test"]) == 1
    assert excluded[0]["rows"] == 2


def sample():
    return {"id": "sample", "history": ["what is music", "a form of art"], "knowledge": ["music uses sound"],
            "candidates": [{"id": "a", "text": "what is music", "relevance": 0},
                           {"id": "b", "text": "who makes music", "relevance": 1}]}


def training_module():
    pytest.importorskip("torch")
    from interview_robot import followup_training
    return followup_training


def test_scalar_features_do_not_read_labels():
    f = training_module()
    a = sample()
    b = copy.deepcopy(a)
    for c in b["candidates"]:
        c["relevance"] = 1 - c["relevance"]
        c["source"] = "future gold annotation must not be used"
    assert f.scalar_features(a) == f.scalar_features(b)


def test_random_expectation_matches_enumeration():
    import itertools
    from interview_robot.metrics import average_metrics, ranking_metrics
    f = training_module()
    for relevance in ([0, 1], [1, 0, 2], [1, 1, 1]):
        scores = dict(enumerate(relevance))
        expected = average_metrics([ranking_metrics(list(p), scores) for p in itertools.permutations(scores)])
        assert f.random_metrics(relevance) == pytest.approx(expected)


def test_forward_masks_permutation_and_knowledge_absence():
    f = training_module()
    import torch
    class FakeCache:
        dim = 8
        def get(self, texts):
            return torch.tensor([[int(digest(t)[i:i+2], 16) / 255 for i in range(0, 16, 2)] for t in texts])
    row = sample()
    cache = FakeCache()
    args, labels = f.collate([row], cache, "cpu")
    model = f.FollowupRanker(8, hidden=8, heads=2).eval()
    with torch.no_grad():
        scores = model(*args)
        assert torch.isfinite(f.listwise_loss(scores, labels))
        reversed_row = dict(row, candidates=list(reversed(row["candidates"])))
        args2, _ = f.collate([reversed_row], cache, "cpu")
        assert torch.allclose(model(*args2), scores.flip(1), atol=1e-6)
        dropped, _ = f.collate([row], cache, "cpu", knowledge_dropout=1.0)
        missing, _ = f.collate([dict(row, knowledge=[])], cache, "cpu")
        assert all(torch.equal(x, y) for x, y in zip(dropped, missing))
        assert torch.isfinite(model(*missing)).all()
        shorter = dict(row, candidates=row["candidates"][:1], knowledge=[])
        padded, _ = f.collate([row, shorter], cache, "cpu")
        output = model(*padded)
        assert torch.isneginf(output[1, 1])


def test_cache_rejects_modified_vectors(tmp_path):
    f = training_module()
    np.save(tmp_path / "vectors.npy", np.zeros((1, 8), dtype=np.float32))
    save_json(tmp_path / "texts.json", ["hello"])
    save_json(tmp_path / "manifest.json", {"texts_sha256": file_sha(tmp_path / "texts.json"),
        "vectors_sha256": file_sha(tmp_path / "vectors.npy")})
    assert f.Cache(tmp_path).dim == 8
    np.save(tmp_path / "vectors.npy", np.ones((1, 8), dtype=np.float32))
    with pytest.raises(ValueError, match="checksum"):
        f.Cache(tmp_path)


def test_missing_knowledge_perturbation_leaves_source_unchanged():
    f = training_module()
    source = [sample()]
    changed = f.perturb(source, "missing")
    assert not changed[0]["knowledge"] and source[0]["knowledge"]

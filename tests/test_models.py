import unittest

try:
    import torch
except ImportError:
    torch = None

if torch is not None:
    from interview_robot.models.ranker import KnowledgeRanker, listwise_loss
    from interview_robot.models.state_model import MultimodalStateModel, partial_state_loss


@unittest.skipIf(torch is None, "optional torch not installed in this environment")
class ModelTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(7)
        torch.set_num_threads(1)

    def test_state_missing_modality_values_cannot_affect_output(self):
        model = MultimodalStateModel({"audio": 8, "text": 8}, modality_dropout=0).eval()
        mask = torch.tensor([[[True, False]] * 3])
        confidence, age = torch.ones(1, 3, 2), torch.zeros(1, 3, 2)
        a = {"audio": torch.randn(1, 3, 8), "text": torch.zeros(1, 3, 8)}
        b = {**a, "text": torch.full((1, 3, 8), float("nan"))}
        self.assertTrue(torch.allclose(model(a, mask, confidence, age)["turn"],
                                       model(b, mask, confidence, age)["turn"]))

    def test_all_missing_state_backward_is_finite(self):
        model = MultimodalStateModel({"audio": 8}, modality_dropout=0)
        output = model({"audio": torch.randn(2, 3, 8)}, torch.zeros(2, 3, 1, dtype=torch.bool),
                       torch.ones(2, 3, 1), torch.zeros(2, 3, 1))
        loss = partial_state_loss(output, {"turn": torch.tensor([0, 1]), "affect": torch.tensor([-100, -100])})
        loss.backward()
        self.assertTrue(torch.isfinite(loss))
        self.assertTrue(all(torch.isfinite(p.grad).all() for p in model.parameters() if p.grad is not None))

    def test_padding_does_not_change_last_valid_state(self):
        model = MultimodalStateModel({"audio": 8}, modality_dropout=0).eval()
        values = torch.randn(1, 3, 8)
        first = model({"audio": values}, torch.ones(1, 3, 1, dtype=torch.bool),
                      torch.ones(1, 3, 1), torch.zeros(1, 3, 1))["turn"]
        padded = torch.cat((values, torch.randn(1, 2, 8)), dim=1)
        second = model({"audio": padded}, torch.ones(1, 5, 1, dtype=torch.bool),
                       torch.ones(1, 5, 1), torch.zeros(1, 5, 1), lengths=torch.tensor([3]))["turn"]
        self.assertTrue(torch.allclose(first, second, atol=1e-6))

    def test_ranker_missing_knowledge_and_masked_candidates(self):
        model = KnowledgeRanker(feature_dim=8).eval()
        h, hm = torch.randn(1, 3, 8), torch.ones(1, 3, dtype=torch.bool)
        q = torch.randn(1, 3, 2, 8)
        qm = torch.tensor([[[True, True], [True, False], [False, False]]])
        km = torch.zeros(1, 2, dtype=torch.bool)
        scores = model(h, hm, torch.full((1, 2, 8), float("nan")), km, q, qm)
        self.assertTrue(torch.isfinite(scores[0, :2]).all())
        self.assertTrue(torch.isneginf(scores[0, 2]))
        loss = listwise_loss(scores, torch.tensor([[1, 0, -1]]))
        loss.backward()
        self.assertTrue(torch.isfinite(loss))
        self.assertTrue(all(torch.isfinite(p.grad).all() for p in model.parameters() if p.grad is not None))

    def test_no_knowledge_exact_dialogue_fallback(self):
        model = KnowledgeRanker(feature_dim=8).eval()
        args = (torch.randn(1, 3, 8), torch.ones(1, 3, dtype=torch.bool),
                torch.zeros(1, 0, 8), torch.zeros(1, 0, dtype=torch.bool),
                torch.randn(1, 2, 2, 8), torch.ones(1, 2, 2, dtype=torch.bool))
        with_k = model(*args)
        model.use_knowledge = False
        self.assertTrue(torch.allclose(with_k, model(*args)))

    def test_listwise_multi_positive_and_padding(self):
        scores = torch.tensor([[0.0, 0.0, -torch.inf]], requires_grad=True)
        loss = listwise_loss(scores, torch.tensor([[1, 1, -1]]))
        self.assertAlmostEqual(loss.item(), 0.693147, places=5)
        loss.backward()
        self.assertTrue(torch.isfinite(scores.grad).all())
        with self.assertRaises(ValueError):
            listwise_loss(torch.zeros(1, 2), torch.zeros(1, 2))


if __name__ == "__main__":
    unittest.main()

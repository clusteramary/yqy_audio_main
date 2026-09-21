"""Run tiny CPU optimizer steps for BOTH models. Synthetic data is not evidence."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch

from interview_robot.models.ranker import KnowledgeRanker, listwise_loss
from interview_robot.models.state_model import MultimodalStateModel, partial_state_loss


def main():
    torch.manual_seed(42)
    torch.set_num_threads(1)
    state = MultimodalStateModel({"audio": 8, "text": 8, "visual": 8}, modality_dropout=0.0)
    optimizer = torch.optim.Adam(state.parameters(), lr=0.001)
    features = {name: torch.randn(2, 4, 8) for name in ("audio", "text", "visual")}
    mask = torch.ones(2, 4, 3, dtype=torch.bool)
    mask[1] = False
    output = state(features, mask, torch.ones(2, 4, 3), torch.zeros(2, 4, 3))
    loss = partial_state_loss(output, {"turn": torch.tensor([0, 1]), "affect": torch.tensor([2, -100])})
    loss.backward()
    assert torch.isfinite(loss) and all(torch.isfinite(p.grad).all() for p in state.parameters() if p.grad is not None)
    optimizer.step()
    print("state: CPU optimizer step passed, including all-missing example")
    ranker = KnowledgeRanker(feature_dim=8)
    optimizer = torch.optim.Adam(ranker.parameters(), lr=0.001)
    scores = ranker(torch.randn(2, 4, 8), torch.ones(2, 4, dtype=torch.bool),
                    torch.randn(2, 3, 8), torch.zeros(2, 3, dtype=torch.bool),
                    torch.randn(2, 3, 2, 8), torch.tensor([[[True, True]] * 2 + [[False, False]]] * 2))
    loss = listwise_loss(scores, torch.tensor([[1, 0, -1], [0, 1, -1]]))
    loss.backward()
    assert torch.isfinite(loss) and all(torch.isfinite(p.grad).all() for p in ranker.parameters() if p.grad is not None)
    optimizer.step()
    print("ranker: CPU optimizer step passed, including missing knowledge and padding")
    print("Development smoke check only; no research performance claim.")


if __name__ == "__main__":
    main()

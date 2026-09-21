"""Question-conditioned dual-path attention over cached token embeddings."""
from __future__ import annotations

import torch
from torch import nn


class KnowledgeRanker(nn.Module):
    def __init__(self, feature_dim: int = 32, hidden: int = 32, heads: int = 4,
                 use_knowledge: bool = True, use_gate: bool = True):
        super().__init__()
        if hidden % heads:
            raise ValueError("hidden must be divisible by heads")
        self.projection = nn.Linear(feature_dim, hidden)
        self.history_attention = nn.MultiheadAttention(hidden, heads, batch_first=True)
        self.knowledge_attention = nn.MultiheadAttention(hidden, heads, batch_first=True)
        self.gate = nn.Linear(hidden * 3, 1)
        self.score = nn.Sequential(nn.Linear(hidden * 2, hidden), nn.Tanh(), nn.Linear(hidden, 1))
        self.use_knowledge, self.use_gate = use_knowledge, use_gate

    def _project(self, features, mask):
        if mask.dtype != torch.bool or features.shape[:-1] != mask.shape:
            raise ValueError("feature/mask shape mismatch")
        features = torch.where(mask.unsqueeze(-1), features, torch.zeros_like(features))
        if not torch.isfinite(features).all():
            raise ValueError("non-finite observed feature")
        return self.projection(features) * mask.unsqueeze(-1)

    def _attend(self, attention, query, values, mask):
        # The zero sentinel is exposed only if the entire source is missing.
        has_source = mask.any(dim=1)
        zeros = torch.zeros(values.shape[0], 1, values.shape[-1], device=values.device, dtype=values.dtype)
        values = torch.cat((values, zeros), dim=1)
        mask = torch.cat((mask, ~has_source.unsqueeze(1)), dim=1)
        result, _ = attention(query, values, values, key_padding_mask=~mask, need_weights=False)
        return result * has_source[:, None, None], has_source

    def forward(self, history, history_mask, knowledge, knowledge_mask, questions, question_mask):
        # history/knowledge: [B,L,D]; questions: [B,C,Q,D].
        if questions.ndim != 4:
            raise ValueError("questions must have shape [B,C,Q,D]")
        q_tokens = self._project(questions, question_mask)
        candidate_mask = question_mask.any(dim=-1)
        if not candidate_mask.any(dim=1).all():
            raise ValueError("each example needs an available candidate")
        q = q_tokens.sum(dim=2) / question_mask.sum(dim=2, keepdim=True).clamp(min=1)
        h = self._project(history, history_mask)
        d, _ = self._attend(self.history_attention, q, h, history_mask)
        if self.use_knowledge:
            k = self._project(knowledge, knowledge_mask)
            k, has_k = self._attend(self.knowledge_attention, q, k, knowledge_mask)
            weight = torch.sigmoid(self.gate(torch.cat((q, d, k), dim=-1))) if self.use_gate else torch.full_like(d[:, :, :1], 0.5)
            weight = torch.where(has_k[:, None, None], weight, torch.ones_like(weight))
            fused = weight * d + (1 - weight) * k
        else:
            fused = d
        scores = self.score(torch.cat((q, fused), dim=-1)).squeeze(-1)
        return scores.masked_fill(~candidate_mask, -torch.inf)


def listwise_loss(scores, relevance):
    """Supports multiple positives, graded relevance and padded (-1) labels."""
    if scores.shape != relevance.shape or scores.ndim != 2:
        raise ValueError("scores and relevance must be [B,C]")
    valid = relevance >= 0
    if ((relevance > 3) | (relevance < -1)).any():
        raise ValueError("relevance must be -1 or 0..3")
    if not torch.isfinite(scores[valid]).all():
        raise ValueError("labeled candidates must have finite scores")
    gains = (torch.pow(2.0, relevance.clamp(min=0)) - 1) * valid
    if not (gains.sum(dim=1) > 0).all():
        raise ValueError("each sample needs positive relevance")
    target = gains / gains.sum(dim=1, keepdim=True)
    log_probs = torch.log_softmax(scores.masked_fill(~valid, -torch.inf), dim=1)
    log_probs = log_probs.masked_fill(~valid, 0)
    return -(target * log_probs).sum(dim=1).mean()

"""Prototype confidence/age gated fusion of cached encoder features."""
from __future__ import annotations

import torch
from torch import nn
from torch.nn import functional as F


class MultimodalStateModel(nn.Module):
    """Inputs are causal, aligned feature windows, NOT raw audio/video/text.

    features[name]: [B,T,D_name]; available/confidence/age: [B,T,M].
    Outputs predict the final valid timestep. Speaker change is a binary event,
    never a global person-ID classifier. HOLD/SHIFT labels are dataset-specific.
    """
    def __init__(self, input_dims: dict, hidden: int = 32, heads: int = 4,
                 modality_dropout: float = 0.1, use_gate: bool = True,
                 use_temporal: bool = True, output_heads: dict = None,
                 fusion: str = "attention"):
        super().__init__()
        if not input_dims or hidden % heads or not 0 <= modality_dropout < 1:
            raise ValueError("invalid modality dimensions, heads or dropout")
        self.names = tuple(input_dims)
        if fusion not in ("attention", "concat"):
            raise ValueError("fusion must be attention or concat")
        self.fusion = fusion
        self.project = nn.ModuleDict({name: nn.Linear(size, hidden) for name, size in input_dims.items()})
        self.gate = nn.ModuleDict({name: nn.Linear(hidden + 2, 1) for name in input_dims})
        if fusion == "attention":
            self.attention = nn.MultiheadAttention(hidden, heads, dropout=0.0, batch_first=True)
        else:
            self.concat = nn.Sequential(nn.Linear((hidden + 1) * len(input_dims), hidden), nn.ReLU())
        self.null_token = nn.Parameter(torch.zeros(1, 1, hidden))
        self.temporal = nn.GRU(hidden, hidden, batch_first=True)
        sizes = output_heads if output_heads is not None else {"turn": 2, "affect": 3, "speaker_change": 2}
        if not sizes or any(size < 2 for size in sizes.values()):
            raise ValueError("output heads must have at least two classes")
        self.heads = nn.ModuleDict({name: nn.Linear(hidden, size) for name, size in sizes.items()})
        self.modality_dropout = modality_dropout
        self.use_gate, self.use_temporal = use_gate, use_temporal

    def forward(self, features, available, confidence, age, lengths=None):
        if available.ndim != 3 or available.shape[-1] != len(self.names):
            raise ValueError("availability must have shape [B,T,M]")
        if available.dtype != torch.bool or confidence.shape != available.shape or age.shape != available.shape:
            raise ValueError("invalid mask/confidence/age shapes")
        if not torch.isfinite(confidence).all() or not ((confidence >= 0) & (confidence <= 1)).all():
            raise ValueError("confidence must be finite in [0,1]")
        if not torch.isfinite(age).all() or not (age >= 0).all():
            raise ValueError("age must be finite and nonnegative")
        mask = available.clone()
        if self.training and self.modality_dropout:
            mask &= torch.rand(mask.shape, device=mask.device) >= self.modality_dropout
        projected, weights = [], []
        for index, name in enumerate(self.names):
            values = features[name]
            if values.shape[:2] != mask.shape[:2] or values.shape[-1] != self.project[name].in_features:
                raise ValueError(f"invalid feature shape: {name}")
            valid = mask[:, :, index:index + 1]
            # Replace unavailable features BEFORE linear projection (including NaNs).
            values = torch.where(valid, values, torch.zeros_like(values))
            if not torch.isfinite(values).all():
                raise ValueError("non-finite observed features")
            h = self.project[name](values)
            c, a = confidence[:, :, index:index + 1], age[:, :, index:index + 1]
            weight = torch.sigmoid(self.gate[name](torch.cat((h, c, a), dim=-1))) if self.use_gate else torch.ones_like(c)
            if self.use_gate:
                weight = weight * c * torch.exp(-a)
            weight = weight * valid
            projected.append(h * weight)
            weights.append(weight)
        x = torch.stack(projected, dim=2)
        batch, steps, modalities, width = x.shape
        if self.fusion == "concat":
            fused = self.concat(torch.cat((x.flatten(2), mask.to(x.dtype)), dim=-1))
        else:
            x = x.reshape(batch * steps, modalities, width)
            # An always-valid null token prevents all-missing attention softmax NaNs.
            x = torch.cat((x, self.null_token.expand(batch * steps, -1, -1)), dim=1)
            valid = torch.cat((mask.reshape(batch * steps, modalities),
                               torch.ones(batch * steps, 1, dtype=torch.bool, device=x.device)), dim=1)
            x, _ = self.attention(x, x, x, key_padding_mask=~valid, need_weights=False)
            fused = (x * valid.unsqueeze(-1)).sum(1) / valid.sum(1, keepdim=True)
            fused = fused.reshape(batch, steps, width)
        if lengths is None:
            lengths = torch.full((batch,), steps, dtype=torch.long, device=x.device)
        if lengths.shape != (batch,) or not ((lengths > 0) & (lengths <= steps)).all():
            raise ValueError("invalid sequence lengths")
        if self.use_temporal:
            packed = nn.utils.rnn.pack_padded_sequence(fused, lengths.cpu(), batch_first=True,
                                                      enforce_sorted=False)
            _, hidden = self.temporal(packed)
            result = hidden[-1]
        else:
            result = fused[torch.arange(batch, device=x.device), lengths - 1]
        output = {name: head(result) for name, head in self.heads.items()}
        output["gates"] = torch.cat(weights, dim=-1)
        return output


def partial_state_loss(output, labels):
    """Only existing task labels train a head. Missing labels must be -100."""
    total = next(value for key, value in output.items() if key != "gates").sum() * 0
    active = 0
    for task in output:
        if task == "gates" or task not in labels:
            continue
        target = labels[task]
        valid = target != -100
        if valid.any():
            total = total + F.cross_entropy(output[task][valid], target[valid])
            active += 1
    return total / max(active, 1)

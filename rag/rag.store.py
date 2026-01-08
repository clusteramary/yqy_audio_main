# rag_store.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

try:
    import faiss  # faiss-cpu / faiss-gpu
except Exception as e:
    raise RuntimeError("faiss not installed. pip install faiss-cpu") from e

try:
    from sentence_transformers import SentenceTransformer
except Exception as e:
    raise RuntimeError(
        "sentence-transformers not installed. pip install sentence-transformers"
    ) from e


@dataclass
class RagHit:
    idx: int
    score: float
    text: str


def _chunk_text(text: str, chunk_chars: int = 520, overlap: int = 80) -> List[str]:
    """
    简单稳妥的中文分块：按长度滑窗，尽量不断句（这里用换行优先切）。
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 先按空行/单行做粗切
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    if not paras:
        return []

    merged = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 1 <= chunk_chars:
            buf = (buf + "\n" + p).strip() if buf else p
        else:
            if buf:
                merged.append(buf)
            buf = p
    if buf:
        merged.append(buf)

    # 再做滑窗 overlap（避免刚好断在关键处）
    chunks = []
    for m in merged:
        if len(m) <= chunk_chars:
            chunks.append(m)
            continue
        s = 0
        while s < len(m):
            e = min(len(m), s + chunk_chars)
            chunks.append(m[s:e])
            if e == len(m):
                break
            s = max(0, e - overlap)
    return chunks


class TextRAG:
    def __init__(
        self,
        txt_path: Path,
        embed_model: str = "BAAI/bge-small-zh-v1.5",
        chunk_chars: int = 520,
        overlap: int = 80,
    ):
        base_dir = Path(__file__).resolve().parent
        self.txt_path = Path(txt_path) if txt_path else (base_dir / "ai.txt")

        self.embed_model_name = embed_model
        self.chunk_chars = chunk_chars
        self.overlap = overlap

        self.model: Optional[SentenceTransformer] = None
        self.index = None
        self.chunks: List[str] = []

    def build(self) -> None:
        raw = self.txt_path.read_text(encoding="utf-8", errors="ignore")
        self.chunks = _chunk_text(raw, self.chunk_chars, self.overlap)
        if not self.chunks:
            raise RuntimeError(f"No chunks built from {self.txt_path}")

        self.model = SentenceTransformer(self.embed_model_name)
        emb = self.model.encode(
            self.chunks,
            normalize_embeddings=True,  # 归一化后 inner product = cosine
            batch_size=64,
            show_progress_bar=True,
        ).astype(np.float32)

        dim = emb.shape[1]
        self.index = faiss.IndexFlatIP(dim)
        self.index.add(emb)

    def retrieve(
        self, query: str, top_k: int = 4, min_score: float = 0.25
    ) -> List[RagHit]:
        if self.model is None or self.index is None:
            raise RuntimeError("RAG not built. Call build() at startup.")

        q = query.strip()
        if len(q) < 2:
            return []

        q_emb = self.model.encode([q], normalize_embeddings=True).astype(np.float32)
        scores, idxs = self.index.search(q_emb, top_k)
        hits: List[RagHit] = []
        for score, idx in zip(scores[0].tolist(), idxs[0].tolist()):
            if idx < 0:
                continue
            if score < min_score:
                continue
            hits.append(RagHit(idx=idx, score=float(score), text=self.chunks[idx]))
        return hits

    def build_injection(self, query: str, top_k: int = 4) -> str:
        hits = self.retrieve(query, top_k=top_k)
        if not hits:
            return ""

        lines = ["【RAG参考资料】"]
        for i, h in enumerate(hits, 1):
            # 控制每条长度，避免把 prompt 撑爆
            t = h.text.strip()
            if len(t) > 450:
                t = t[:450] + "…"
            lines.append(f"({i}) {t}")
        lines.append(
            "【回答要求】优先基于参考资料；资料不足时再结合常识，并明确说明“资料未覆盖”。"
        )
        return "\n".join(lines) + "\n"

"""Deterministic lexical baselines. No pretrained models or network requests."""
from __future__ import annotations

import math
import re
from collections import Counter

from .data import number, require

TOKENIZER = "latin-words-cjk-unigrams-v1"


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+|[\u3400-\u9fff]", text.lower())


class BM25:
    def __init__(self, documents: list[str], k1: float = 1.5, b: float = 0.75):
        require(bool(documents), "empty BM25 corpus")
        self.k1, self.b = k1, b
        self.docs = [Counter(tokenize(doc)) for doc in documents]
        self.lengths = [sum(doc.values()) for doc in self.docs]
        self.average = sum(self.lengths) / len(self.lengths) or 1.0
        frequencies = Counter(token for doc in self.docs for token in doc)
        self.idf = {token: math.log(1 + (len(documents) - count + 0.5) / (count + 0.5))
                    for token, count in frequencies.items()}

    def scores(self, query: str) -> list[float]:
        query_tokens = set(tokenize(query))
        scores = []
        for doc, length in zip(self.docs, self.lengths):
            score = 0.0
            for token in query_tokens:
                frequency = doc.get(token, 0)
                if frequency:
                    norm = frequency + self.k1 * (1 - self.b + self.b * length / self.average)
                    score += self.idf.get(token, 0) * frequency * (self.k1 + 1) / norm
            scores.append(score)
        return scores


class LexicalRanker:
    """Per-candidate-set BM25; dialogue+knowledge is a heuristic baseline."""
    def __init__(self, knowledge_weight: float = 0.0):
        self.knowledge_weight = number(knowledge_weight, "knowledge_weight")

    def rank(self, history: list[str], knowledge: list[str], candidates: list[dict]) -> list[dict]:
        require(bool(candidates), "cannot rank empty candidates")
        index = BM25([candidate["text"] for candidate in candidates])
        dialogue_scores = index.scores("\n".join(history))
        knowledge_scores = index.scores("\n".join(knowledge)) if knowledge else [0.0] * len(candidates)
        results = [
            {"id": item["id"], "text": item["text"],
             "score": d + self.knowledge_weight * k, "dialogue_score": d, "knowledge_score": k}
            for item, d, k in zip(candidates, dialogue_scores, knowledge_scores)
        ]
        # Fixed label-independent tie break; no positive-first ordering shortcut.
        return sorted(results, key=lambda row: (-row["score"], row["id"]))

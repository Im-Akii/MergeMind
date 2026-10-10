"""Small BM25 with an identifier-aware tokenizer (camelCase / snake_case split)."""

from __future__ import annotations

import math
import re
from collections import Counter

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_SPLIT = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is", "it", "be", "as",
    "at", "by", "this", "that", "i", "we", "you", "my", "me", "when", "not", "are", "was", "but",
    "if", "so", "do", "does", "have", "has", "can", "from", "will", "would", "should", "there",
}


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for w in _WORD.findall(text or ""):
        low = w.lower()
        if low not in STOP and len(low) > 1:
            out.append(low)
        if "_" in w or any(c.isupper() for c in w[1:]):
            out += [p.lower() for p in _SPLIT.findall(w) if len(p) > 1 and p.lower() not in STOP]
    return out


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.docs = [Counter(d) for d in docs]
        self.lens = [len(d) for d in docs]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 0.0
        self.k1, self.b = k1, b
        df: Counter = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: list[str]) -> list[float]:
        out = []
        for d, length in zip(self.docs, self.lens):
            s = 0.0
            for t in set(query):
                tf = d.get(t, 0)
                if tf:
                    s += self.idf[t] * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * length / (self.avg or 1)))
            out.append(s)
        return out

    def top(self, query: list[str], k: int = 10) -> list[tuple[int, float]]:
        ranked = sorted(enumerate(self.scores(query)), key=lambda x: -x[1])
        return [(i, s) for i, s in ranked[:k] if s > 0]


def rrf(rankings: list[list[int]], k: int = 60) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion over several ranked id lists."""
    fused: Counter = Counter()
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            fused[item] += 1.0 / (k + rank + 1)
    return fused.most_common()

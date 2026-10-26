"""BM25 (Okapi) over chunk tokens. Lexical retrieval is what finds exact clause words
("non-renewal", "governed by", "gia hạn") that embeddings blur."""
from __future__ import annotations

import heapq
import math
import re
from collections import Counter

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.casefold())


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.4, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tfs = [Counter(d) for d in docs]
        self.lens = [len(d) for d in docs]
        self.avgdl = sum(self.lens) / max(1, len(docs))
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def score(self, query: list[str], i: int) -> float:
        tf = self.tfs[i]
        norm = self.k1 * (1 - self.b + self.b * self.lens[i] / (self.avgdl or 1))
        return sum(self.idf[t] * tf[t] * (self.k1 + 1) / (tf[t] + norm) for t in set(query) if t in tf)

    def scores(self, query: list[str]) -> list[float]:
        return [self.score(query, i) for i in range(len(self.tfs))]

    def top_k(self, query: list[str], k: int) -> list[int]:
        scored = [(s, i) for i, s in enumerate(self.scores(query)) if s > 0]
        return [i for _, i in heapq.nlargest(k, scored)]

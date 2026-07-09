"""BM25 (Okapi) 自研实现：倒排索引 + 标准 IDF/TF 饱和公式。"""

import math
from collections import Counter, defaultdict


class BM25Index:
    def __init__(self, docs: list[tuple[str, list[str]]], k1: float = 1.5, b: float = 0.75):
        """docs: [(doc_id, terms)]"""
        self.k1 = k1
        self.b = b
        self.doc_len: dict[str, int] = {}
        self.inverted: dict[str, dict[str, int]] = defaultdict(dict)  # term -> {doc_id: tf}
        for doc_id, terms in docs:
            self.doc_len[doc_id] = len(terms)
            for term, tf in Counter(terms).items():
                self.inverted[term][doc_id] = tf
        self.n_docs = len(self.doc_len)
        self.avg_len = (sum(self.doc_len.values()) / self.n_docs) if self.n_docs else 0.0

    def _idf(self, term: str) -> float:
        df = len(self.inverted.get(term, {}))
        if df == 0:
            return 0.0
        return math.log(1 + (self.n_docs - df + 0.5) / (df + 0.5))

    def search(self, query_terms: list[str], top_k: int = 10) -> list[tuple[str, float]]:
        scores: dict[str, float] = defaultdict(float)
        for term in query_terms:
            idf = self._idf(term)
            if idf == 0:
                continue
            for doc_id, tf in self.inverted.get(term, {}).items():
                dl = self.doc_len[doc_id] or 1
                denom = tf + self.k1 * (1 - self.b + self.b * dl / (self.avg_len or 1))
                scores[doc_id] += idf * tf * (self.k1 + 1) / denom
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return ranked[:top_k]


def rrf_fuse(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal Rank Fusion：多路召回融合，k=60 为经验值。"""
    fused: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            fused[doc_id] += 1.0 / (k + rank)
    return dict(fused)

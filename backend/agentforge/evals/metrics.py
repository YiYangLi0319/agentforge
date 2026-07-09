"""检索评估指标：Recall@K / HitRate@K / MRR / nDCG@K（自研实现）。"""

import math


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """前 K 个结果覆盖了多少比例的相关文档。"""
    if not relevant:
        return 0.0
    hits = sum(1 for doc in retrieved[:k] if doc in relevant)
    return hits / len(relevant)


def hit_rate_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """前 K 个结果中是否至少命中一个相关文档。"""
    return 1.0 if any(doc in relevant for doc in retrieved[:k]) else 0.0


def mrr(retrieved: list[str], relevant: set[str]) -> float:
    """第一个相关结果的排名倒数。"""
    for rank, doc in enumerate(retrieved, start=1):
        if doc in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """归一化折损累计增益（二值相关性）。"""
    dcg = sum(
        1.0 / math.log2(rank + 1) for rank, doc in enumerate(retrieved[:k], start=1) if doc in relevant
    )
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


def aggregate(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0

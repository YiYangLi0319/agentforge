"""重排序：兼容 Jina / 通用 rerank API 格式；未配置时返回 None（跳过重排）。"""

import logging
from abc import ABC, abstractmethod

import httpx

logger = logging.getLogger(__name__)


class Reranker(ABC):
    name = "base"

    @abstractmethod
    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        """返回与 documents 等长的相关性分数列表。"""


class APIReranker(Reranker):
    """POST {base_url} {model, query, documents} -> results[{index, relevance_score}]"""

    name = "api"

    def __init__(self, *, base_url: str, api_key: str, model: str):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "query": query, "documents": documents},
            )
            resp.raise_for_status()
            data = resp.json()
        scores = [0.0] * len(documents)
        for item in data.get("results", []):
            idx = item.get("index", -1)
            if 0 <= idx < len(documents):
                scores[idx] = float(item.get("relevance_score", 0.0))
        return scores


def build_reranker(settings) -> Reranker | None:
    if settings.rerank_api_key and settings.rerank_base_url:
        return APIReranker(
            base_url=settings.rerank_base_url,
            api_key=settings.rerank_api_key,
            model=settings.rerank_model or "jina-reranker-v2-base-multilingual",
        )
    return None

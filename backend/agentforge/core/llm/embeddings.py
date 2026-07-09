"""Embedding 抽象：OpenAI 兼容实现 + 确定性 Mock（字符 n-gram 投影，保持文本相似性）。"""

import hashlib
import math
import random
from abc import ABC, abstractmethod

import httpx

from agentforge.core.llm.base import LLMError


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class Embeddings(ABC):
    model: str = ""
    provider: str = ""
    dim: int = 0

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]


class OpenAICompatEmbeddings(Embeddings):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        provider: str = "openai_compat",
        dim: int = 0,
        send_dimensions: bool = False,
        batch_size: int = 16,
    ):
        self.model = model
        self.provider = provider
        self.dim = dim
        self.send_dimensions = send_dimensions
        self.batch_size = batch_size
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60.0,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        result: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            payload: dict = {"model": self.model, "input": batch}
            if self.send_dimensions and self.dim:
                payload["dimensions"] = self.dim
            resp = await self._client.post("/embeddings", json=payload)
            if resp.status_code >= 400:
                raise LLMError(f"Embedding HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            items = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
            result.extend([item["embedding"] for item in items])
        if result and not self.dim:
            self.dim = len(result[0])
        return result

    async def aclose(self) -> None:
        await self._client.aclose()


class MockEmbeddings(Embeddings):
    """确定性向量：哈希随机分量 + 字符 3-gram 投影分量。

    相同文本向量完全一致；字面重叠的文本相似度更高，
    保证离线模式下向量检索排序仍然合理。
    """

    provider = "mock"
    model = "mock-embedding"

    def __init__(self, dim: int = 256):
        self.dim = dim

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        # n-gram 分量：捕捉字面相似性
        t = text.strip().lower()
        for i in range(max(len(t) - 2, 1)):
            gram = t[i : i + 3]
            h = int(hashlib.md5(gram.encode("utf-8")).hexdigest()[:8], 16)
            vec[h % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        vec = [x / norm * 0.8 for x in vec]
        # 哈希随机分量：避免全零并区分无重叠文本
        rng = random.Random(hashlib.md5(t.encode("utf-8")).hexdigest())
        noise = [rng.uniform(-1, 1) for _ in range(self.dim)]
        nnorm = math.sqrt(sum(x * x for x in noise)) or 1.0
        return [v + n / nnorm * 0.2 for v, n in zip(vec, noise, strict=True)]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

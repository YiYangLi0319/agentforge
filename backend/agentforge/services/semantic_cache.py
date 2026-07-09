"""语义缓存：对相似问题复用历史答案，降本提速。

- 作用域隔离：缓存键 = hash(agent_type + 排序后的 kb_ids)，避免不同知识库/模式串答案；
- 命中判定：查询向量与缓存条目余弦相似度 >= 阈值 且未过期（TTL）；
- 统计：进程内 hits/misses 计数 + DB 条目数，供看板展示命中率。
"""

import hashlib
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge.core.llm.embeddings import Embeddings, cosine
from agentforge.db.models import SemanticCacheEntry

logger = logging.getLogger(__name__)


def _record_cache(hit: bool) -> None:
    try:
        from agentforge.observability.metrics import record_cache

        record_cache(hit)
    except Exception:  # noqa: BLE001 指标失败不影响主流程
        pass


def scope_key(agent_type: str, kb_ids: list[str]) -> str:
    raw = agent_type + "|" + ",".join(sorted(kb_ids))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class CacheLookup:
    def __init__(self, answer: str, sources: list[dict], similarity: float):
        self.answer = answer
        self.sources = sources
        self.similarity = similarity


class SemanticCache:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        embeddings: Embeddings,
        *,
        enabled: bool = True,
        threshold: float = 0.93,
        ttl_seconds: int = 86400,
        max_scan: int = 200,
    ):
        self.sessions = sessions
        self.embeddings = embeddings
        self.enabled = enabled
        self.threshold = threshold
        self.ttl_seconds = ttl_seconds
        self.max_scan = max_scan
        self.hits = 0
        self.misses = 0

    async def lookup(self, agent_type: str, kb_ids: list[str], query: str) -> CacheLookup | None:
        if not self.enabled:
            return None
        key = scope_key(agent_type, kb_ids)
        cutoff = datetime.now(UTC) - timedelta(seconds=self.ttl_seconds)
        query_emb = await self.embeddings.embed_one(query)

        async with self.sessions() as db:
            rows = (
                (
                    await db.execute(
                        select(SemanticCacheEntry)
                        .where(
                            SemanticCacheEntry.scope_key == key,
                            SemanticCacheEntry.created_at >= cutoff,
                        )
                        .order_by(SemanticCacheEntry.last_used_at.desc())
                        .limit(self.max_scan)
                    )
                )
                .scalars()
                .all()
            )
            best, best_sim = None, 0.0
            for row in rows:
                sim = cosine(query_emb, row.embedding or [])
                if sim > best_sim:
                    best, best_sim = row, sim

            if best is not None and best_sim >= self.threshold:
                await db.execute(
                    update(SemanticCacheEntry)
                    .where(SemanticCacheEntry.id == best.id)
                    .values(hit_count=best.hit_count + 1, last_used_at=datetime.now(UTC))
                )
                await db.commit()
                self.hits += 1
                _record_cache(True)
                return CacheLookup(best.answer, list(best.sources or []), round(best_sim, 4))

        self.misses += 1
        _record_cache(False)
        return None

    async def store(
        self, agent_type: str, kb_ids: list[str], query: str, answer: str, sources: list[dict]
    ) -> None:
        if not self.enabled or not answer.strip():
            return
        key = scope_key(agent_type, kb_ids)
        emb = await self.embeddings.embed_one(query)
        async with self.sessions() as db:
            db.add(
                SemanticCacheEntry(
                    scope_key=key, query=query, answer=answer, sources=sources, embedding=emb
                )
            )
            await db.commit()

    async def stats(self) -> dict:
        async with self.sessions() as db:
            entries = (await db.execute(select(func.count(SemanticCacheEntry.id)))).scalar() or 0
            total_hits = (
                await db.execute(select(func.coalesce(func.sum(SemanticCacheEntry.hit_count), 0)))
            ).scalar() or 0
        total = self.hits + self.misses
        return {
            "enabled": self.enabled,
            "threshold": self.threshold,
            "entries": int(entries),
            "session_hits": self.hits,
            "session_misses": self.misses,
            "hit_rate": round(self.hits / total, 4) if total else 0.0,
            "lifetime_hits": int(total_hits),
        }

    async def clear(self) -> int:
        async with self.sessions() as db:
            count = (await db.execute(select(func.count(SemanticCacheEntry.id)))).scalar() or 0
            await db.execute(delete(SemanticCacheEntry))
            await db.commit()
        self.hits = self.misses = 0
        return int(count)

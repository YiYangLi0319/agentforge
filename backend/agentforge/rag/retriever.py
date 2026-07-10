"""混合检索：pgvector 向量召回 + 自研 BM25 关键词召回 -> RRF 融合 -> 可选 API 重排。

SQLite（轻量模式）下向量检索自动降级为进程内余弦计算，接口与评分口径完全一致。
"""

import logging

from pydantic import BaseModel
from sqlalchemy import and_, bindparam, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge.core.llm.embeddings import Embeddings, cosine
from agentforge.db.models import Chunk, Document, KnowledgeBase
from agentforge.rag.bm25 import BM25Index, rrf_fuse
from agentforge.rag.rerank import Reranker
from agentforge.rag.tokenize import tokenize

logger = logging.getLogger(__name__)


class RetrievedChunk(BaseModel):
    chunk_id: str
    document_id: str
    kb_id: str
    seq: int = 0
    filename: str = ""
    heading: str = ""
    content: str = ""
    vector_score: float = 0.0
    bm25_score: float = 0.0
    rrf_score: float = 0.0
    rerank_score: float | None = None
    final_score: float = 0.0
    expanded: bool = False  # 是否经父块扩展（small-to-big）


class HybridRetriever:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        embeddings: Embeddings,
        reranker: Reranker | None = None,
    ):
        self.sessions = sessions
        self.embeddings = embeddings
        self.reranker = reranker
        self._bm25_cache: dict[str, tuple[str, BM25Index]] = {}

    async def _kb_revision(self, session: AsyncSession, kb_ids: list[str]) -> str:
        rows = (
            await session.execute(
                select(KnowledgeBase.id, KnowledgeBase.updated_at).where(
                    KnowledgeBase.id.in_(kb_ids)
                )
            )
        ).all()
        return "|".join(
            f"{kb_id}:{updated_at.isoformat()}" for kb_id, updated_at in sorted(rows)
        )

    async def _get_bm25(self, session: AsyncSession, kb_ids: list[str]) -> BM25Index:
        key = ",".join(sorted(kb_ids))
        revision = await self._kb_revision(session, kb_ids)
        cached = self._bm25_cache.get(key)
        if cached and cached[0] == revision:
            return cached[1]
        rows = (
            await session.execute(select(Chunk.id, Chunk.terms).where(Chunk.kb_id.in_(kb_ids)))
        ).all()
        index = BM25Index([(r[0], r[1] or []) for r in rows])
        self._bm25_cache[key] = (revision, index)
        return index

    async def _vector_search(
        self, session: AsyncSession, kb_ids: list[str], query_emb: list[float], limit: int
    ) -> list[tuple[str, float]]:
        from agentforge.db.types import pgvector_enabled

        if session.bind.dialect.name == "postgresql" and pgvector_enabled():
            emb_literal = "[" + ",".join(f"{x:.6f}" for x in query_emb) + "]"
            stmt = (
                text(
                    """
                    SELECT id, 1 - (embedding <=> CAST(:emb AS vector)) AS score
                    FROM chunks
                    WHERE kb_id IN :kb_ids AND embedding IS NOT NULL
                    ORDER BY embedding <=> CAST(:emb AS vector)
                    LIMIT :lim
                    """
                )
                .bindparams(bindparam("kb_ids", expanding=True))
            )
            rows = (
                await session.execute(stmt, {"emb": emb_literal, "kb_ids": kb_ids, "lim": limit})
            ).all()
            return [(r[0], float(r[1])) for r in rows]

        # SQLite / 其他方言：进程内余弦
        rows = (
            await session.execute(
                select(Chunk.id, Chunk.embedding).where(
                    Chunk.kb_id.in_(kb_ids), Chunk.embedding.is_not(None)
                )
            )
        ).all()
        scored = [(r[0], cosine(query_emb, r[1])) for r in rows]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    async def search(
        self,
        kb_ids: list[str],
        query: str,
        top_k: int = 5,
        *,
        mode: str = "hybrid",  # hybrid | vector | keyword
        rerank: bool = True,
        vector_query: str | None = None,  # HyDE：向量检索用假设文档，BM25 仍用原查询
        parent_window: int = 0,  # >0 时对命中块做父块扩展（small-to-big）
    ) -> list[RetrievedChunk]:
        if not kb_ids:
            return []
        candidate_n = max(top_k * 4, 20)

        async with self.sessions() as session:
            vector_hits: list[tuple[str, float]] = []
            bm25_hits: list[tuple[str, float]] = []

            if mode in ("hybrid", "vector"):
                query_emb = await self.embeddings.embed_one(vector_query or query)
                vector_hits = await self._vector_search(session, kb_ids, query_emb, candidate_n)
            if mode in ("hybrid", "keyword"):
                index = await self._get_bm25(session, kb_ids)
                bm25_hits = index.search(tokenize(query), top_k=candidate_n)

            fused = rrf_fuse([[cid for cid, _ in vector_hits], [cid for cid, _ in bm25_hits]])
            if not fused:
                return []
            ranked_ids = sorted(fused, key=lambda cid: fused[cid], reverse=True)[: max(top_k * 3, 15)]

            rows = (
                await session.execute(
                    select(Chunk, Document.filename)
                    .join(Document, Document.id == Chunk.document_id)
                    .where(Chunk.id.in_(ranked_ids))
                )
            ).all()

        vmap, bmap = dict(vector_hits), dict(bm25_hits)
        by_id: dict[str, RetrievedChunk] = {}
        for chunk, filename in rows:
            by_id[chunk.id] = RetrievedChunk(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                kb_id=chunk.kb_id,
                seq=chunk.seq,
                filename=filename,
                heading=chunk.heading,
                content=chunk.content,
                vector_score=round(vmap.get(chunk.id, 0.0), 4),
                bm25_score=round(bmap.get(chunk.id, 0.0), 4),
                rrf_score=round(fused.get(chunk.id, 0.0), 5),
                final_score=round(fused.get(chunk.id, 0.0), 5),
            )
        results = [by_id[cid] for cid in ranked_ids if cid in by_id]

        if rerank and self.reranker and results:
            try:
                scores = await self.reranker.rerank(query, [r.content for r in results])
                for r, s in zip(results, scores, strict=True):
                    r.rerank_score = round(s, 4)
                    r.final_score = round(s, 4)
                results.sort(key=lambda r: r.final_score, reverse=True)
            except Exception as e:  # noqa: BLE001 重排失败退回 RRF 排序
                logger.warning("重排失败，使用 RRF 排序: %s", e)

        results = results[:top_k]
        if parent_window > 0 and results:
            await self._expand_parents(results, parent_window)
        return results

    async def _expand_parents(self, results: list[RetrievedChunk], window: int) -> None:
        """small-to-big：把命中的小块扩展为包含相邻块的更大上下文。"""
        ranges: dict[str, tuple[int, int]] = {}
        for result in results:
            lo, hi = max(result.seq - window, 0), result.seq + window
            previous = ranges.get(result.document_id)
            ranges[result.document_id] = (
                min(previous[0], lo) if previous else lo,
                max(previous[1], hi) if previous else hi,
            )
        conditions = [
            and_(Chunk.document_id == document_id, Chunk.seq >= lo, Chunk.seq <= hi)
            for document_id, (lo, hi) in ranges.items()
        ]
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    select(Chunk.document_id, Chunk.seq, Chunk.content)
                    .where(or_(*conditions))
                    .order_by(Chunk.document_id, Chunk.seq)
                )
            ).all()
        by_document: dict[str, list[tuple[int, str]]] = {}
        for document_id, seq, content in rows:
            by_document.setdefault(document_id, []).append((seq, content))
        for result in results:
            lo, hi = max(result.seq - window, 0), result.seq + window
            parts = [
                content
                for seq, content in by_document.get(result.document_id, [])
                if lo <= seq <= hi
            ]
            if len(parts) > 1:
                result.content = "\n".join(parts)
                result.expanded = True

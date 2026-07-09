"""文档入库流水线：解析 -> 语义分块 -> 分词 -> 向量化 -> 落库，全程更新文档状态。"""

import logging

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge.core.llm.embeddings import Embeddings
from agentforge.db.models import Chunk, Document, KnowledgeBase, utcnow
from agentforge.rag.chunking import chunk_sections
from agentforge.rag.parsers import parse_document
from agentforge.rag.tokenize import tokenize

logger = logging.getLogger(__name__)

EMBED_BATCH = 16


async def ingest_document(
    sessions: async_sessionmaker[AsyncSession],
    embeddings: Embeddings,
    document_id: str,
    filename: str,
    data: bytes,
) -> None:
    async with sessions() as session:
        await session.execute(
            update(Document).where(Document.id == document_id).values(status="processing")
        )
        await session.commit()

    try:
        sections = parse_document(filename, data)
        drafts = chunk_sections(sections)
        if not drafts:
            raise ValueError("解析结果为空（文档无有效文本内容）")

        vectors: list[list[float]] = []
        for i in range(0, len(drafts), EMBED_BATCH):
            batch = [d.content for d in drafts[i : i + EMBED_BATCH]]
            vectors.extend(await embeddings.embed(batch))

        async with sessions() as session:
            doc = (
                await session.execute(select(Document).where(Document.id == document_id))
            ).scalar_one()
            # 重新入库前清理旧分块（支持重复上传同名文档的幂等处理）
            await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
            for draft, vec in zip(drafts, vectors, strict=True):
                session.add(
                    Chunk(
                        document_id=document_id,
                        kb_id=doc.kb_id,
                        seq=draft.seq,
                        content=draft.content,
                        heading=draft.heading,
                        tokens=draft.tokens,
                        terms=tokenize(draft.content),
                        embedding=vec,
                    )
                )
            doc.status = "ready"
            doc.error = ""
            doc.chunk_count = len(drafts)
            await session.execute(
                update(KnowledgeBase).where(KnowledgeBase.id == doc.kb_id).values(updated_at=utcnow())
            )
            await session.commit()
        logger.info("文档入库完成: %s（%s 块）", filename, len(drafts))
    except Exception as e:  # noqa: BLE001 入库失败要落状态，不能让后台任务裸抛
        logger.exception("文档入库失败: %s", filename)
        async with sessions() as session:
            await session.execute(
                update(Document)
                .where(Document.id == document_id)
                .values(status="failed", error=str(e)[:500])
            )
            await session.commit()


async def delete_document_chunks(session: AsyncSession, document_id: str) -> None:
    """显式删除分块（SQLite 默认不启用外键级联，跨方言安全）。"""
    await session.execute(delete(Chunk).where(Chunk.document_id == document_id))

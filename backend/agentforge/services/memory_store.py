"""长期记忆存储（数据库实现）：MemoryEntry 表 + 余弦相似度检索。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge.core.llm.embeddings import cosine
from agentforge.db.models import MemoryEntry


class DBMemoryStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self.sessions = sessions

    async def add(self, user_id: str, items: list[dict]) -> None:
        async with self.sessions() as session:
            for item in items:
                session.add(
                    MemoryEntry(
                        user_id=user_id,
                        content=item["content"],
                        importance=item.get("importance", 3),
                        embedding=item.get("embedding"),
                    )
                )
            await session.commit()

    async def search(self, user_id: str, embedding: list[float], limit: int) -> list[dict]:
        async with self.sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(MemoryEntry).where(MemoryEntry.user_id == user_id).limit(500)
                    )
                )
                .scalars()
                .all()
            )
        scored: list[dict] = [
            {
                "content": r.content,
                "importance": r.importance,
                "embedding": r.embedding,
                "similarity": cosine(embedding, r.embedding or []),
            }
            for r in rows
        ]
        scored.sort(key=lambda x: float(x["similarity"]), reverse=True)
        return scored[:limit]

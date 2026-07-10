"""记忆系统。

- ConversationMemory（短期）：超出 token 预算时，把较早的对话轮次滚动压缩为摘要；
- LongTermMemory（长期）：LLM 从对话中抽取事实 -> 向量化 -> 去重入库，跨会话按相似度召回。
"""

import logging
from typing import Protocol

from pydantic import BaseModel, Field

from agentforge.core.llm.base import ChatModel
from agentforge.core.llm.embeddings import Embeddings, cosine
from agentforge.core.llm.structured import complete_json
from agentforge.core.messages import Message, estimate_messages_tokens

logger = logging.getLogger(__name__)


class ConversationMemory:
    """短期记忆：滚动摘要压缩。返回准备好的上下文与更新后的摘要。"""

    def __init__(self, llm: ChatModel, token_budget: int = 6000, keep_recent: int = 6):
        self.llm = llm
        self.token_budget = token_budget
        self.keep_recent = keep_recent
        self.did_compact = False

    def should_compact(self, history: list[Message]) -> bool:
        return estimate_messages_tokens(history) > self.token_budget and len(history) > self.keep_recent

    def _with_summary(self, messages: list[Message], summary: str) -> list[Message]:
        if not summary:
            return messages
        note = Message.system(f"（此前对话的摘要，供参考）\n{summary}")
        return [note] + messages

    async def prepare(self, history: list[Message], summary: str = "") -> tuple[list[Message], str]:
        self.did_compact = False
        if not self.should_compact(history):
            return self._with_summary(history, summary), summary

        old, recent = history[: -self.keep_recent], history[-self.keep_recent :]
        transcript = "\n".join(f"{m.role.value}: {m.content}" for m in old if m.content)
        prompt = (
            "请把以下对话内容压缩为一段摘要（300 字以内），"
            "务必保留：用户的关键信息与偏好、正在进行的任务、已确认的结论。\n\n"
        )
        if summary:
            prompt += f"【已有摘要】\n{summary}\n\n"
        prompt += f"【需要合并压缩的对话】\n{transcript}"
        try:
            resp = await self.llm.complete([Message.user(prompt)], temperature=0.1, max_tokens=400)
            new_summary = resp.message.content.strip()
            self.did_compact = bool(new_summary)
        except Exception as e:  # noqa: BLE001 摘要失败不阻断对话
            logger.warning("对话摘要失败，保留原摘要: %s", e)
            new_summary = summary
        return self._with_summary(recent, new_summary), new_summary


class ExtractedFact(BaseModel):
    content: str = Field(description="一条独立、完整、可跨会话复用的事实")
    importance: int = Field(default=3, ge=1, le=5, description="重要性 1-5")


class FactList(BaseModel):
    facts: list[ExtractedFact] = Field(default_factory=list)


class MemoryStore(Protocol):
    async def add(self, user_id: str, items: list[dict]) -> None: ...

    async def search(self, user_id: str, embedding: list[float], limit: int) -> list[dict]: ...


class InMemoryMemoryStore:
    """进程内实现（测试用）；生产实现见 services.memory_store（pgvector）。"""

    def __init__(self) -> None:
        self.items: dict[str, list[dict]] = {}

    async def add(self, user_id: str, items: list[dict]) -> None:
        self.items.setdefault(user_id, []).extend(items)

    async def search(self, user_id: str, embedding: list[float], limit: int) -> list[dict]:
        rows = self.items.get(user_id, [])
        scored = [
            {**r, "similarity": cosine(embedding, r.get("embedding") or [])} for r in rows
        ]
        scored.sort(key=lambda r: r["similarity"], reverse=True)
        return scored[:limit]


class LongTermMemory:
    def __init__(
        self,
        store: MemoryStore,
        embeddings: Embeddings,
        llm: ChatModel,
        *,
        dedupe_threshold: float = 0.92,
    ):
        self.store = store
        self.embeddings = embeddings
        self.llm = llm
        self.dedupe_threshold = dedupe_threshold

    async def extract_and_store(self, user_id: str, turns: list[Message]) -> int:
        transcript = "\n".join(f"{m.role.value}: {m.content}" for m in turns if m.content)
        if not transcript.strip():
            return 0
        prompt = (
            "从下面的对话中抽取值得长期记住的用户事实（身份、偏好、目标、约束等）。"
            "没有则返回空列表；不要抽取一次性的临时信息。\n\n" + transcript
        )
        result, _ = await complete_json(self.llm, [Message.user(prompt)], FactList)
        if not result.facts:
            return 0

        embeddings = await self.embeddings.embed([f.content for f in result.facts])
        to_add: list[dict] = []
        for fact, emb in zip(result.facts, embeddings, strict=True):
            top = await self.store.search(user_id, emb, 1)
            if top and top[0].get("similarity", 0) >= self.dedupe_threshold:
                continue  # 已有近似记忆，跳过
            to_add.append({"content": fact.content, "importance": fact.importance, "embedding": emb})
        if to_add:
            await self.store.add(user_id, to_add)
        return len(to_add)

    async def retrieve(
        self,
        user_id: str,
        query: str,
        k: int = 5,
        min_similarity: float = 0.3,
        *,
        query_embedding: list[float] | None = None,
    ) -> list[str]:
        emb = query_embedding or await self.embeddings.embed_one(query)
        rows = await self.store.search(user_id, emb, k)
        return [r["content"] for r in rows if r.get("similarity", 0) >= min_similarity]


def render_memories(memories: list[str]) -> str:
    if not memories:
        return ""
    lines = "\n".join(f"- {m}" for m in memories)
    return f"（关于该用户的长期记忆，供参考）\n{lines}"

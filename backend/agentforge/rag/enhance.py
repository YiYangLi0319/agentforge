"""RAG 进阶增强：查询改写、HyDE、上下文压缩。均为可选步骤，失败自动回退不影响主流程。"""

import logging

from pydantic import BaseModel, Field

from agentforge.core.llm.base import ChatModel
from agentforge.core.llm.structured import complete_json
from agentforge.core.messages import Message
from agentforge.rag.retriever import RetrievedChunk

logger = logging.getLogger(__name__)


class RewrittenQuery(BaseModel):
    query: str = Field(description="改写后的检索查询：补全指代、展开缩写、加入关键实体词")


async def rewrite_query(llm: ChatModel, query: str, history_hint: str = "") -> str:
    """查询改写：让口语化/含指代的问题更适合检索。失败则返回原查询。"""
    try:
        prompt = "把下面的用户问题改写成更适合知识库检索的查询（保留关键实体，展开指代与缩写，不要臆造事实）。"
        if history_hint:
            prompt += f"\n对话背景：{history_hint}"
        prompt += f"\n\n用户问题：{query}"
        result, _ = await complete_json(llm, [Message.user(prompt)], RewrittenQuery, temperature=0.1)
        return result.query.strip() or query
    except Exception as e:  # noqa: BLE001
        logger.warning("查询改写失败，用原查询: %s", e)
        return query


async def hyde_document(llm: ChatModel, query: str) -> str:
    """HyDE：生成一段"假设答案"用于向量检索（假设答案与真实文档语义更接近）。"""
    try:
        prompt = (
            "针对下面的问题，写一段 2-4 句、像知识库文档摘录一样的假设性答案（不必真实，用于检索）。"
            f"\n\n问题：{query}"
        )
        resp = await llm.complete([Message.user(prompt)], temperature=0.3, max_tokens=200)
        return resp.message.content.strip() or query
    except Exception as e:  # noqa: BLE001
        logger.warning("HyDE 生成失败，用原查询: %s", e)
        return query


class CompressedChunk(BaseModel):
    relevant: bool = Field(description="该片段是否与问题相关")
    extract: str = Field(default="", description="仅保留与问题相关的原文句子，无关则留空")


async def compress_context(
    llm: ChatModel, query: str, chunks: list[RetrievedChunk]
) -> list[RetrievedChunk]:
    """上下文压缩：逐块抽取与问题相关的句子，剔除无关块，降低送入模型的 token。"""
    compressed: list[RetrievedChunk] = []
    for chunk in chunks:
        try:
            prompt = (
                "从下面的片段中，只抽取与问题直接相关的原文句子；若整段都无关，标记 relevant=false。\n\n"
                f"问题：{query}\n\n片段：\n{chunk.content}"
            )
            result, _ = await complete_json(llm, [Message.user(prompt)], CompressedChunk, temperature=0.0)
        except Exception as e:  # noqa: BLE001 压缩失败保留原块
            logger.warning("上下文压缩失败，保留原块: %s", e)
            compressed.append(chunk)
            continue
        if result.relevant and result.extract.strip():
            chunk = chunk.model_copy(update={"content": result.extract.strip()})
            compressed.append(chunk)
    return compressed or chunks  # 全被压没时退回原结果，避免空手

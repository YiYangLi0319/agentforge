"""引用溯源：检索结果 -> 带编号上下文；回答 -> 提取实际引用的来源子集。"""

import re

from agentforge.core.tools.sources import register_source
from agentforge.rag.retriever import RetrievedChunk

_CITE_RE = re.compile(r"\[(\d{1,3})\]")


def format_context_with_citations(chunks: list[RetrievedChunk], state: dict) -> str:
    """把检索结果格式化为带全局编号的上下文块，同时登记到来源注册表。"""
    if not chunks:
        return "（知识库中没有找到相关内容）"
    blocks = []
    for c in chunks:
        n = register_source(
            state,
            origin="kb",
            title=c.heading or c.filename,
            snippet=c.content[:200],
            chunk_id=c.chunk_id,
            document_id=c.document_id,
            filename=c.filename,
            heading=c.heading,
        )
        header = f"[{n}] 《{c.filename}》" + (f" - {c.heading}" if c.heading else "")
        blocks.append(f"{header}\n{c.content}")
    return "\n\n".join(blocks)


def extract_cited_ids(answer: str) -> set[int]:
    return {int(m) for m in _CITE_RE.findall(answer)}


def cited_sources(answer: str, state: dict) -> list[dict]:
    """返回答案中实际引用的来源（保持编号），无引用时返回空列表。"""
    sources: list[dict] = state.get("sources", [])
    used = extract_cited_ids(answer)
    return [s for s in sources if s["id"] in used]

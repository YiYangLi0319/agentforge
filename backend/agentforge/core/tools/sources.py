"""引用来源注册表：工具向共享 state 登记来源并获得全局编号 [n]，答案中的 [n] 可溯源。"""

from typing import Any


def register_source(
    state: dict[str, Any],
    *,
    origin: str,  # kb | web
    title: str,
    snippet: str = "",
    url: str = "",
    chunk_id: str = "",
    document_id: str = "",
    filename: str = "",
    heading: str = "",
) -> int:
    """登记来源并返回编号（从 1 开始）；同一 url/chunk 去重复用编号。"""
    sources: list[dict] = state.setdefault("sources", [])
    for s in sources:
        if (url and s.get("url") == url) or (chunk_id and s.get("chunk_id") == chunk_id):
            return int(s["id"])
    new_id = len(sources) + 1
    sources.append(
        {
            "id": new_id,
            "origin": origin,
            "title": title[:200],
            "snippet": snippet[:300],
            "url": url,
            "chunk_id": chunk_id,
            "document_id": document_id,
            "filename": filename,
            "heading": heading,
        }
    )
    return new_id

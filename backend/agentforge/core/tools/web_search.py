"""Web 搜索工具：Tavily(配 Key) > DuckDuckGo(免 Key) > Mock(离线兜底) 三级降级。"""

import asyncio
import logging
from abc import ABC, abstractmethod

import httpx
from pydantic import BaseModel

from agentforge.core.tools.base import ToolContext, ToolResult, tool

logger = logging.getLogger(__name__)


class SearchHit(BaseModel):
    title: str = ""
    url: str = ""
    snippet: str = ""


class SearchProvider(ABC):
    name = "base"

    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> list[SearchHit]: ...


class TavilySearchProvider(SearchProvider):
    name = "tavily"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def search(self, query: str, max_results: int = 5) -> list[SearchHit]:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": self.api_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                },
            )
            resp.raise_for_status()
            data = resp.json()
        return [
            SearchHit(title=r.get("title", ""), url=r.get("url", ""), snippet=r.get("content", "")[:400])
            for r in data.get("results", [])
        ]


class DuckDuckGoSearchProvider(SearchProvider):
    name = "duckduckgo"

    async def search(self, query: str, max_results: int = 5) -> list[SearchHit]:
        def _sync_search() -> list[SearchHit]:
            from ddgs import DDGS

            with DDGS() as ddgs:
                rows = ddgs.text(query, max_results=max_results)
                return [
                    SearchHit(
                        title=r.get("title", ""),
                        url=r.get("href", r.get("url", "")),
                        snippet=(r.get("body") or "")[:400],
                    )
                    for r in rows
                ]

        return await asyncio.to_thread(_sync_search)


class MockSearchProvider(SearchProvider):
    """离线演示用：返回确定性的假结果。"""

    name = "mock"

    async def search(self, query: str, max_results: int = 5) -> list[SearchHit]:
        return [
            SearchHit(
                title=f"关于「{query[:30]}」的资料 {i + 1}",
                url=f"https://example.com/mock/{abs(hash(query)) % 10000}/{i + 1}",
                snippet=f"这是离线演示模式下关于「{query[:40]}」的模拟搜索结果摘要（第 {i + 1} 条）。"
                f"配置 TAVILY_API_KEY 或联网后可获得真实结果。",
            )
            for i in range(min(max_results, 3))
        ]


class FallbackSearchProvider(SearchProvider):
    """按优先级依次尝试，全部失败则用 Mock 兜底，保证工具永不抛错。"""

    name = "auto"

    def __init__(self, providers: list[SearchProvider]):
        self.providers = providers

    async def search(self, query: str, max_results: int = 5) -> list[SearchHit]:
        for p in self.providers:
            try:
                hits = await p.search(query, max_results)
                if hits:
                    return hits
            except Exception as e:  # noqa: BLE001
                logger.warning("搜索提供方 %s 失败，尝试下一个: %s", p.name, e)
        return await MockSearchProvider().search(query, max_results)


def build_search_provider(tavily_api_key: str = "", mode: str = "auto") -> SearchProvider:
    if mode == "mock":
        return MockSearchProvider()
    chain: list[SearchProvider] = []
    if tavily_api_key:
        chain.append(TavilySearchProvider(tavily_api_key))
    chain.append(DuckDuckGoSearchProvider())
    return FallbackSearchProvider(chain)


def format_hits(hits: list[SearchHit], state: dict) -> str:
    """结果格式化 + 登记到全局来源注册表（答案中可用 [n] 引用）。"""
    from agentforge.core.tools.sources import register_source

    if not hits:
        return "没有找到相关结果，请尝试更换关键词。"
    lines = []
    for h in hits:
        n = register_source(state, origin="web", title=h.title, url=h.url, snippet=h.snippet)
        lines.append(f"[{n}] {h.title}\n  URL: {h.url}\n  摘要: {h.snippet}")
    return "\n".join(lines)


@tool(name="web_search", timeout=30.0, tags=["web"])
async def web_search(query: str, max_results: int = 5, ctx: ToolContext | None = None) -> ToolResult:
    """搜索互联网获取实时信息，返回网页标题、链接与摘要。

    Args:
        query: 搜索关键词（保持简短精准，中英文均可）
        max_results: 返回结果条数，默认 5，最大 10
    """
    assert ctx is not None
    provider: SearchProvider = ctx.services.get("search") or MockSearchProvider()
    hits = await provider.search(query, min(max(max_results, 1), 10))
    return ToolResult(content=format_hits(hits, ctx.state), data={"count": len(hits)})

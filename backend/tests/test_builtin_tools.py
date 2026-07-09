"""内置工具测试：搜索降级链与来源累积、网页抓取 SSRF 防护、Python 沙箱。"""

import httpx

from agentforge.core.tools.base import ToolContext
from agentforge.core.tools.python_sandbox import run_python_code
from agentforge.core.tools.web_fetch import extract_main_text, web_fetch
from agentforge.core.tools.web_search import (
    FallbackSearchProvider,
    MockSearchProvider,
    SearchHit,
    SearchProvider,
    web_search,
)


class BrokenProvider(SearchProvider):
    name = "broken"

    async def search(self, query: str, max_results: int = 5) -> list[SearchHit]:
        raise ConnectionError("network down")


async def test_search_fallback_chain_and_sources():
    provider = FallbackSearchProvider([BrokenProvider()])  # 全部失败 -> Mock 兜底
    ctx = ToolContext(services={"search": provider})
    result = await web_search.execute({"query": "AgentForge 是什么", "max_results": 3}, ctx)
    assert result.ok and "example.com" in result.content
    assert result.content.startswith("[1]")  # 带全局引用编号
    assert len(ctx.state["sources"]) == 3

    # 再搜一次相同结果不重复累积（编号复用）
    await web_search.execute({"query": "AgentForge 是什么", "max_results": 3}, ctx)
    assert len(ctx.state["sources"]) == 3


async def test_mock_search_deterministic():
    p = MockSearchProvider()
    a = await p.search("同一查询", 3)
    b = await p.search("同一查询", 3)
    assert [h.url for h in a] == [h.url for h in b]


async def test_web_fetch_blocks_private_hosts():
    for url in ["http://127.0.0.1:8000/admin", "http://localhost/x", "ftp://example.com/f"]:
        result = await web_fetch.execute({"url": url}, ToolContext())
        assert not result.ok


def test_extract_main_text_strips_noise():
    html = """
    <html><head><title>测试页</title><script>evil()</script></head>
    <body><nav>导航</nav><article><h1>正文标题</h1><p>正文内容第一段。</p></article>
    <footer>页脚</footer></body></html>
    """
    title, text = extract_main_text(html)
    assert title == "测试页"
    assert "正文内容" in text and "导航" not in text and "evil" not in text


async def test_web_fetch_parses_html(monkeypatch):
    html = b"<html><head><title>T</title></head><body><main><p>hello world</p></main></body></html>"

    real_client = httpx.AsyncClient

    def fake_client(**kwargs):
        kwargs.pop("transport", None)
        return real_client(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, content=html, headers={"content-type": "text/html"})
            ),
            **kwargs,
        )

    monkeypatch.setattr("agentforge.core.tools.web_fetch.httpx.AsyncClient", fake_client)
    result = await web_fetch.execute({"url": "http://93.184.216.34/page"}, ToolContext())
    assert result.ok and "hello world" in result.content


async def test_python_sandbox_success_and_error():
    ok = await run_python_code("print(sum(range(10)))")
    assert ok.ok and ok.content.strip() == "45"

    err = await run_python_code("raise ValueError('x')")
    assert not err.ok and "ValueError" in err.content


async def test_python_sandbox_timeout():
    result = await run_python_code("import time; time.sleep(5)", timeout=1.0)
    assert not result.ok and "超时" in result.content

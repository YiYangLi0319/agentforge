"""网页抓取工具：正文抽取 + SSRF 防护（禁止内网地址）。"""

import ipaddress
import socket
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from agentforge.core.tools.base import ToolContext, ToolResult, tool

MAX_BYTES = 2 * 1024 * 1024
MAX_TEXT = 8000

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 AgentForge/0.1"


def _ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """仅认可全局可路由地址。可拦截私有/环回/链路本地(含 169.254 云元数据)/CGNAT 等。"""
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10"):
        return False  # 运营商级 NAT（is_global 在部分版本未覆盖）
    return bool(ip.is_global)


def _is_private_host(host: str) -> bool:
    """SSRF 防护：解析域名，任一解析结果非全局可路由即拒绝。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True
    saw_address = False
    for info in infos:
        saw_address = True
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return True
        if not _ip_is_public(ip):
            return True
    return not saw_address


def extract_main_text(html: str) -> tuple[str, str]:
    """返回 (标题, 正文)。启发式抽取：优先 article/main，去除脚本导航等噪声。"""
    soup = BeautifulSoup(html, "lxml")
    title = (soup.title.string or "").strip() if soup.title else ""
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "iframe"]):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    text = root.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines()]
    cleaned = "\n".join(ln for ln in lines if ln)
    return title, cleaned[:MAX_TEXT]


@tool(name="web_fetch", timeout=30.0, tags=["web"])
async def web_fetch(url: str, ctx: ToolContext | None = None) -> ToolResult:
    """抓取指定网页并提取正文内容（用于阅读搜索到的页面详情）。

    Args:
        url: 完整的网页地址，必须以 http:// 或 https:// 开头
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return ToolResult.error("仅支持 http/https 协议")
    if not parsed.hostname or _is_private_host(parsed.hostname):
        return ToolResult.error("目标地址不可访问（内网地址已被安全策略拦截）")

    mock_mode = bool(ctx and ctx.services.get("mock_web"))
    if mock_mode:
        title, text = f"模拟页面: {url[:60]}", f"这是离线演示模式下 {url} 的模拟正文内容。"
    else:
        async with httpx.AsyncClient(
            headers={"User-Agent": _UA}, follow_redirects=False, timeout=20.0
        ) as client:
            async with client.stream("GET", url) as resp:
                if 300 <= resp.status_code < 400:
                    return ToolResult.error("目标返回重定向；为防止 SSRF，抓取器不会自动跟随")
                if resp.status_code >= 400:
                    return ToolResult.error(f"HTTP {resp.status_code}")
                content_type = resp.headers.get("content-type", "")
                if "html" not in content_type and "text" not in content_type:
                    return ToolResult.error(f"不支持的内容类型: {content_type}")
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > MAX_BYTES:
                        break
        html = b"".join(chunks).decode("utf-8", "ignore")
        title, text = extract_main_text(html)

    if ctx is not None:
        from agentforge.core.tools.sources import register_source

        n = register_source(
            ctx.state,
            origin="web",
            title=title or url,
            url=url,
            snippet=text[:200],
            verified=True,
            evidence=text,
        )
        return ToolResult(
            content=f"[{n}] 标题: {title}\n\n{text}", data={"title": title, "length": len(text)}
        )
    return ToolResult(content=f"标题: {title}\n\n{text}", data={"title": title, "length": len(text)})

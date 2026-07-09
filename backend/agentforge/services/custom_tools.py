"""自定义 HTTP 工具：把用户在 UI 定义的 HTTP 接口动态包装成 Agent 工具。

安全：复用 web_fetch 的 SSRF 防护（禁止内网地址）；URL/body 用参数模板渲染。
"""

import logging
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge.core.tools.base import Tool, ToolResult
from agentforge.core.tools.web_fetch import _is_private_host
from agentforge.db.models import CustomTool

logger = logging.getLogger(__name__)

_TYPE_MAP = {"string": "string", "number": "number", "integer": "integer", "boolean": "boolean"}


def _build_schema(params: list[dict]) -> dict:
    props: dict = {}
    required: list[str] = []
    for p in params:
        name = p.get("name")
        if not name:
            continue
        schema = {"type": _TYPE_MAP.get(p.get("type", "string"), "string")}
        if p.get("description"):
            schema["description"] = p["description"]
        props[name] = schema
        if p.get("required"):
            required.append(name)
    return {"type": "object", "properties": props, "required": required}


def _render(template: str, values: dict) -> str:
    out = template
    for k, v in values.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def build_custom_tool(row: CustomTool) -> Tool:
    params: list[dict] = list(row.params_schema or [])
    method = (row.method or "GET").upper()
    timeout = float(row.timeout or 15)

    async def handler(**kwargs) -> ToolResult:
        query_args = {
            p["name"]: kwargs[p["name"]]
            for p in params
            if p.get("location") == "query" and p["name"] in kwargs
        }
        url = _render(row.url_template, kwargs)
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return ToolResult.error("仅支持 http/https")
        if not parsed.hostname or _is_private_host(parsed.hostname):
            return ToolResult.error("目标地址不可访问（内网地址被安全策略拦截）")

        body = _render(row.body_template, kwargs) if row.body_template else None
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.request(
                    method,
                    url,
                    headers=row.headers or None,
                    params=query_args or None,
                    content=body.encode("utf-8") if body else None,
                )
        except httpx.HTTPError as e:
            return ToolResult.error(f"请求失败: {type(e).__name__}: {e}")
        text = resp.text[:4000]
        ok = resp.status_code < 400
        return ToolResult(ok=ok, content=f"HTTP {resp.status_code}\n{text}", data={"status": resp.status_code})

    return Tool(
        name=row.name,
        description=row.description or row.name,
        parameters=_build_schema(params),
        handler=handler,
        inject_ctx=False,
        timeout=timeout + 5,
        tags=["custom", "http"],
    )


async def load_custom_tools(sessions: async_sessionmaker[AsyncSession], user_id: str) -> list[Tool]:
    async with sessions() as db:
        rows = (
            (
                await db.execute(
                    select(CustomTool).where(
                        CustomTool.user_id == user_id, CustomTool.enabled.is_(True)
                    )
                )
            )
            .scalars()
            .all()
        )
    tools: list[Tool] = []
    for row in rows:
        try:
            tools.append(build_custom_tool(row))
        except Exception as e:  # noqa: BLE001
            logger.warning("自定义工具 %s 构建失败: %s", row.name, e)
    return tools

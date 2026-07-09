"""MCP JSON-RPC 客户端：握手、列出工具、调用工具。"""

import asyncio
import logging
from typing import Any

from pydantic import BaseModel

from agentforge.core.mcp.transport import Transport

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"
REQUEST_TIMEOUT = 30.0


class MCPError(Exception):
    pass


class MCPToolSpec(BaseModel):
    name: str
    description: str = ""
    input_schema: dict = {}


class MCPClient:
    def __init__(self, transport: Transport, client_name: str = "agentforge"):
        self.transport = transport
        self.client_name = client_name
        self._id = 0
        self._initialized = False

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    async def _request(self, method: str, params: dict | None = None) -> Any:
        msg_id = self._next_id()
        await self.transport.send(
            {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}
        )
        # 简化模型：请求-响应严格配对（本客户端不并发复用同一连接）
        try:
            response = await asyncio.wait_for(self.transport.receive(), timeout=REQUEST_TIMEOUT)
        except TimeoutError as e:
            raise MCPError(f"MCP 请求超时: {method}") from e
        if response.get("id") != msg_id:
            # 跳过通知等非配对消息，再取一次
            response = await asyncio.wait_for(self.transport.receive(), timeout=REQUEST_TIMEOUT)
        if "error" in response:
            raise MCPError(f"MCP 错误 [{method}]: {response['error'].get('message')}")
        return response.get("result", {})

    async def _notify(self, method: str, params: dict | None = None) -> None:
        await self.transport.send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def initialize(self) -> dict:
        await self.transport.start()
        result = await self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": self.client_name, "version": "0.1.0"},
            },
        )
        await self._notify("notifications/initialized")
        self._initialized = True
        return result

    async def list_tools(self) -> list[MCPToolSpec]:
        result = await self._request("tools/list")
        return [
            MCPToolSpec(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema") or t.get("input_schema") or {"type": "object", "properties": {}},
            )
            for t in result.get("tools", [])
        ]

    async def call_tool(self, name: str, arguments: dict) -> str:
        result = await self._request("tools/call", {"name": name, "arguments": arguments})
        # MCP 返回 content 数组，抽取文本
        parts: list[str] = []
        for item in result.get("content", []):
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
            else:
                parts.append(str(item))
        text = "\n".join(parts) if parts else str(result)
        if result.get("isError"):
            return f"[MCP 工具报错] {text}"
        return text

    async def close(self) -> None:
        await self.transport.close()

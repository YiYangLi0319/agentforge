"""MCP 管理器：连接多个 MCP 服务器、发现工具并包装成 Agent 可用的 Tool。"""

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field

from agentforge.core.mcp.client import MCPClient
from agentforge.core.mcp.transport import StdioTransport, Transport
from agentforge.core.tools.base import Tool, ToolResult

logger = logging.getLogger(__name__)


class MCPServerConfig(BaseModel):
    name: str
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict = Field(default_factory=dict)
    cwd: str | None = None
    enabled: bool = True


def _wrap_tool(client: MCPClient, server: str, spec) -> Tool:
    schema = spec.input_schema or {"type": "object", "properties": {}}
    if "properties" not in schema:
        schema = {"type": "object", "properties": {}}

    async def handler(**kwargs) -> ToolResult:
        text = await client.call_tool(spec.name, kwargs)
        return ToolResult(ok="[MCP 工具报错]" not in text, content=text)

    return Tool(
        name=f"mcp__{server}__{spec.name}",
        description=f"[MCP:{server}] {spec.description or spec.name}",
        parameters=schema,
        handler=handler,
        inject_ctx=False,
        timeout=45.0,
        tags=["mcp", server],
    )


class MCPManager:
    """按需连接 MCP 服务器；健康的服务器工具会被合并进 Agent 工具集。"""

    def __init__(self) -> None:
        self.clients: dict[str, MCPClient] = {}
        self.tools: list[Tool] = []
        self.status: dict[str, str] = {}  # server -> connected | error: xxx

    async def connect_config(self, config: MCPServerConfig) -> None:
        if not config.enabled:
            return
        transport = StdioTransport(config.command, config.args, config.env, config.cwd)
        await self._connect(config.name, transport)

    async def connect_transport(self, name: str, transport: Transport) -> None:
        """直接用给定 transport 连接（测试 / 进程内服务器）。"""
        await self._connect(name, transport)

    async def _connect(self, name: str, transport: Transport) -> None:
        client = MCPClient(transport)
        try:
            await client.initialize()
            specs = await client.list_tools()
            self.clients[name] = client
            for spec in specs:
                self.tools.append(_wrap_tool(client, name, spec))
            self.status[name] = "connected"
            logger.info("MCP 服务器 %s 已连接，发现 %s 个工具", name, len(specs))
        except Exception as e:  # noqa: BLE001 单个服务器失败不影响整体
            self.status[name] = f"error: {e}"
            logger.warning("MCP 服务器 %s 连接失败: %s", name, e)
            await client.close()

    async def load_from_file(self, path: str) -> None:
        cfg_path = Path(path)
        if not cfg_path.exists():
            logger.warning("MCP 配置文件不存在: %s", path)
            return
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        # 兼容 {"mcpServers": {name: {...}}} 与 [{name,...}] 两种格式
        servers = data.get("mcpServers", data) if isinstance(data, dict) else data
        configs: list[MCPServerConfig] = []
        if isinstance(servers, dict):
            for name, cfg in servers.items():
                configs.append(MCPServerConfig(name=name, **cfg))
        else:
            configs = [MCPServerConfig(**c) for c in servers]
        for cfg in configs:
            await self.connect_config(cfg)

    def tool_list(self) -> list[dict]:
        return [
            {"name": t.name, "description": t.description, "tags": t.tags} for t in self.tools
        ]

    async def shutdown(self) -> None:
        for client in self.clients.values():
            await client.close()
        self.clients.clear()
        self.tools.clear()

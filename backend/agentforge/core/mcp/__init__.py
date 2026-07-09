"""MCP (Model Context Protocol) 客户端：接入外部 MCP 工具服务器，把其工具注入 Agent。

对齐 Anthropic 的 MCP 标准（JSON-RPC 2.0）：initialize -> tools/list -> tools/call。
支持 stdio 传输（子进程）与进程内传输（测试）。
"""

from agentforge.core.mcp.client import MCPClient, MCPError
from agentforge.core.mcp.registry import MCPManager, MCPServerConfig

__all__ = ["MCPClient", "MCPError", "MCPManager", "MCPServerConfig"]

"""示例 MCP 服务器（stdio 传输，零依赖）：演示如何被 AgentForge 接入。

启动方式由 MCP 配置文件指定，例如 backend/mcp.config.json：
    {
      "mcpServers": {
        "demo": {"command": "python", "args": ["samples/mcp_server.py"]}
      }
    }
然后设置环境变量 MCP_CONFIG_PATH=mcp.config.json 并重启后端。

实现的工具：
- add(a, b)         两数相加
- current_time()    当前时间
- word_count(text)  统计字数
"""

import json
import sys
from datetime import datetime

TOOLS = [
    {
        "name": "add",
        "description": "计算两个数字的和",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
    },
    {
        "name": "current_time",
        "description": "返回当前日期时间",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "word_count",
        "description": "统计文本的字符数",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
]


def handle(method: str, params: dict):
    if method == "initialize":
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "agentforge-demo-mcp", "version": "0.1.0"},
        }
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {})
        if name == "add":
            return {"content": [{"type": "text", "text": str(args["a"] + args["b"])}]}
        if name == "current_time":
            return {"content": [{"type": "text", "text": datetime.now().isoformat(timespec="seconds")}]}
        if name == "word_count":
            return {"content": [{"type": "text", "text": str(len(args.get("text", "")))}]}
        return {"content": [{"type": "text", "text": f"未知工具: {name}"}], "isError": True}
    raise ValueError(f"未知方法: {method}")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg_id = msg.get("id")
        if msg_id is None:  # 通知，无需响应
            continue
        try:
            result = handle(msg.get("method", ""), msg.get("params", {}))
            response = {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except Exception as e:  # noqa: BLE001
            response = {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32000, "message": str(e)}}
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()

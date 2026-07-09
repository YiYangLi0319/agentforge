"""MCP 客户端测试：用进程内传输连接一个示例服务器，验证握手/列举/调用/包装。"""

from agentforge.core.mcp.registry import MCPManager
from agentforge.core.mcp.transport import InMemoryTransport
from agentforge.core.runtime import RunContext


async def _demo_handler(method: str, params: dict):
    if method == "initialize":
        return {"protocolVersion": "2024-11-05", "capabilities": {}, "serverInfo": {"name": "demo"}}
    if method == "tools/list":
        return {
            "tools": [
                {
                    "name": "add",
                    "description": "两数相加",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                        "required": ["a", "b"],
                    },
                },
                {
                    "name": "boom",
                    "description": "总是报错",
                    "inputSchema": {"type": "object", "properties": {}},
                },
            ]
        }
    if method == "tools/call":
        name = params["name"]
        args = params.get("arguments", {})
        if name == "add":
            return {"content": [{"type": "text", "text": str(args["a"] + args["b"])}]}
        if name == "boom":
            return {"content": [{"type": "text", "text": "内部错误"}], "isError": True}
    raise ValueError(f"未知方法 {method}")


async def test_mcp_connect_discover_and_call():
    mgr = MCPManager()
    await mgr.connect_transport("demo", InMemoryTransport(_demo_handler))

    assert mgr.status["demo"] == "connected"
    names = {t.name for t in mgr.tools}
    assert "mcp__demo__add" in names and "mcp__demo__boom" in names

    add_tool = next(t for t in mgr.tools if t.name == "mcp__demo__add")
    # schema 从 MCP inputSchema 透传
    assert add_tool.parameters["properties"]["a"]["type"] == "number"

    result = await add_tool.execute({"a": 3, "b": 4})
    assert result.ok and result.content == "7"

    boom_tool = next(t for t in mgr.tools if t.name == "mcp__demo__boom")
    err = await boom_tool.execute({})
    assert not err.ok and "报错" in err.content

    await mgr.shutdown()
    assert mgr.tools == []


async def test_mcp_bad_server_marked_error():
    async def broken(method: str, params: dict):
        raise RuntimeError("server down")

    mgr = MCPManager()
    await mgr.connect_transport("broken", InMemoryTransport(broken))
    assert "error" in mgr.status["broken"]
    assert mgr.tools == []


async def test_mcp_tool_usable_by_agent():
    """MCP 工具应能被自研 Agent 的 ReAct 循环正常调用。"""
    from agentforge.core.agent import Agent
    from agentforge.core.llm.mock import MockChatModel
    from agentforge.core.messages import Message, ToolCall

    mgr = MCPManager()
    await mgr.connect_transport("demo", InMemoryTransport(_demo_handler))
    llm = MockChatModel(
        script=[
            Message.assistant(
                tool_calls=[ToolCall(id="c1", name="mcp__demo__add", arguments={"a": 10, "b": 5})]
            ),
            "结果是 15。",
        ]
    )
    agent = Agent(llm=llm, tools=mgr.tools, max_steps=3)
    events = [ev async for ev in agent.run("算 10+5", RunContext(run_id="t"))]
    tool_fin = next(e for e in events if e.type == "tool_finished")
    assert tool_fin.ok and tool_fin.result_preview == "15"
    assert events[-1].type == "run_finished"
    await mgr.shutdown()

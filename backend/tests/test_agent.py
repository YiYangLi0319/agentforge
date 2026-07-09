"""ReAct 循环测试：完整回合、并行工具、审批门、步数/预算熔断、未知工具自愈。"""

import asyncio

from agentforge.core.agent import Agent
from agentforge.core.events import RunFinished
from agentforge.core.llm.mock import MockChatModel
from agentforge.core.messages import Message, ToolCall
from agentforge.core.runtime import RunContext
from agentforge.core.tools.base import ToolContext, ToolResult, tool


@tool()
async def echo(text: str) -> str:
    """回显文本。

    Args:
        text: 输入内容
    """
    return f"echo:{text}"


async def collect(agent: Agent, prompt: str, ctx: RunContext) -> list:
    return [ev async for ev in agent.run(prompt, ctx)]


def types_of(events: list) -> list[str]:
    return [e.type for e in events]


async def test_full_react_round(run_ctx):
    llm = MockChatModel(
        script=[
            Message.assistant(tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "hi"})]),
            "最终答案",
        ]
    )
    agent = Agent(name="tester", llm=llm, tools=[echo], system_prompt="你是测试助手", max_steps=5)
    events = await collect(agent, "请回显", run_ctx)
    t = types_of(events)

    assert t[0] == "step_started"
    assert "assistant_message" in t and "tool_started" in t and "tool_finished" in t
    assert t[-1] == "run_finished"

    finished = events[-1]
    assert isinstance(finished, RunFinished)
    assert finished.output["text"] == "最终答案"
    assert finished.usage.total_tokens > 0

    tool_fin = next(e for e in events if e.type == "tool_finished")
    assert tool_fin.ok and "echo:hi" in tool_fin.result_preview

    # 第二次 LLM 调用应看到工具结果消息
    second_call_msgs = llm.calls[1]["messages"]
    assert any(m["role"] == "tool" and "echo:hi" in m["content"] for m in second_call_msgs)

    # 追踪：agent span + 2 次 llm + 1 次 tool
    kinds = [s.kind for s in run_ctx.tracer.spans]
    assert kinds.count("llm") == 2 and kinds.count("tool") == 1 and "agent" in kinds


async def test_parallel_tool_calls_order_preserved(run_ctx):
    order: list[str] = []

    @tool()
    async def slow(tag: str) -> str:
        """慢工具。

        Args:
            tag: 标记
        """
        await asyncio.sleep(0.2 if tag == "a" else 0.01)
        order.append(tag)
        return tag

    llm = MockChatModel(
        script=[
            Message.assistant(
                tool_calls=[
                    ToolCall(id="c1", name="slow", arguments={"tag": "a"}),
                    ToolCall(id="c2", name="slow", arguments={"tag": "b"}),
                ]
            ),
            "done",
        ]
    )
    agent = Agent(llm=llm, tools=[slow], max_steps=3)
    events = await collect(agent, "并行", run_ctx)
    assert order == ["b", "a"]  # 并行执行：短任务先完成
    # 但回喂给模型的 tool 消息保持原调用顺序
    msgs = llm.calls[1]["messages"]
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2"]
    assert events[-1].type == "run_finished"


async def test_approval_gate_deny(run_ctx):
    @tool(requires_approval=True)
    async def danger(cmd: str) -> str:
        """危险操作。

        Args:
            cmd: 命令
        """
        return "executed"

    decisions = []

    async def gate(tc, t):
        decisions.append(tc.name)
        return False

    run_ctx.approval_gate = gate
    llm = MockChatModel(
        script=[
            Message.assistant(tool_calls=[ToolCall(id="c1", name="danger", arguments={"cmd": "rm"})]),
            "换个方式",
        ]
    )
    agent = Agent(llm=llm, tools=[danger], max_steps=3)
    events = await collect(agent, "执行危险操作", run_ctx)
    t = types_of(events)
    assert "approval_required" in t and "approval_decided" in t
    assert decisions == ["danger"]
    tool_fin = next(e for e in events if e.type == "tool_finished")
    assert not tool_fin.ok and "拒绝" in tool_fin.result_preview


async def test_max_steps_forces_final_answer(run_ctx):
    llm = MockChatModel(
        script=[
            Message.assistant(tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "1"})]),
            "被迫的最终回答",
        ]
    )
    agent = Agent(llm=llm, tools=[echo], max_steps=2)
    events = await collect(agent, "无限循环", run_ctx)
    assert events[-1].type == "run_finished"
    assert events[-1].output["text"] == "被迫的最终回答"
    # 第二次调用不应再携带工具，且注入了强制作答提示
    assert llm.calls[1]["tools"] == []
    assert any("禁止再调用工具" in m["content"] for m in llm.calls[1]["messages"] if m["role"] == "system")


async def test_token_budget_forces_final(run_ctx):
    llm = MockChatModel(
        script=[
            Message.assistant(tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "x"})]),
            "预算耗尽回答",
        ]
    )
    agent = Agent(llm=llm, tools=[echo], max_steps=10, token_budget=1)
    events = await collect(agent, "预算测试", run_ctx)
    assert events[-1].output["text"] == "预算耗尽回答"
    assert any("token 预算已用尽" in m["content"] for m in llm.calls[1]["messages"] if m["role"] == "system")


async def test_unknown_tool_feeds_error_back(run_ctx):
    llm = MockChatModel(
        script=[
            Message.assistant(tool_calls=[ToolCall(id="c1", name="ghost", arguments={})]),
            "改用已有工具",
        ]
    )
    agent = Agent(llm=llm, tools=[echo], max_steps=3)
    events = await collect(agent, "调用不存在的工具", run_ctx)
    tool_fin = next(e for e in events if e.type == "tool_finished")
    assert not tool_fin.ok and "未知工具" in tool_fin.result_preview
    assert events[-1].type == "run_finished"


async def test_checkpoint_contains_full_messages(run_ctx):
    llm = MockChatModel(script=["直接回答"])
    agent = Agent(llm=llm, tools=[], system_prompt="sys", max_steps=2)
    events = await collect(agent, "你好", run_ctx)
    ckpt = next(e for e in events if e.type == "checkpoint")
    roles = [m.role.value for m in ckpt.messages]
    assert roles == ["system", "user", "assistant"]


async def test_tool_emit_forwards_events(run_ctx):
    """工具内部可通过 ctx.emit 上浮自定义事件（子 Agent 场景的基础）。"""
    from agentforge.core.events import LLMDelta

    @tool()
    async def emitter(text: str, ctx: ToolContext) -> ToolResult:
        """会发事件的工具。

        Args:
            text: 输入
        """
        ctx.emit(LLMDelta(text="来自子任务", agent="child"))
        return ToolResult(content="done")

    llm = MockChatModel(
        script=[
            Message.assistant(tool_calls=[ToolCall(id="c1", name="emitter", arguments={"text": "x"})]),
            "结束",
        ]
    )
    agent = Agent(llm=llm, tools=[emitter], max_steps=3)
    events = await collect(agent, "发事件", run_ctx)
    child = [e for e in events if e.type == "llm_delta" and e.agent == "child"]
    assert len(child) == 1

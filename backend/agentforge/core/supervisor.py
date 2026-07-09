"""Supervisor 多 Agent 编排：主管 LLM 通过 delegate 工具把子任务派发给专家 Agent。

实现方式：把每个子 Agent 包装成一个工具（delegate_to_xxx），
主管本身就是一个 ReAct Agent —— 复用同一套循环、追踪与事件机制。
子 Agent 的过程事件通过 ToolContext.emit 实时上浮，前端可看到嵌套执行过程。
"""

from collections.abc import Callable
from dataclasses import dataclass

from agentforge.core.agent import Agent
from agentforge.core.events import RunFailed, RunFinished
from agentforge.core.llm.base import ChatModel
from agentforge.core.runtime import RunContext
from agentforge.core.tools.base import Tool, ToolContext, ToolRegistry, ToolResult


@dataclass
class WorkerSpec:
    name: str  # 如 "researcher"
    description: str  # 该专家擅长什么（供主管决策）
    build: Callable[[], Agent]  # 每次委派新建实例，避免状态串扰


def _make_delegate_tool(spec: WorkerSpec) -> Tool:
    async def handler(task: str, ctx: ToolContext) -> ToolResult:
        agent = spec.build()
        parent_ctx: RunContext | None = ctx.run_ctx
        sub_ctx = RunContext(
            run_id=ctx.run_id,
            user_id=ctx.user_id,
            session_id=ctx.session_id,
            kb_ids=ctx.kb_ids,
            services=ctx.services,
            state=ctx.state,
        )
        if parent_ctx is not None:
            sub_ctx.tracer = parent_ctx.tracer  # 共享追踪器，Span 树保持完整
            sub_ctx.approval_gate = parent_ctx.approval_gate
        final = ""
        failed = ""
        async for ev in agent.run(task, sub_ctx):
            if isinstance(ev, RunFinished):
                final = str(ev.output.get("text", ""))
            elif isinstance(ev, RunFailed):
                failed = ev.error
            elif ctx.emit is not None and ev.type != "checkpoint":
                ctx.emit(ev)  # 子 Agent 过程事件实时上浮
        if failed:
            return ToolResult.error(f"专家 {spec.name} 执行失败: {failed[:300]}")
        return ToolResult(content=final or "(专家未返回内容)")

    return Tool(
        name=f"delegate_to_{spec.name}",
        description=f"把子任务委派给专家「{spec.name}」：{spec.description}",
        parameters={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "交给该专家的完整任务描述（自包含、可独立执行）"}
            },
            "required": ["task"],
        },
        handler=handler,
        inject_ctx=True,
        timeout=600.0,
    )


def build_supervisor(
    *,
    llm: ChatModel,
    workers: list[WorkerSpec],
    name: str = "supervisor",
    system_prompt: str = "",
    max_steps: int = 6,
    token_budget: int = 0,
) -> Agent:
    roster = "\n".join(f"- {w.name}: {w.description}" for w in workers)
    default_prompt = (
        "你是团队主管，负责理解用户需求、把任务拆解并委派给合适的专家，最后整合各专家的结果给出完整回答。\n"
        f"可用专家：\n{roster}\n"
        "规则：能独立回答的简单问题直接回答；复杂任务先委派再汇总；委派时任务描述必须自包含。"
    )
    registry = ToolRegistry([_make_delegate_tool(w) for w in workers])
    return Agent(
        name=name,
        llm=llm,
        tools=registry,
        system_prompt=system_prompt or default_prompt,
        max_steps=max_steps,
        token_budget=token_budget,
    )

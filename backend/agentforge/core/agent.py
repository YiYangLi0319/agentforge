"""ReAct Agent 循环：思考(LLM) -> 行动(并行工具调用) -> 观察(结果回喂) 直至产出最终回答。

设计要点：
- 全过程以事件流（AsyncIterator[AgentEvent]）对外输出，天然支持 SSE 与持久化；
- 步数上限 / token 预算双重熔断，超限后强制模型直接作答；
- 工具并行执行，执行期间子事件（含嵌套子 Agent 的事件）通过队列实时合流；
- 高危工具走审批门（human-in-the-loop），拒绝后以"用户拒绝"回喂模型；
- 每步产出 Checkpoint 事件（消息快照），运行时据此支持断点恢复。
"""

import asyncio
import time
import traceback
from collections.abc import AsyncIterator

from agentforge.core.events import (
    AgentEvent,
    ApprovalDecided,
    ApprovalRequired,
    AssistantMessage,
    Checkpoint,
    LLMDelta,
    RunFailed,
    RunFinished,
    StepFinished,
    StepStarted,
    ToolFinished,
    ToolStarted,
)
from agentforge.core.llm.base import ChatModel, ChatResponse, StreamDelta
from agentforge.core.llm.pricing import estimate_cost
from agentforge.core.messages import Message, Role, ToolCall, Usage
from agentforge.core.runtime import RunContext
from agentforge.core.tools.base import Tool, ToolRegistry, ToolResult

_RESULT = "__result__"


class Agent:
    def __init__(
        self,
        *,
        name: str = "assistant",
        llm: ChatModel,
        tools: ToolRegistry | list[Tool] | None = None,
        system_prompt: str = "",
        max_steps: int = 8,
        token_budget: int = 0,  # 0 = 不限制
        temperature: float | None = None,
        stream_final: bool = True,
    ):
        self.name = name
        self.llm = llm
        self.tools = tools if isinstance(tools, ToolRegistry) else ToolRegistry(tools or [])
        self.system_prompt = system_prompt
        self.max_steps = max(max_steps, 1)
        self.token_budget = token_budget
        self.temperature = temperature
        self.stream_final = stream_final

    def _build_messages(self, input: list[Message] | str) -> list[Message]:
        history = [Message.user(input)] if isinstance(input, str) else list(input)
        if self.system_prompt and not (history and history[0].role == Role.SYSTEM):
            return [Message.system(self.system_prompt)] + history
        return history

    async def run(self, input: list[Message] | str, ctx: RunContext) -> AsyncIterator[AgentEvent]:
        messages = self._build_messages(input)
        usage_total = Usage()
        final_text = ""
        try:
            async with ctx.tracer.span(f"agent:{self.name}", "agent") as agent_span:
                for step in range(1, self.max_steps + 1):
                    yield StepStarted(step=step, agent=self.name)

                    over_budget = self.token_budget and usage_total.total_tokens >= self.token_budget
                    force_final = step == self.max_steps or bool(over_budget)
                    if force_final and step > 1:
                        reason = "token 预算已用尽" if over_budget else "已达最大步数"
                        messages.append(
                            Message.system(f"（{reason}，请基于已获得的信息直接给出最终回答，禁止再调用工具。）")
                        )

                    schemas = None if force_final or not len(self.tools) else self.tools.openai_schemas()

                    response: ChatResponse | None = None
                    async with ctx.tracer.span(f"llm:{self.llm.model}", "llm") as llm_span:
                        llm_span.input = {"step": step, "messages": len(messages), "tools": bool(schemas)}
                        async for ev in self.llm.stream(
                            messages, tools=schemas, temperature=self.temperature
                        ):
                            if isinstance(ev, StreamDelta):
                                if ev.text and self.stream_final:
                                    yield LLMDelta(text=ev.text, agent=self.name)
                            else:
                                response = ev
                        assert response is not None, "LLM 流未返回最终响应"
                        cost = estimate_cost(response.model or self.llm.model, response.usage)
                        llm_span.set_usage(response.usage, cost)
                        llm_span.set_output(
                            content=response.message.content[:500],
                            tool_calls=[tc.name for tc in response.message.tool_calls],
                            finish_reason=response.finish_reason,
                        )

                    usage_total = usage_total + response.usage
                    messages.append(response.message)

                    if response.message.tool_calls and not force_final:
                        yield AssistantMessage(
                            content=response.message.content,
                            tool_calls=response.message.tool_calls,
                            agent=self.name,
                        )
                        async for ev_or_msg in self._execute_tools(response.message.tool_calls, ctx):
                            if isinstance(ev_or_msg, Message):
                                messages.append(ev_or_msg)
                            else:
                                yield ev_or_msg
                        yield Checkpoint(messages=messages)
                        yield StepFinished(step=step, usage=response.usage, agent=self.name)
                        continue

                    final_text = response.message.content
                    yield AssistantMessage(content=final_text, final=True, agent=self.name)
                    yield Checkpoint(messages=messages)
                    yield StepFinished(step=step, usage=response.usage, agent=self.name)
                    break

                total_cost = estimate_cost(self.llm.model, usage_total)
                agent_span.set_output(final=final_text[:500], steps=step)
                yield RunFinished(
                    output={"text": final_text, "sources": ctx.state.get("sources", [])},
                    usage=usage_total,
                    cost=total_cost,
                    agent=self.name,
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 引擎不崩溃，失败以事件输出
            detail = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}"
            yield RunFailed(error=detail, agent=self.name)

    async def _execute_tools(
        self, tool_calls: list[ToolCall], ctx: RunContext
    ) -> AsyncIterator[AgentEvent | Message]:
        """并行执行一批工具调用；事件实时合流，结果按原调用顺序回喂。"""
        queue: asyncio.Queue = asyncio.Queue()
        tasks = [
            asyncio.create_task(self._run_one_tool(tc, ctx, queue)) for tc in tool_calls
        ]
        results: dict[str, Message] = {}
        finished = 0
        try:
            while finished < len(tasks):
                item = await queue.get()
                if isinstance(item, tuple) and item[0] == _RESULT:
                    _, call_id, msg = item
                    results[call_id] = msg
                    finished += 1
                else:
                    yield item
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
        for tc in tool_calls:
            yield results.get(
                tc.id, Message.tool_result(tc.id, tc.name, "[工具执行失败] 内部错误：结果缺失")
            )

    async def _run_one_tool(self, tc: ToolCall, ctx: RunContext, queue: asyncio.Queue) -> None:
        emit = queue.put_nowait
        started = time.perf_counter()
        try:
            result = await self._resolve_tool_result(tc, ctx, emit)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 保证队列一定收到结果，避免主循环卡死
            result = ToolResult.error(f"内部异常 {type(e).__name__}: {e}")

        duration_ms = int((time.perf_counter() - started) * 1000)
        emit(
            ToolFinished(
                tool_call_id=tc.id,
                tool=tc.name,
                ok=result.ok,
                result_preview=result.content[:500],
                duration_ms=duration_ms,
                agent=self.name,
            )
        )
        emit((_RESULT, tc.id, Message.tool_result(tc.id, tc.name, result.content)))

    async def _resolve_tool_result(self, tc: ToolCall, ctx: RunContext, emit) -> ToolResult:
        tool = self.tools.get(tc.name)
        emit(ToolStarted(tool_call_id=tc.id, tool=tc.name, arguments=tc.arguments, agent=self.name))

        result: ToolResult
        if tool is None:
            result = ToolResult.error(f"未知工具: {tc.name}，可用工具: {', '.join(self.tools.names())}")
        elif "__raw__" in tc.arguments:
            result = ToolResult.error("工具参数不是合法 JSON，请修正后重新调用")
        else:
            approved = True
            if tool.requires_approval and ctx.approval_gate is not None:
                emit(
                    ApprovalRequired(
                        tool_call_id=tc.id, tool=tc.name, arguments=tc.arguments, agent=self.name
                    )
                )
                approved = await ctx.approval_gate(tc, tool)
                emit(ApprovalDecided(tool_call_id=tc.id, approved=approved, agent=self.name))
            if not approved:
                result = ToolResult(ok=False, content="[已取消] 用户拒绝执行该工具，请换一种方式完成任务或直接说明。")
            else:
                async with ctx.tracer.span(f"tool:{tc.name}", "tool") as span:
                    span.input = dict(tc.arguments)
                    result = await tool.execute(tc.arguments, ctx.tool_context(emit=emit))
                    span.status = "ok" if result.ok else "error"
                    span.set_output(preview=result.content[:500])
                    if not result.ok:
                        span.error = result.content[:500]
        return result

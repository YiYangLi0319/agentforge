"""Planner-Executor 编排：LLM 生成带依赖关系的计划，按拓扑分波并行执行。"""

import logging
from collections.abc import AsyncIterator, Callable

from pydantic import BaseModel, Field

from agentforge.core.events import AgentEvent, PlanCreated, RunFinished
from agentforge.core.llm.base import ChatModel
from agentforge.core.llm.structured import complete_json
from agentforge.core.messages import Message
from agentforge.core.runtime import RunContext
from agentforge.core.streams import merge_streams

logger = logging.getLogger(__name__)


class PlanStep(BaseModel):
    id: str = Field(description="步骤唯一标识，如 s1")
    title: str = Field(description="步骤简述")
    description: str = Field(default="", description="执行该步骤的具体指令")
    depends_on: list[str] = Field(default_factory=list, description="依赖的前置步骤 id 列表")


class Plan(BaseModel):
    goal: str = Field(default="", description="总目标")
    steps: list[PlanStep] = Field(default_factory=list)


async def create_plan(llm: ChatModel, goal: str, context: str = "", max_steps: int = 5) -> Plan:
    prompt = (
        f"你是任务规划专家。请把下面的目标拆解为最多 {max_steps} 个可执行步骤，"
        "步骤之间如有先后依赖用 depends_on 表示（无依赖的步骤会被并行执行）。\n\n"
        f"目标：{goal}"
    )
    if context:
        prompt += f"\n\n补充背景：{context}"
    plan, _ = await complete_json(llm, [Message.user(prompt)], Plan)
    if not plan.goal:
        plan.goal = goal
    # 清理非法依赖，防御模型幻觉
    ids = {s.id for s in plan.steps}
    for s in plan.steps:
        s.depends_on = [d for d in s.depends_on if d in ids and d != s.id]
    return plan


def topological_waves(plan: Plan) -> list[list[PlanStep]]:
    """按依赖分波：同一波内的步骤可并行。存在环时把剩余步骤合为最后一波。"""
    remaining = {s.id: s for s in plan.steps}
    done: set[str] = set()
    waves: list[list[PlanStep]] = []
    while remaining:
        wave = [s for s in remaining.values() if all(d in done for d in s.depends_on)]
        if not wave:  # 环：直接全部放最后一波，保证不死循环
            wave = list(remaining.values())
        waves.append(wave)
        for s in wave:
            done.add(s.id)
            remaining.pop(s.id)
    return waves


# worker 工厂：输入 (step, 之前步骤的结果) 返回该步骤的事件流
WorkerFactory = Callable[[PlanStep, dict[str, str]], AsyncIterator[AgentEvent]]


class PlanExecutor:
    """按波次执行计划；每个步骤由 worker 工厂产出事件流，结果自动汇总注入后续步骤。"""

    def __init__(self, worker_factory: WorkerFactory):
        self.worker_factory = worker_factory
        self.results: dict[str, str] = {}
        self.failed: set[str] = set()

    async def execute(self, plan: Plan, ctx: RunContext) -> AsyncIterator[AgentEvent]:
        yield PlanCreated(plan=plan.model_dump())
        async with ctx.tracer.span("plan_executor", "chain", input={"steps": len(plan.steps)}):
            for wave in topological_waves(plan):
                runnable = [s for s in wave if not (set(s.depends_on) & self.failed)]
                for s in wave:
                    if s not in runnable:
                        self.failed.add(s.id)
                        logger.warning("步骤 %s 因依赖失败被跳过", s.id)
                if not runnable:
                    continue

                async def tagged(step: PlanStep) -> AsyncIterator[AgentEvent]:
                    async for ev in self.worker_factory(step, dict(self.results)):
                        if isinstance(ev, RunFinished):
                            self.results[step.id] = str(ev.output.get("text", ""))
                        elif ev.type == "run_failed":
                            self.failed.add(step.id)
                        yield ev

                async for ev in merge_streams([tagged(s) for s in runnable]):
                    yield ev

"""编排层测试：计划拓扑分波、并行执行与结果传递、Supervisor 委派与事件上浮。"""

import asyncio
import json
import time

from agentforge.core.agent import Agent
from agentforge.core.events import RunFinished
from agentforge.core.llm.mock import MockChatModel
from agentforge.core.messages import Message, ToolCall
from agentforge.core.planner import Plan, PlanExecutor, PlanStep, create_plan, topological_waves
from agentforge.core.streams import merge_streams
from agentforge.core.supervisor import WorkerSpec, build_supervisor


def test_topological_waves_and_cycle_safety():
    plan = Plan(
        goal="g",
        steps=[
            PlanStep(id="s1", title="a"),
            PlanStep(id="s2", title="b"),
            PlanStep(id="s3", title="c", depends_on=["s1", "s2"]),
        ],
    )
    waves = topological_waves(plan)
    assert [sorted(s.id for s in w) for w in waves] == [["s1", "s2"], ["s3"]]

    cyclic = Plan(
        goal="g",
        steps=[PlanStep(id="a", title="a", depends_on=["b"]), PlanStep(id="b", title="b", depends_on=["a"])],
    )
    assert len(topological_waves(cyclic)) == 1  # 有环不死循环


async def test_create_plan_cleans_invalid_deps():
    llm = MockChatModel(
        script=[
            json.dumps(
                {
                    "goal": "研究",
                    "steps": [
                        {"id": "s1", "title": "T1", "description": "", "depends_on": ["ghost", "s1"]},
                        {"id": "s2", "title": "T2", "description": "", "depends_on": ["s1"]},
                    ],
                },
                ensure_ascii=False,
            )
        ]
    )
    plan = await create_plan(llm, "研究某主题")
    assert plan.steps[0].depends_on == []  # 幻觉依赖被清理
    assert plan.steps[1].depends_on == ["s1"]


async def test_plan_executor_parallel_and_result_passing(run_ctx):
    timestamps: dict[str, float] = {}

    def worker_factory(step: PlanStep, prior: dict[str, str]):
        async def gen():
            timestamps[step.id] = time.perf_counter()
            await asyncio.sleep(0.15)
            text = f"{step.id}-result"
            if step.id == "s3":
                assert prior["s1"] == "s1-result" and prior["s2"] == "s2-result"
            yield RunFinished(output={"text": text}, agent=step.id)

        return gen()

    plan = Plan(
        goal="g",
        steps=[
            PlanStep(id="s1", title="a"),
            PlanStep(id="s2", title="b"),
            PlanStep(id="s3", title="c", depends_on=["s1", "s2"]),
        ],
    )
    executor = PlanExecutor(worker_factory)
    events = [ev async for ev in executor.execute(plan, run_ctx)]
    assert events[0].type == "plan_created"
    assert executor.results == {"s1": "s1-result", "s2": "s2-result", "s3": "s3-result"}
    # s1/s2 并行（启动间隔远小于 sleep），s3 在其后
    assert abs(timestamps["s1"] - timestamps["s2"]) < 0.1
    assert timestamps["s3"] > max(timestamps["s1"], timestamps["s2"])


async def test_plan_executor_skips_dependents_of_failed(run_ctx):
    from agentforge.core.events import RunFailed

    def worker_factory(step: PlanStep, prior: dict[str, str]):
        async def gen():
            if step.id == "s1":
                yield RunFailed(error="炸了", agent=step.id)
            else:
                yield RunFinished(output={"text": "ok"}, agent=step.id)

        return gen()

    plan = Plan(
        goal="g",
        steps=[PlanStep(id="s1", title="a"), PlanStep(id="s2", title="b", depends_on=["s1"])],
    )
    executor = PlanExecutor(worker_factory)
    _ = [ev async for ev in executor.execute(plan, run_ctx)]
    assert "s1" in executor.failed and "s2" not in executor.results


async def test_supervisor_delegates_and_forwards_child_events(run_ctx):
    def build_worker() -> Agent:
        return Agent(name="researcher", llm=MockChatModel(script=["调研结论：市场规模约百亿"]), max_steps=2)

    supervisor_llm = MockChatModel(
        script=[
            Message.assistant(
                tool_calls=[
                    ToolCall(id="c1", name="delegate_to_researcher", arguments={"task": "调研市场规模"})
                ]
            ),
            "综合专家结论：市场规模约百亿。",
        ]
    )
    sup = build_supervisor(
        llm=supervisor_llm,
        workers=[WorkerSpec(name="researcher", description="擅长调研", build=build_worker)],
    )
    events = [ev async for ev in sup.run("帮我调研市场", run_ctx)]
    # 子 Agent 的事件上浮（agent 字段为 researcher）
    assert any(e.agent == "researcher" for e in events)
    final = events[-1]
    assert final.type == "run_finished" and "百亿" in final.output["text"]
    # 委派工具的结果注入了主管的上下文
    tool_msgs = [m for m in supervisor_llm.calls[1]["messages"] if m["role"] == "tool"]
    assert any("百亿" in m["content"] for m in tool_msgs)


async def test_merge_streams_propagates_exception():
    async def ok_gen():
        yield 1
        await asyncio.sleep(0.05)
        yield 2

    async def bad_gen():
        yield 3
        raise RuntimeError("stream broken")

    items = []
    try:
        async for item in merge_streams([ok_gen(), bad_gen()]):
            items.append(item)
        raise AssertionError("应当抛出异常")
    except RuntimeError as e:
        assert "stream broken" in str(e)
    assert 1 in items and 3 in items

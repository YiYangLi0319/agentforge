"""深度研究流水线测试：离线 Mock 模式下全流程可跑通，事件序列与产物完整。"""

import json

from agentforge.agents.deep_research import run_deep_research
from agentforge.core.llm.base import ChatResponse, StreamDelta
from agentforge.core.llm.mock import MockChatModel
from agentforge.core.messages import Message, Usage
from agentforge.core.tools.web_search import MockSearchProvider


class LoopJudge(MockChatModel):
    """受控评审模型：评审(Review) 前 fail_times 次判不通过，之后通过；其余(如 Synthesis)走自动模式。"""

    def __init__(self, fail_times: int):
        super().__init__()
        self.fail_times = fail_times
        self.review_calls = 0

    async def complete(
        self,
        messages,
        *,
        tools=None,
        temperature=None,
        max_tokens=None,
        response_format=None,
        schema_hint=None,
    ):
        props = (schema_hint or {}).get("properties", {})
        if "passed" in props and "completeness" in props:  # Review schema
            self.review_calls += 1
            passed = self.review_calls > self.fail_times
            data = {
                "passed": passed,
                "completeness": 4,
                "citation_quality": 5 if passed else 2,
                "logic": 4,
                "feedback": "请补强事实句的 [n] 引用",
            }
            content = json.dumps(data, ensure_ascii=False)
            return ChatResponse(
                message=Message.assistant(content),
                usage=Usage(prompt_tokens=5, completion_tokens=5),
                model="mock",
            )
        return await super().complete(
            messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            schema_hint=schema_hint,
        )


class GroundedWriter(MockChatModel):
    """写作阶段始终输出事实句均带有效编号的报告，便于隔离测试评审循环。"""

    async def stream(self, messages, **kwargs):
        prompt = "\n".join(message.content for message in messages)
        if "资深研究报告撰写人" in prompt:
            text = (
                "# 测试报告\n\n## 摘要\n\n调研数据显示该主题已有明确进展 [1]。\n\n"
                "## 分析\n\n根据来源，相关方案已形成可验证的实践路径 [1]。\n\n"
                "## 结论与建议\n\n报告建议继续依据已核验资料推进 [1]。"
            )
            yield StreamDelta(text=text)
            yield ChatResponse(
                message=Message.assistant(text),
                usage=Usage(prompt_tokens=10, completion_tokens=20),
                model="mock",
            )
            return
        async for event in super().stream(messages, **kwargs):
            yield event


async def test_deep_research_pipeline_offline(run_ctx):
    run_ctx.services["search"] = MockSearchProvider()
    llm = MockChatModel()

    events = [
        ev
        async for ev in run_deep_research(
            "2026 年国内 AI Agent 行业发展趋势",
            run_ctx,
            llm=llm,
            max_workers=2,
            worker_max_steps=3,
        )
    ]
    types = [e.type for e in events]

    assert "plan_created" in types
    assert types.count("research_task_started") >= 1
    assert types.count("research_task_finished") == types.count("research_task_started")
    assert "sources_updated" in types
    assert "report_draft" in types
    assert "report_review" in types
    assert types[-1] == "run_finished"

    finished = events[-1]
    report = finished.output["report"]
    assert report and "## 参考来源" in report
    assert finished.output["sources"], "报告应有引用来源"
    assert finished.usage.total_tokens > 0

    # 并行搜索员事件带 agent 标记
    workers = {e.agent for e in events if e.type == "research_task_started"}
    assert all(w and w.startswith("搜索员") for w in workers)

    # 追踪覆盖各阶段
    span_names = [s.name for s in run_ctx.tracer.spans]
    assert "research:plan" in span_names
    assert "research:synthesize" in span_names
    assert "research:write" in span_names
    assert "research:review" in span_names


async def test_research_iterative_revision_until_pass(run_ctx):
    """首版未达标 -> 迭代修订 -> 复审通过后停止（Reflexion 循环）。"""
    run_ctx.services["search"] = MockSearchProvider()
    judge = LoopJudge(fail_times=1)  # 第 1 次评审不通过，第 2 次通过
    events = [
        ev
        async for ev in run_deep_research(
            "测试迭代修订", run_ctx, llm=GroundedWriter(), judge_llm=judge, max_workers=1, max_revisions=3
        )
    ]
    reviews = [e for e in events if e.type == "report_review"]
    drafts = [e for e in events if e.type == "report_draft"]
    # 初评(不过) + 修订后复评(过) = 2 次评审；草稿：初稿 + 1 次修订
    assert len(reviews) == 2
    assert reviews[0].passed is False and reviews[-1].passed is True
    assert {d.revision for d in drafts} == {0, 1}
    finished = events[-1]
    assert finished.type == "run_finished" and finished.output["revisions"] == 1


async def test_research_stops_at_max_revisions_and_keeps_best(run_ctx):
    """始终不达标时：达到最大修订轮数后停止，仍产出报告（保底最优版本）。"""
    run_ctx.services["search"] = MockSearchProvider()
    judge = LoopJudge(fail_times=99)  # 永远不通过
    events = [
        ev
        async for ev in run_deep_research(
            "测试上限", run_ctx, llm=MockChatModel(), judge_llm=judge, max_workers=1, max_revisions=2
        )
    ]
    reviews = [e for e in events if e.type == "report_review"]
    # 初评 + 2 轮修订复评 = 3 次
    assert len(reviews) == 3
    assert all(r.passed is False for r in reviews)
    finished = events[-1]
    assert finished.type == "run_finished"
    assert finished.output["revisions"] == 2
    assert finished.output["report"], "即使未达标也应交出报告"


async def test_deep_research_fails_when_all_workers_fail(run_ctx):
    class ExplodingSearch(MockSearchProvider):
        async def search(self, query, max_results=5):
            raise RuntimeError("总是失败")

    # 搜索员的 LLM 每次都强制产出空最终回答 -> findings 为空
    llm = MockChatModel(
        script=[
            # plan 阶段（schema 自动生成不走脚本，这里先给 plan JSON）
            '{"topic": "t", "sub_questions": [{"id": "q1", "question": "子问题", "queries": []}]}',
            "",  # 搜索员直接给出空回答
        ]
    )
    run_ctx.services["search"] = ExplodingSearch()
    events = [
        ev
        async for ev in run_deep_research("主题", run_ctx, llm=llm, max_workers=1, worker_max_steps=2)
    ]
    assert events[-1].type == "run_failed"

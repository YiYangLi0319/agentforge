"""深度研究 Agent：规划 -> 并行搜索子 Agent -> 证据聚合交叉验证 -> 撰写 -> 评审修订。

工作流式编排（区别于 Supervisor 的自主委派）：阶段确定、并行度可控、成本可预算，
这是生产环境做"研究报告"这类结构化任务更可靠的形态。
"""

import logging
from collections.abc import AsyncIterator

from pydantic import BaseModel, Field

from agentforge.core.agent import Agent
from agentforge.core.events import (
    AgentEvent,
    LLMDelta,
    PlanCreated,
    ReportDraft,
    ReportReview,
    ResearchPhaseChanged,
    ResearchTaskFinished,
    ResearchTaskStarted,
    RunFailed,
    RunFinished,
    SourcesUpdated,
)
from agentforge.core.llm.base import ChatModel, StreamDelta
from agentforge.core.llm.pricing import estimate_cost
from agentforge.core.llm.structured import complete_json
from agentforge.core.messages import Message
from agentforge.core.runtime import RunContext
from agentforge.core.streams import merge_streams
from agentforge.core.tools.base import ToolRegistry
from agentforge.core.tools.web_fetch import web_fetch
from agentforge.core.tools.web_search import web_search
from agentforge.rag.citations import CitationAudit, audit_citations, extract_cited_ids, public_source

logger = logging.getLogger(__name__)


class SubQuestion(BaseModel):
    id: str = Field(description="子问题编号，如 q1")
    question: str = Field(description="需要独立调研的子问题")
    queries: list[str] = Field(default_factory=list, description="建议的搜索关键词，1-2 个")


class ResearchPlan(BaseModel):
    topic: str = Field(description="研究主题的精确表述")
    sub_questions: list[SubQuestion] = Field(description="拆解出的子问题，2-4 个")


class Synthesis(BaseModel):
    key_findings: list[str] = Field(description="跨来源交叉验证后的关键发现，每条附引用编号")
    conflicts: list[str] = Field(default_factory=list, description="不同来源间互相矛盾的信息点")
    outline: list[str] = Field(description="建议的报告章节提纲，3-5 章")


class Review(BaseModel):
    passed: bool = Field(description="报告是否达到发布标准")
    completeness: int = Field(ge=1, le=5, description="完整性 1-5")
    citation_quality: int = Field(ge=1, le=5, description="引用规范性 1-5")
    logic: int = Field(ge=1, le=5, description="逻辑清晰度 1-5")
    feedback: str = Field(default="", description="具体修改意见")


_SEARCHER_PROMPT = (
    "你是资深调研员。针对给定的子问题：先用 web_search 搜索（可多次、换关键词），"
    "对高价值结果用 web_fetch 阅读原文，然后输出调研纪要。\n"
    "要求：纪要用要点列出事实与数据，每条事实句末必须标注来源编号（工具结果中的 [n]）；"
    "注明信息的时间；来源之间有矛盾时明确指出。"
)


def _render_sources(sources: list[dict], limit: int) -> str:
    lines = []
    for s in sources[:limit]:
        loc = s.get("url") or s.get("filename", "")
        evidence = str(s.get("evidence") or s.get("snippet") or "")[:1500]
        verification = "已读取原文" if s.get("verified") else "仅搜索摘要"
        lines.append(f"[{s['id']}] {s['title']} {loc}（{verification}）\n证据：{evidence}")
    return "\n".join(lines) if lines else "（暂无来源）"


async def run_deep_research(
    query: str,
    ctx: RunContext,
    *,
    llm: ChatModel,
    judge_llm: ChatModel | None = None,
    max_workers: int = 3,
    max_sources: int = 12,
    worker_max_steps: int = 4,
    max_revisions: int = 2,
    require_verified_sources: bool = False,
) -> AsyncIterator[AgentEvent]:
    judge = judge_llm or llm
    try:
        # ---------- 阶段 1：研究规划 ----------
        yield ResearchPhaseChanged(phase="planning", agent="规划员")
        async with ctx.tracer.span("research:plan", "llm") as span:
            plan, usage = await complete_json(
                llm,
                [
                    Message.user(
                        "你是研究规划专家。把下面的研究主题拆解为可并行调研的子问题（2-4 个），"
                        f"每个子问题给出搜索关键词建议。\n\n研究主题：{query}"
                    )
                ],
                ResearchPlan,
            )
            span.set_usage(usage, estimate_cost(llm.model, usage))
            span.set_output(sub_questions=len(plan.sub_questions))
        plan.sub_questions = plan.sub_questions[:max_workers]
        yield PlanCreated(plan=plan.model_dump(), agent="规划员")
        yield ResearchPhaseChanged(
            phase="searching",
            total_tasks=len(plan.sub_questions),
            agent="研究团队",
        )

        # ---------- 阶段 2：并行搜索子 Agent ----------
        findings: dict[str, str] = {}

        def make_worker_stream(i: int, sub: SubQuestion) -> AsyncIterator[AgentEvent]:
            worker_name = f"搜索员-{i + 1}"

            async def gen() -> AsyncIterator[AgentEvent]:
                yield ResearchTaskStarted(task_id=sub.id, title=sub.question, agent=worker_name)
                agent = Agent(
                    name=worker_name,
                    llm=llm,
                    tools=ToolRegistry([web_search, web_fetch]),
                    system_prompt=_SEARCHER_PROMPT,
                    max_steps=worker_max_steps,
                    stream_final=False,  # 纪要不逐字推流，避免与报告流混淆
                )
                hint = f"（建议搜索词：{'；'.join(sub.queries)}）" if sub.queries else ""
                ok, summary = False, ""
                async for ev in agent.run(f"子问题：{sub.question}\n{hint}", ctx):
                    if isinstance(ev, RunFinished):
                        summary = str(ev.output.get("text", ""))
                        ok = bool(summary.strip())
                    elif isinstance(ev, RunFailed):
                        summary = f"调研失败：{ev.error[:200]}"
                    elif ev.type != "checkpoint":
                        yield ev
                if ok:
                    findings[sub.id] = summary
                yield ResearchTaskFinished(
                    task_id=sub.id,
                    ok=ok,
                    summary=summary[:300],
                    evidence_count=len(extract_cited_ids(summary)),
                    agent=worker_name,
                )

            return gen()

        streams = [make_worker_stream(i, sub) for i, sub in enumerate(plan.sub_questions)]
        completed_tasks = 0
        async for ev in merge_streams(streams):
            yield ev
            if isinstance(ev, ResearchTaskFinished):
                completed_tasks += 1
                yield ResearchPhaseChanged(
                    phase="searching",
                    completed_tasks=completed_tasks,
                    total_tasks=len(plan.sub_questions),
                    agent="研究团队",
                )

        sources: list[dict] = ctx.state.get("sources", [])
        yield SourcesUpdated(sources=[public_source(s) for s in sources[:max_sources]])
        if not findings:
            yield RunFailed(error="所有搜索子任务均失败，无法生成报告")
            return

        # ---------- 阶段 3：证据聚合与交叉验证 ----------
        yield ResearchPhaseChanged(
            phase="synthesizing",
            completed_tasks=completed_tasks,
            total_tasks=len(plan.sub_questions),
            agent="分析员",
        )
        notes = "\n\n".join(
            f"【{sub.question}】\n{findings.get(sub.id, '（无结果）')}" for sub in plan.sub_questions
        )
        async with ctx.tracer.span("research:synthesize", "llm") as span:
            synthesis, usage = await complete_json(
                judge,
                [
                    Message.user(
                        "你是研究分析师。请对多名调研员的纪要做交叉验证与聚合：\n"
                        "1) 提炼关键发现（保留引用编号 [n]）；2) 指出来源间互相矛盾的信息；"
                        "3) 给出报告章节提纲。\n\n"
                        f"研究主题：{plan.topic}\n\n调研纪要：\n{notes}"
                    )
                ],
                Synthesis,
            )
            span.set_usage(usage, estimate_cost(judge.model, usage))

        # ---------- 阶段 4：撰写报告（流式） ----------
        yield ResearchPhaseChanged(
            phase="writing",
            completed_tasks=completed_tasks,
            total_tasks=len(plan.sub_questions),
            agent="写作员",
        )
        sources_text = _render_sources(sources, max_sources)
        writer_prompt = (
            "你是资深研究报告撰写人。基于调研材料撰写一份结构化的中文 Markdown 研究报告。\n"
            "要求：\n"
            "- 结构：# 报告标题、## 摘要、按提纲展开的分析章节、## 结论与建议\n"
            "- 所有事实与数据句末标注来源编号 [n]（编号必须来自下方来源列表）\n"
            "- 来源矛盾处如实说明；不要编造来源列表之外的信息\n"
            "- 不要自行输出参考来源清单（系统会自动附加）\n\n"
            f"研究主题：{plan.topic}\n\n章节提纲：{synthesis.outline}\n\n"
            f"关键发现：\n" + "\n".join(f"- {k}" for k in synthesis.key_findings) + "\n\n"
            f"矛盾提示：{synthesis.conflicts or '无'}\n\n调研纪要：\n{notes}\n\n"
            f"可引用来源列表：\n{sources_text}"
        )

        async def write_report(extra_instruction: str = "") -> AsyncIterator[str | tuple]:
            msgs = [Message.user(writer_prompt + extra_instruction)]
            async with ctx.tracer.span("research:write", "llm") as span:
                async for ev in llm.stream(msgs):
                    if isinstance(ev, StreamDelta):
                        yield ev.text
                    else:
                        span.set_usage(ev.usage, estimate_cost(llm.model, ev.usage))
                        yield ("__final__", ev.message.content)

        report_md = ""
        async for piece in write_report():
            if isinstance(piece, tuple):
                report_md = piece[1]
            else:
                yield LLMDelta(text=piece, channel="report", agent="写作员")
        yield ReportDraft(markdown=report_md, revision=0, agent="写作员")

        # ---------- 阶段 5：评审 + 迭代修订循环（Reflexion / self-refine）----------
        available_ids = [s["id"] for s in sources[:max_sources]]
        evidence_text = _render_sources(sources, max_sources)

        async def review_report(md: str) -> tuple[Review, CitationAudit]:
            audit = audit_citations(md, sources[:max_sources], require_citations=True)
            if require_verified_sources and audit.cited_ids and audit.verified_ratio == 0:
                audit = audit.model_copy(
                    update={
                        "passed": False,
                        "issues": [*audit.issues, "发布模式要求至少引用一个已抓取原文的来源"],
                    }
                )
            async with ctx.tracer.span("research:review", "llm") as span:
                rv, usage = await complete_json(
                    judge,
                    [
                        Message.user(
                            "你是严格的报告评审人。请按完整性、引用规范性（事实句是否标注 [n] 且编号存在）、"
                            "逻辑清晰度三个维度评审下面的研究报告，并判断是否达到发布标准。"
                            "你必须根据证据原文判断陈述是否得到来源支持，不得只检查编号格式。\n\n"
                            f"可用来源编号：{available_ids}\n\n来源证据：\n{evidence_text}\n\n报告：\n{md}"
                        )
                    ],
                    Review,
                )
                span.set_usage(usage, estimate_cost(judge.model, usage))
            quality_floor = min(rv.completeness, rv.citation_quality, rv.logic) >= 3
            if not audit.passed or not quality_floor:
                feedback = "；".join([rv.feedback, *audit.issues]).strip("；")
                rv = rv.model_copy(
                    update={
                        "passed": False,
                        "citation_quality": min(rv.citation_quality, 2) if not audit.passed else rv.citation_quality,
                        "feedback": feedback,
                    }
                )
            return rv, audit

        def score_of(rv: Review, audit: CitationAudit) -> float:
            return rv.completeness + rv.citation_quality + rv.logic + audit.coverage * 5 - len(audit.invalid_ids) * 5

        def emit_review(rv: Review, audit: CitationAudit) -> ReportReview:
            return ReportReview(
                passed=rv.passed,
                scores={
                    "completeness": rv.completeness,
                    "citation_quality": rv.citation_quality,
                    "logic": rv.logic,
                },
                feedback=rv.feedback,
                audit=audit.model_dump(),
                agent="评审员",
            )

        yield ResearchPhaseChanged(phase="reviewing", agent="评审员")
        review, citation_audit = await review_report(report_md)
        yield emit_review(review, citation_audit)
        # 保底：记录历次评分最高的版本，即使多轮仍不达标也交出最好的一版
        best_md, best_review, best_audit = report_md, review, citation_audit
        best_score = score_of(review, citation_audit)

        revision = 0
        while not review.passed and revision < max_revisions:
            revision += 1
            yield ResearchPhaseChanged(phase="revising", revision=revision, agent="写作员")
            revised = ""
            async for piece in write_report(
                f"\n\n这是第 {revision} 次修订。上一版评审意见如下，请针对性修改，"
                "重点补强被扣分的维度（尤其是引用规范：事实句务必标注真实存在的 [n]）。"
                f"\n评审意见：{review.feedback}\n\n上一版报告全文：\n{report_md}"
            ):
                if isinstance(piece, tuple):
                    revised = piece[1]
                else:
                    yield LLMDelta(text=piece, channel="report", agent="写作员")
            if revised:
                report_md = revised
            yield ReportDraft(markdown=report_md, revision=revision, agent="写作员")

            yield ResearchPhaseChanged(phase="reviewing", revision=revision, agent="评审员")
            review, citation_audit = await review_report(report_md)
            yield emit_review(review, citation_audit)
            if score_of(review, citation_audit) >= best_score:
                best_md, best_review, best_audit = report_md, review, citation_audit
                best_score = score_of(review, citation_audit)
            if review.passed:
                break

        # 选定终稿：通过则用当前版本，否则回退到历次评分最高的版本
        if not review.passed:
            report_md, review, citation_audit = best_md, best_review, best_audit

        # ---------- 收尾：附加参考来源，汇总用量 ----------
        used_ids = extract_cited_ids(report_md)
        final_sources_internal = [s for s in sources if s["id"] in used_ids] or sources[:max_sources]
        final_sources = [public_source(s) for s in final_sources_internal]
        refs = "\n".join(
            f"[{s['id']}] {s['title']}{'（' + s['url'] + '）' if s.get('url') else ''}"
            for s in final_sources
        )
        full_report = f"{report_md}\n\n## 参考来源\n\n{refs}"

        usage_total, cost_total = ctx.tracer.totals()
        quality_passed = bool(review.passed and citation_audit.passed)
        yield ResearchPhaseChanged(
            phase="completed" if quality_passed else "needs_review",
            revision=revision,
            agent="研究团队",
        )
        yield RunFinished(
            output={
                "text": full_report,
                "report": full_report,
                "plan": plan.model_dump(),
                "sources": final_sources,
                "review": {**review.model_dump(), "audit": citation_audit.model_dump()},
                "revisions": revision,
                "quality_passed": quality_passed,
            },
            usage=usage_total,
            cost=cost_total,
            agent="研究团队",
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("深度研究流水线失败")
        yield RunFailed(error=f"{type(e).__name__}: {e}")

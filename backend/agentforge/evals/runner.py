"""评估运行器 CLI：一键跑 检索 / RAG 问答 / Agent 任务 三类评估并输出报告。

用法（在 backend 目录下）：
    python -m agentforge.evals.runner retrieval          # 检索质量：Recall@K / MRR / nDCG
    python -m agentforge.evals.runner rag                # 端到端问答：LLM-as-judge 三维评分
    python -m agentforge.evals.runner agent              # Agent 任务完成率
    python -m agentforge.evals.runner all                # 全部

离线（Mock）模式可跑通全流程验证管道；接入真实 LLM/Embedding 后得到有效评估结论。
报告输出到 backend/evals_reports/，同时落库 eval_records 表。
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select

from agentforge.config import Settings, get_settings
from agentforge.core.agent import Agent
from agentforge.core.events import RunFinished
from agentforge.core.llm.registry import build_chat_model, build_embeddings, build_judge_model
from agentforge.core.runtime import RunContext
from agentforge.core.tools.base import ToolRegistry
from agentforge.core.tools.python_sandbox import python_execute
from agentforge.core.tools.retrieval import search_knowledge_base
from agentforge.db.base import build_engine, build_sessionmaker, init_db
from agentforge.db.models import Chunk, Document, EvalRecord, KnowledgeBase, User
from agentforge.evals.gates import gate_specs
from agentforge.evals.judge import judge_answer, judge_task
from agentforge.evals.metrics import aggregate, hit_rate_at_k, mrr, ndcg_at_k, recall_at_k
from agentforge.rag.citations import audit_citations
from agentforge.rag.pipeline import RagPipeline
from agentforge.rag.retriever import HybridRetriever
from agentforge.services.ingestion import ingest_document

DATASETS_DIR = Path(__file__).parent / "datasets"
SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples" / "kb"
REPORTS_DIR = Path("evals_reports")

EVAL_USER = "eval_bot"
EVAL_KB = "评估语料（内置样例）"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class EvalContext:
    """评估环境：独立装配引擎组件（不依赖 API 服务）。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.engine = build_engine(settings.database_url)
        self.sessions = build_sessionmaker(self.engine)
        self.llm = build_chat_model(settings)
        self.judge = build_judge_model(settings)
        self.embeddings = build_embeddings(settings)
        self.retriever = HybridRetriever(self.sessions, self.embeddings)
        self.kb_id = ""

    async def setup(self) -> None:
        await init_db(self.engine)
        async with self.sessions() as db:
            user = (
                await db.execute(select(User).where(User.username == EVAL_USER))
            ).scalar_one_or_none()
            if user is None:
                user = User(username=EVAL_USER, password_hash="!")
                db.add(user)
                await db.commit()
            kb = (
                await db.execute(
                    select(KnowledgeBase).where(
                        KnowledgeBase.user_id == user.id, KnowledgeBase.name == EVAL_KB
                    )
                )
            ).scalar_one_or_none()
            if kb is None:
                kb = KnowledgeBase(user_id=user.id, name=EVAL_KB)
                db.add(kb)
                await db.commit()
            self.kb_id = kb.id
            chunk_count = (
                await db.execute(select(func.count(Chunk.id)).where(Chunk.kb_id == kb.id))
            ).scalar() or 0

        if chunk_count == 0:
            print(f"[setup] 首次运行，正在导入评估语料（{SAMPLES_DIR}）...")
            for path in sorted(SAMPLES_DIR.glob("*.md")):
                async with self.sessions() as db:
                    doc = Document(kb_id=self.kb_id, filename=path.name, size=path.stat().st_size)
                    db.add(doc)
                    await db.commit()
                    doc_id = doc.id
                await ingest_document(self.sessions, self.embeddings, doc_id, path.name, path.read_bytes())

    async def close(self) -> None:
        await self.engine.dispose()


async def run_retrieval_suite(ectx: EvalContext, dataset: Path, top_k: int) -> dict:
    cases = load_jsonl(dataset)
    rows = []
    for case in cases:
        results = await ectx.retriever.search([ectx.kb_id], case["query"], top_k=top_k)
        # chunk 级结果 -> 文档级排名（保序去重）
        ranked_docs: list[str] = []
        for r in results:
            if r.filename not in ranked_docs:
                ranked_docs.append(r.filename)
        relevant = set(case["relevant_docs"])
        rows.append(
            {
                "query": case["query"],
                "retrieved": ranked_docs,
                "relevant": sorted(relevant),
                "recall@k": recall_at_k(ranked_docs, relevant, top_k),
                "hit@k": hit_rate_at_k(ranked_docs, relevant, top_k),
                "mrr": mrr(ranked_docs, relevant),
                "ndcg@k": ndcg_at_k(ranked_docs, relevant, top_k),
            }
        )
    metrics = {
        f"recall@{top_k}": aggregate([r["recall@k"] for r in rows]),
        f"hit_rate@{top_k}": aggregate([r["hit@k"] for r in rows]),
        "mrr": aggregate([r["mrr"] for r in rows]),
        f"ndcg@{top_k}": aggregate([r["ndcg@k"] for r in rows]),
        "cases": len(rows),
    }
    return {"suite": "retrieval", "dataset": dataset.name, "metrics": metrics, "detail": rows}


async def run_rag_suite(ectx: EvalContext, dataset: Path) -> dict:
    cases = load_jsonl(dataset)
    rows = []
    for case in cases:
        ctx = RunContext(user_id="eval", kb_ids=[ectx.kb_id])
        ctx.services.update(
            {
                "retriever": ectx.retriever,
                "rag_pipeline": RagPipeline(ectx.retriever, ectx.llm),
                "settings": ectx.settings,
            }
        )
        agent = Agent(
            name="rag_eval",
            llm=ectx.llm,
            tools=ToolRegistry([search_knowledge_base]),
            system_prompt=(
                "你是企业知识库助手。回答前必须先调用 search_knowledge_base 检索，"
                "引用检索内容的句末标注来源编号 [n]，检索不到时如实说明。"
            ),
            max_steps=4,
            stream_final=False,
        )
        answer, t0 = "", time.perf_counter()
        async for ev in agent.run(case["question"], ctx):
            if isinstance(ev, RunFinished):
                answer = str(ev.output.get("text", ""))
        latency_ms = int((time.perf_counter() - t0) * 1000)
        usage, cost = ctx.tracer.totals()

        sources = ctx.state.get("sources", [])
        context_text = "\n".join(s.get("evidence") or s.get("snippet", "") for s in sources)
        citation_audit = audit_citations(answer, sources, require_citations=True)
        judgement, _ = await judge_answer(
            ectx.judge,
            question=case["question"],
            answer=answer,
            context=context_text,
            reference=case.get("reference", ""),
        )
        rows.append(
            {
                "question": case["question"],
                "answer": answer[:300],
                "faithfulness": judgement.faithfulness,
                "relevance": judgement.relevance,
                "citation": judgement.citation,
                "citation_integrity": citation_audit.passed,
                "citation_coverage": citation_audit.coverage,
                "invalid_citations": citation_audit.invalid_ids,
                "reason": judgement.reason,
                "latency_ms": latency_ms,
                "tokens": usage.total_tokens,
                "cost": cost,
            }
        )
    metrics = {
        "faithfulness": aggregate([r["faithfulness"] for r in rows]),
        "relevance": aggregate([r["relevance"] for r in rows]),
        "citation": aggregate([r["citation"] for r in rows]),
        "citation_integrity_rate": aggregate([1.0 if r["citation_integrity"] else 0.0 for r in rows]),
        "citation_coverage": aggregate([r["citation_coverage"] for r in rows]),
        "avg_latency_ms": int(aggregate([float(r["latency_ms"]) for r in rows])),
        "avg_tokens": int(aggregate([float(r["tokens"]) for r in rows])),
        "total_cost": round(sum(r["cost"] for r in rows), 4),
        "cases": len(rows),
    }
    return {"suite": "rag", "dataset": dataset.name, "metrics": metrics, "detail": rows}


async def run_agent_suite(ectx: EvalContext, dataset: Path) -> dict:
    cases = load_jsonl(dataset)
    rows = []
    for case in cases:
        ctx = RunContext(user_id="eval")
        ctx.services["settings"] = ectx.settings
        sandbox = python_execute
        sandbox.requires_approval = False  # 评估环境自动批准
        agent = Agent(
            name="agent_eval",
            llm=ectx.llm,
            tools=ToolRegistry([sandbox]),
            system_prompt="你是严谨的助手，涉及计算必须用 python_execute 验证后再回答。",
            max_steps=5,
            stream_final=False,
        )
        answer, steps, t0 = "", 0, time.perf_counter()
        async for ev in agent.run(case["task"], ctx):
            if ev.type == "step_started":
                steps += 1
            if isinstance(ev, RunFinished):
                answer = str(ev.output.get("text", ""))
        latency_ms = int((time.perf_counter() - t0) * 1000)
        usage, cost = ctx.tracer.totals()

        judgement, _ = await judge_task(ectx.judge, task=case["task"], final_answer=answer)
        rows.append(
            {
                "task": case["task"],
                "answer": answer[:300],
                "success": judgement.success,
                "quality": judgement.quality,
                "steps": steps,
                "latency_ms": latency_ms,
                "tokens": usage.total_tokens,
                "cost": cost,
            }
        )
    metrics = {
        "success_rate": aggregate([1.0 if r["success"] else 0.0 for r in rows]),
        "avg_quality": aggregate([float(r["quality"]) for r in rows]),
        "avg_steps": aggregate([float(r["steps"]) for r in rows]),
        "avg_tokens": int(aggregate([float(r["tokens"]) for r in rows])),
        "total_cost": round(sum(r["cost"] for r in rows), 4),
        "cases": len(rows),
    }
    return {"suite": "agent", "dataset": dataset.name, "metrics": metrics, "detail": rows}


def render_report(result: dict, llm_desc: str) -> str:
    lines = [
        f"# 评估报告 · {result['suite']}",
        "",
        f"- 时间：{datetime.now(UTC).astimezone().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 数据集：{result['dataset']}（{result['metrics'].get('cases', 0)} 条）",
        f"- 模型：{llm_desc}",
        "",
        "## 汇总指标",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
    ]
    for key, value in result["metrics"].items():
        lines.append(f"| {key} | {value} |")
    lines += ["", "## 逐条明细", "", "```json", json.dumps(result["detail"], ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)


def threshold_failures(result: dict, specs: list[str]) -> list[str]:
    """解析 metric=value 阈值并返回失败原因，供 CI 建立可执行质量门。"""
    failures: list[str] = []
    for spec in specs:
        try:
            metric, raw = spec.rsplit("=", 1)
            minimum = float(raw)
        except ValueError as exc:
            raise ValueError(f"无效阈值 {spec!r}，应为 metric=value") from exc
        actual = result["metrics"].get(metric)
        if not isinstance(actual, int | float):
            failures.append(f"指标 {metric!r} 不存在或不是数值")
        elif float(actual) < minimum:
            failures.append(f"{metric}={actual} 低于阈值 {minimum}")
    return failures


async def persist_record(ectx: EvalContext, result: dict, used_judge: bool) -> None:
    async with ectx.sessions() as db:
        db.add(
            EvalRecord(
                suite=result["suite"],
                dataset=result["dataset"],
                metrics=result["metrics"],
                detail=result["detail"],
                enabled_judge=used_judge,
            )
        )
        await db.commit()


async def main() -> None:
    parser = argparse.ArgumentParser(description="AgentForge 评估运行器")
    parser.add_argument("suite", choices=["retrieval", "rag", "agent", "all"])
    parser.add_argument("--dataset", type=str, default="", help="自定义 JSONL 数据集路径")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output-dir", type=str, default=str(REPORTS_DIR))
    parser.add_argument(
        "--fail-under",
        action="append",
        default=[],
        metavar="METRIC=VALUE",
        help="质量门，可重复指定；任一指标低于阈值时以非零状态退出",
    )
    args = parser.parse_args()

    settings = get_settings()
    ectx = EvalContext(settings)
    await ectx.setup()
    llm_desc = f"{ectx.llm.provider}/{ectx.llm.model}（judge: {ectx.judge.provider}/{ectx.judge.model}）"
    if ectx.llm.provider == "mock":
        print("[提示] 当前为 Mock 模式：可验证评估管道，指标不代表真实模型效果。\n")

    suites = ["retrieval", "rag", "agent"] if args.suite == "all" else [args.suite]
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        for suite in suites:
            dataset = Path(args.dataset) if args.dataset else DATASETS_DIR / f"{suite}_zh.jsonl"
            print(f"=== 运行评估: {suite}（{dataset.name}）===")
            if suite == "retrieval":
                result = await run_retrieval_suite(ectx, dataset, args.top_k)
            elif suite == "rag":
                result = await run_rag_suite(ectx, dataset)
            else:
                result = await run_agent_suite(ectx, dataset)

            for key, value in result["metrics"].items():
                print(f"  {key:>18}: {value}")
            stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            report_path = output_dir / f"{stamp}_{suite}.md"
            report_path.write_text(render_report(result, llm_desc), encoding="utf-8")
            (output_dir / f"{stamp}_{suite}.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            await persist_record(ectx, result, used_judge=suite in ("rag", "agent"))
            print(f"  报告已写入: {report_path}\n")
            specs = args.fail_under or gate_specs(suite)  # 未显式指定时回退到默认质量门
            failures = threshold_failures(result, specs)
            if failures:
                raise SystemExit("评估质量门失败：" + "；".join(failures))
    finally:
        await ectx.close()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())

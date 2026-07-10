"""RunManager：Agent 运行的生命周期管理。

职责：
- 事件溯源：事件持久化（llm_delta 等瞬态事件只推送不落库）+ 实时发布到事件总线；
- Checkpoint：每步消息快照写入 Run 行，支持进程重启后恢复；
- human-in-the-loop：审批门实现为"暂停运行 + 等待用户决定"；
- 追踪落库：运行结束后批量写入 Span 树；
- 取消与超时保护。
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge.core.events import INTERNAL_EVENTS, dump_event
from agentforge.core.messages import ToolCall
from agentforge.core.runtime import RunContext
from agentforge.core.tools.base import Tool
from agentforge.db.models import ResearchReport, Run, RunEvent, Span
from agentforge.services.bus import CLOSE_SENTINEL, EventBus

logger = logging.getLogger(__name__)

# 不落库的瞬态事件（SSE 断线重放靠 assistant_message / report_draft 全量事件兜底）
TRANSIENT_EVENTS = {"llm_delta"}

APPROVAL_TIMEOUT = 600.0

RunFactory = Callable[[RunContext], AsyncIterator]


class RunLimitExceeded(RuntimeError):
    """达到单实例运行并发上限。"""


class RunManager:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        max_concurrent: int = 8,
        max_per_user: int = 2,
        max_per_session: int = 1,
    ):
        self.sessions = sessions
        self.bus = EventBus()
        self._tasks: dict[str, asyncio.Task] = {}
        self._approvals: dict[tuple[str, str], asyncio.Future] = {}
        self._owners: dict[str, tuple[str, str | None]] = {}
        self._start_lock = asyncio.Lock()
        self.max_concurrent = max_concurrent
        self.max_per_user = max_per_user
        self.max_per_session = max_per_session

    # ---------- 启动与驱动 ----------

    async def start(
        self,
        *,
        user_id: str,
        kind: str,
        input: dict,
        ctx: RunContext,
        factory: RunFactory,
        session_id: str | None = None,
    ) -> str:
        async with self._start_lock:
            self._prune_finished_tasks()
            self._check_capacity(user_id, session_id)
            async with self.sessions() as session:
                run = Run(user_id=user_id, kind=kind, status="running", input=input, session_id=session_id)
                session.add(run)
                await session.commit()
                run_id = run.id

            ctx.run_id = run_id
            ctx.user_id = user_id
            ctx.session_id = session_id
            ctx.state["kind"] = kind
            ctx.approval_gate = self._make_approval_gate(run_id)

            self._owners[run_id] = (user_id, session_id)
            task = asyncio.create_task(self._drive(run_id, factory, ctx), name=f"run-{run_id}")
            self._tasks[run_id] = task
            return run_id

    def _prune_finished_tasks(self) -> None:
        for run_id, task in list(self._tasks.items()):
            if task.done():
                self._tasks.pop(run_id, None)
                self._owners.pop(run_id, None)

    def _check_capacity(self, user_id: str, session_id: str | None) -> None:
        owners = list(self._owners.values())
        if self.max_concurrent > 0 and len(owners) >= self.max_concurrent:
            raise RunLimitExceeded("系统运行任务已达上限，请稍后重试")
        if self.max_per_user > 0 and sum(uid == user_id for uid, _ in owners) >= self.max_per_user:
            raise RunLimitExceeded("你的并发任务已达上限，请等待已有任务完成")
        if (
            session_id
            and self.max_per_session > 0
            and sum(sid == session_id for _, sid in owners) >= self.max_per_session
        ):
            raise RunLimitExceeded("该会话已有任务正在运行，请等待完成或先取消")

    async def _drive(self, run_id: str, factory: RunFactory, ctx: RunContext) -> None:
        seq = 0
        status, output, error = "failed", {}, ""
        prompt_tokens = completion_tokens = 0
        cost = 0.0
        try:
            async for ev in factory(ctx):
                if ev.type in INTERNAL_EVENTS:
                    if ev.type == "checkpoint":
                        await self._save_checkpoint(run_id, ev)
                    continue
                seq += 1
                payload = {"seq": seq, **dump_event(ev)}
                if ev.type not in TRANSIENT_EVENTS:
                    await self._persist_event(run_id, seq, ev.type, payload)
                self.bus.publish(run_id, payload)

                if ev.type == "run_finished":
                    status = "succeeded"
                    output = ev.output
                    prompt_tokens = ev.usage.prompt_tokens
                    completion_tokens = ev.usage.completion_tokens
                    cost = ev.cost
                elif ev.type == "run_failed":
                    status, error = "failed", ev.error
        except asyncio.CancelledError:
            status = "cancelled"
            seq += 1
            payload = {"seq": seq, "type": "run_cancelled", "ts": datetime.now(UTC).timestamp()}
            try:
                await self._persist_event(run_id, seq, "run_cancelled", payload)
                self.bus.publish(run_id, payload)
            except Exception:  # noqa: BLE001 数据库故障不能阻止本地任务清理
                logger.exception("Run %s 取消事件持久化失败", run_id)
        except Exception as e:  # noqa: BLE001 工厂函数异常兜底
            logger.exception("Run %s 执行异常", run_id)
            status, error = "failed", f"{type(e).__name__}: {e}"
            seq += 1
            payload = {"seq": seq, "type": "run_failed", "error": error, "ts": datetime.now(UTC).timestamp()}
            try:
                await self._persist_event(run_id, seq, "run_failed", payload)
                self.bus.publish(run_id, payload)
            except Exception:  # noqa: BLE001 数据库故障不能阻止本地任务清理
                logger.exception("Run %s 失败事件持久化失败", run_id)
        finally:
            usage_total, traced_cost = ctx.tracer.totals()
            try:
                self._record_metrics(ctx, status, prompt_tokens, completion_tokens, cost, usage_total, traced_cost)
                await self._finalize_run(
                    run_id,
                    status=status,
                    output=output,
                    error=error,
                    prompt_tokens=prompt_tokens or usage_total.prompt_tokens,
                    completion_tokens=completion_tokens or usage_total.completion_tokens,
                    cost=cost or traced_cost,
                    ctx=ctx,
                )
            except Exception:  # noqa: BLE001 即使数据库不可用也必须关闭 SSE 并释放并发名额
                logger.exception("Run %s 收尾落库失败", run_id)
            finally:
                self.bus.close(run_id)
                self._tasks.pop(run_id, None)
                self._owners.pop(run_id, None)

    async def _finalize_run(
        self,
        run_id: str,
        *,
        status: str,
        output: dict,
        error: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost: float,
        ctx: RunContext,
    ) -> None:
        async with self.sessions() as session:
            await session.execute(
                update(Run)
                .where(Run.id == run_id)
                .values(
                    status=status,
                    output=output,
                    error=error,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cost=cost,
                    finished_at=datetime.now(UTC),
                )
            )
            if ctx.state.get("kind") == "research" and status != "succeeded":
                await session.execute(
                    update(ResearchReport)
                    .where(ResearchReport.run_id == run_id)
                    .values(status=status, review={"error": error or f"研究任务已{status}"})
                )
            for span in ctx.tracer.spans:
                session.add(
                    Span(
                        id=span.id,
                        run_id=run_id,
                        parent_id=span.parent_id,
                        name=span.name,
                        kind=span.kind,
                        status=span.status,
                        input=_safe_json(span.input),
                        output=_safe_json(span.output),
                        error=span.error,
                        prompt_tokens=span.prompt_tokens,
                        completion_tokens=span.completion_tokens,
                        cost=span.cost,
                        started_at=datetime.fromtimestamp(span.started_at, UTC),
                        ended_at=datetime.fromtimestamp(span.ended_at, UTC) if span.ended_at else None,
                    )
                )
            await session.commit()

    async def recover_interrupted(self) -> int:
        """单实例启动时收敛上次崩溃遗留状态；不自动重放可能有副作用的工具。"""
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    select(Run).where(
                        Run.status.in_(("running", "awaiting_approval", "pending", "resuming"))
                    )
                )
            ).scalars().all()
            if not rows:
                return 0
            run_ids = [row.id for row in rows]
            reason = "服务重启导致任务中断，可从 checkpoint 手动恢复 chat 任务"
            await session.execute(
                update(Run)
                .where(Run.id.in_(run_ids))
                .values(status="interrupted", error=reason, finished_at=datetime.now(UTC))
            )
            await session.execute(
                update(ResearchReport)
                .where(ResearchReport.run_id.in_(run_ids), ResearchReport.status == "running")
                .values(status="interrupted", review={"error": reason})
            )
            await session.commit()
        logger.warning("已将 %s 个遗留运行标记为 interrupted", len(run_ids))
        return len(run_ids)

    def _record_metrics(self, ctx, status, ptok, ctok, cost, usage_total, traced_cost) -> None:
        from agentforge.observability import metrics
        from agentforge.observability.live import LIVE

        run_kind = ctx.state.get("kind", "chat")
        duration = 0.0
        # 从 agent span 估算时长（若有）
        for s in ctx.tracer.spans:
            if s.kind == "agent" and s.ended_at:
                duration = max(duration, s.duration_ms / 1000)
        final_prompt = ptok or usage_total.prompt_tokens
        final_completion = ctok or usage_total.completion_tokens
        final_cost = cost or traced_cost
        metrics.record_run(run_kind, status, final_prompt, final_completion, final_cost, duration)
        LIVE.record_run(run_kind, status, final_prompt + final_completion, final_cost, duration)
        for s in ctx.tracer.spans:
            if s.kind == "tool":
                metrics.record_tool(s.name.replace("tool:", ""), s.status == "ok")

    async def _persist_event(self, run_id: str, seq: int, type_: str, payload: dict) -> None:
        async with self.sessions() as session:
            session.add(RunEvent(run_id=run_id, seq=seq, type=type_, payload=payload))
            await session.commit()

    async def _save_checkpoint(self, run_id: str, checkpoint_ev) -> None:
        data = {"messages": [m.model_dump(mode="json") for m in checkpoint_ev.messages]}
        async with self.sessions() as session:
            await session.execute(update(Run).where(Run.id == run_id).values(checkpoint=data))
            await session.commit()

    # ---------- 订阅（SSE 数据源）----------

    async def subscribe(self, run_id: str, after_seq: int = 0) -> AsyncIterator[dict]:
        """先订阅、再重放历史、后消费实时，seq 去重，保证不丢不重。"""
        queue = self.bus.subscribe(run_id)
        last = after_seq
        try:
            async with self.sessions() as session:
                rows = (
                    await session.execute(
                        select(RunEvent)
                        .where(RunEvent.run_id == run_id, RunEvent.seq > after_seq)
                        .order_by(RunEvent.seq)
                    )
                ).scalars().all()
                run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one_or_none()
            for row in rows:
                last = max(last, row.seq)
                yield row.payload

            terminal_statuses = {"succeeded", "failed", "cancelled", "interrupted", "resumed"}
            if run is not None and run.status in terminal_statuses and run_id not in self._tasks:
                return

            while True:
                item = await queue.get()
                if item is CLOSE_SENTINEL or item.get("type") == "__close__":
                    break
                if item.get("seq", 0) <= last:
                    continue
                last = item["seq"]
                yield item
        finally:
            self.bus.unsubscribe(run_id, queue)

    # ---------- human-in-the-loop 审批 ----------

    def _make_approval_gate(self, run_id: str):
        async def gate(tool_call: ToolCall, tool: Tool) -> bool:
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            self._approvals[(run_id, tool_call.id)] = fut
            await self._set_status(run_id, "awaiting_approval")
            try:
                return await asyncio.wait_for(fut, timeout=APPROVAL_TIMEOUT)
            except TimeoutError:
                logger.warning("Run %s 审批超时，自动拒绝", run_id)
                return False
            finally:
                self._approvals.pop((run_id, tool_call.id), None)
                await self._set_status(run_id, "running")

        return gate

    def decide_approval(self, run_id: str, tool_call_id: str, approved: bool) -> bool:
        fut = self._approvals.get((run_id, tool_call_id))
        if fut is None or fut.done():
            return False
        fut.set_result(approved)
        return True

    def pending_approvals(self, run_id: str) -> list[str]:
        return [tc for rid, tc in self._approvals if rid == run_id]

    # ---------- 取消 / 状态 ----------

    def cancel(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    def is_active(self, run_id: str) -> bool:
        return run_id in self._tasks

    async def _set_status(self, run_id: str, status: str) -> None:
        async with self.sessions() as session:
            await session.execute(update(Run).where(Run.id == run_id).values(status=status))
            await session.commit()

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)


def _safe_json(data: dict) -> dict:
    """Span 输入输出裁剪，防止超大 payload 拖垮存储。"""
    out = {}
    for k, v in (data or {}).items():
        if isinstance(v, str) and len(v) > 2000:
            out[k] = v[:2000] + "…(截断)"
        else:
            out[k] = v
    return out

"""追踪路由：运行列表（tokens/成本统计）与单次运行的 Span 树。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentforge.api.deps import get_current_user, get_db
from agentforge.db.models import Run, RunEvent, Span, User

router = APIRouter()


@router.get("/runs")
async def list_runs(
    kind: str | None = Query(default=None, pattern="^(chat|research)$"),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    stmt = select(Run).where(Run.user_id == user.id).order_by(desc(Run.created_at)).limit(limit)
    if kind:
        stmt = stmt.where(Run.kind == kind)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id,
            "kind": r.kind,
            "status": r.status,
            "input_preview": str(r.input.get("message") or r.input.get("query") or "")[:80],
            "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens,
            "cost": r.cost,
            "created_at": r.created_at.isoformat(),
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "duration_ms": int((r.finished_at - r.created_at).total_seconds() * 1000)
            if r.finished_at
            else None,
        }
        for r in rows
    ]


def _input_preview(run: Run) -> str:
    return str((run.input or {}).get("message") or (run.input or {}).get("query") or "")[:80]


async def _run_aggregate(db: AsyncSession, user: User, run_id: str) -> dict:
    run = (
        await db.execute(select(Run).where(Run.id == run_id, Run.user_id == user.id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail=f"运行 {run_id} 不存在")
    spans = (
        (await db.execute(select(Span).where(Span.run_id == run_id))).scalars().all()
    )
    by_kind: dict[str, dict] = {}
    tools: dict[str, dict] = {}
    for span in spans:
        agg = by_kind.setdefault(span.kind, {"count": 0, "tokens": 0, "duration_ms": 0, "cost": 0.0})
        agg["count"] += 1
        agg["tokens"] += span.prompt_tokens + span.completion_tokens
        if span.ended_at:
            agg["duration_ms"] += int((span.ended_at - span.started_at).total_seconds() * 1000)
        agg["cost"] = round(agg["cost"] + span.cost, 6)
        if span.kind == "tool":
            name = span.name.replace("tool:", "")
            tool = tools.setdefault(name, {"count": 0, "errors": 0})
            tool["count"] += 1
            if span.status == "error":
                tool["errors"] += 1
    return {
        "id": run.id,
        "kind": run.kind,
        "status": run.status,
        "input_preview": _input_preview(run),
        "created_at": run.created_at.isoformat(),
        "totals": {
            "prompt_tokens": run.prompt_tokens,
            "completion_tokens": run.completion_tokens,
            "total_tokens": run.prompt_tokens + run.completion_tokens,
            "cost": run.cost,
            "duration_ms": int((run.finished_at - run.created_at).total_seconds() * 1000)
            if run.finished_at
            else None,
            "span_count": len(spans),
            "llm_calls": by_kind.get("llm", {}).get("count", 0),
            "tool_calls": by_kind.get("tool", {}).get("count", 0),
            "retrievals": by_kind.get("retrieval", {}).get("count", 0),
        },
        "by_kind": by_kind,
        "tools": [{"name": name, **stats} for name, stats in sorted(tools.items())],
    }


@router.get("/compare")
async def compare_runs(
    a: str = Query(..., description="运行 A 的 id"),
    b: str = Query(..., description="运行 B 的 id"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """并排对比两次运行：整体用量/耗时/成本 + 按 Span 类型聚合 + 工具调用分布。"""
    if a == b:
        raise HTTPException(status_code=400, detail="请选择两条不同的运行进行对比")
    return {"runs": [await _run_aggregate(db, user, a), await _run_aggregate(db, user, b)]}


@router.get("/runs/{run_id}")
async def run_trace(
    run_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    run = (
        await db.execute(select(Run).where(Run.id == run_id, Run.user_id == user.id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")

    spans = (
        (await db.execute(select(Span).where(Span.run_id == run_id).order_by(Span.started_at)))
        .scalars()
        .all()
    )
    event_count = len(
        (await db.execute(select(RunEvent.id).where(RunEvent.run_id == run_id))).all()
    )
    return {
        "run": {
            "id": run.id,
            "kind": run.kind,
            "status": run.status,
            "input": run.input,
            "output": run.output,
            "error": run.error,
            "prompt_tokens": run.prompt_tokens,
            "completion_tokens": run.completion_tokens,
            "cost": run.cost,
            "created_at": run.created_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        },
        "event_count": event_count,
        "spans": [
            {
                "id": s.id,
                "parent_id": s.parent_id,
                "name": s.name,
                "kind": s.kind,
                "status": s.status,
                "input": s.input,
                "output": s.output,
                "error": s.error,
                "prompt_tokens": s.prompt_tokens,
                "completion_tokens": s.completion_tokens,
                "cost": s.cost,
                "started_at": s.started_at.isoformat(),
                "ended_at": s.ended_at.isoformat() if s.ended_at else None,
                "duration_ms": int((s.ended_at - s.started_at).total_seconds() * 1000)
                if s.ended_at
                else None,
            }
            for s in spans
        ],
    }

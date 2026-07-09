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

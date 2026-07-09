"""Run 路由：状态查询、SSE 事件流、HITL 审批、取消、断点恢复。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentforge.api.app import Container
from agentforge.api.deps import get_container, get_current_user, get_db
from agentforge.api.sse import sse_response
from agentforge.core.runtime import RunContext
from agentforge.db.models import Run, User
from agentforge.services.chat import make_resume_factory

router = APIRouter()


async def _own_run(db: AsyncSession, user: User, run_id: str) -> Run:
    run = (
        await db.execute(select(Run).where(Run.id == run_id, Run.user_id == user.id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return run


@router.get("/{run_id}")
async def get_run(
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    container: Container = Depends(get_container),
) -> dict:
    run = await _own_run(db, user, run_id)
    return {
        "id": run.id,
        "kind": run.kind,
        "status": run.status,
        "input": run.input,
        "output": run.output,
        "error": run.error,
        "prompt_tokens": run.prompt_tokens,
        "completion_tokens": run.completion_tokens,
        "cost": run.cost,
        "pending_approvals": container.run_manager.pending_approvals(run_id),
        "created_at": run.created_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


@router.get("/{run_id}/events")
async def run_events(
    run_id: str,
    after: int = Query(default=0, ge=0, description="断线续传：只推送 seq 大于该值的事件"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    container: Container = Depends(get_container),
):
    await _own_run(db, user, run_id)
    return sse_response(container.run_manager.subscribe(run_id, after_seq=after))


class ApprovalDecision(BaseModel):
    tool_call_id: str
    approved: bool


@router.post("/{run_id}/approval")
async def decide_approval(
    run_id: str,
    body: ApprovalDecision,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    container: Container = Depends(get_container),
) -> dict:
    await _own_run(db, user, run_id)
    ok = container.run_manager.decide_approval(run_id, body.tool_call_id, body.approved)
    if not ok:
        raise HTTPException(status_code=409, detail="该审批不存在或已处理（可能已超时）")
    return {"ok": True, "approved": body.approved}


@router.post("/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    container: Container = Depends(get_container),
) -> dict:
    await _own_run(db, user, run_id)
    ok = container.run_manager.cancel(run_id)
    if not ok:
        raise HTTPException(status_code=409, detail="运行已结束，无法取消")
    return {"ok": True}


@router.post("/{run_id}/resume", status_code=202)
async def resume_run(
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    container: Container = Depends(get_container),
) -> dict:
    """恢复因进程重启而中断的 chat run（基于 checkpoint 消息快照）。"""
    run = await _own_run(db, user, run_id)
    if container.run_manager.is_active(run_id):
        raise HTTPException(status_code=409, detail="运行仍在进行中，无需恢复")
    if run.kind != "chat":
        raise HTTPException(status_code=400, detail="仅支持恢复 chat 类型的运行")
    if run.status not in ("running", "awaiting_approval") or not (run.checkpoint or {}).get("messages"):
        raise HTTPException(status_code=409, detail="该运行不满足恢复条件（无 checkpoint 或已结束）")

    new_run_id = await container.run_manager.start(
        user_id=user.id,
        kind="chat",
        input={**run.input, "resumed_from": run_id},
        session_id=run.session_id,
        ctx=RunContext(),
        factory=make_resume_factory(container, run),
    )
    return {"run_id": new_run_id, "resumed_from": run_id}

"""回答反馈：赞/踩 + 评论；可导出为评估数据集，打通"生产 -> 评估"闭环。"""

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentforge.api.deps import get_current_user, get_db
from agentforge.db.models import Feedback, Run, User

router = APIRouter()


class FeedbackIn(BaseModel):
    run_id: str
    rating: str = Field(pattern="^(up|down)$")
    comment: str = Field(default="", max_length=1000)


@router.post("", status_code=201)
async def submit_feedback(
    body: FeedbackIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    run = (
        await db.execute(select(Run).where(Run.id == body.run_id, Run.user_id == user.id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")

    # 同一 run 只保留一条反馈：已存在则更新评分
    existing = (
        await db.execute(
            select(Feedback).where(Feedback.run_id == body.run_id, Feedback.user_id == user.id)
        )
    ).scalar_one_or_none()
    question = str(run.input.get("message") or run.input.get("query") or "")
    answer = str(run.output.get("text") or run.output.get("report") or "")
    sources = run.output.get("sources") or []

    if existing:
        existing.rating = body.rating
        existing.comment = body.comment
        await db.commit()
        return {"id": existing.id, "updated": True}

    fb = Feedback(
        user_id=user.id,
        run_id=body.run_id,
        session_id=run.session_id or "",
        rating=body.rating,
        question=question,
        answer=answer,
        sources=sources,
        comment=body.comment,
    )
    db.add(fb)
    await db.commit()
    return {"id": fb.id, "updated": False}


@router.get("")
async def list_feedback(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    rows = (
        (
            await db.execute(
                select(Feedback).where(Feedback.user_id == user.id).order_by(desc(Feedback.created_at))
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "run_id": r.run_id,
            "rating": r.rating,
            "question": r.question[:200],
            "comment": r.comment,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.get("/summary")
async def feedback_summary(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    rows = (
        (await db.execute(select(Feedback.rating).where(Feedback.user_id == user.id))).scalars().all()
    )
    up = sum(1 for r in rows if r == "up")
    down = sum(1 for r in rows if r == "down")
    total = up + down
    return {"up": up, "down": down, "total": total, "satisfaction": round(up / total, 3) if total else 0.0}


@router.get("/export", response_class=PlainTextResponse)
async def export_feedback(
    rating: str = Query(default="", pattern="^(up|down|)$"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> str:
    """导出为 JSONL 评估数据集（可直接喂给 evals 框架）。rating 可筛选只导出赞或踩。"""
    stmt = select(Feedback).where(Feedback.user_id == user.id).order_by(Feedback.created_at)
    if rating:
        stmt = stmt.where(Feedback.rating == rating)
    rows = (await db.execute(stmt)).scalars().all()
    lines = [
        json.dumps(
            {
                "question": r.question,
                "answer": r.answer,
                "rating": r.rating,
                "comment": r.comment,
                "reference": r.answer if r.rating == "up" else "",
            },
            ensure_ascii=False,
        )
        for r in rows
    ]
    return "\n".join(lines)

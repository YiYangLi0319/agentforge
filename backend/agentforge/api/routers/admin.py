"""管理后台：仅管理员可访问。用户管理、配额调整、全局用量/成本总览。"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentforge.api.app import Container
from agentforge.api.deps import get_container, get_current_user, get_db
from agentforge.db.models import Feedback, KnowledgeBase, Run, User
from agentforge.services.quota import effective_quota, today_token_usage

router = APIRouter()


async def get_admin_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


@router.get("/users")
async def list_users(
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
    container: Container = Depends(get_container),
) -> list[dict]:
    users = (await db.execute(select(User).order_by(User.created_at))).scalars().all()
    result = []
    for u in users:
        used_today = await today_token_usage(db, u.id)
        total_tokens = (
            await db.execute(
                select(func.coalesce(func.sum(Run.prompt_tokens + Run.completion_tokens), 0)).where(
                    Run.user_id == u.id
                )
            )
        ).scalar() or 0
        total_cost = (
            await db.execute(select(func.coalesce(func.sum(Run.cost), 0.0)).where(Run.user_id == u.id))
        ).scalar() or 0.0
        run_count = (
            await db.execute(select(func.count(Run.id)).where(Run.user_id == u.id))
        ).scalar() or 0
        result.append(
            {
                "id": u.id,
                "username": u.username,
                "is_admin": u.is_admin,
                "quota": effective_quota(u, container.settings),
                "used_today": used_today,
                "total_tokens": int(total_tokens),
                "total_cost": round(float(total_cost), 4),
                "run_count": int(run_count),
                "created_at": u.created_at.isoformat(),
            }
        )
    return result


class QuotaUpdate(BaseModel):
    daily_token_quota: int = Field(ge=0)


@router.patch("/users/{user_id}/quota")
async def set_quota(
    user_id: str,
    body: QuotaUpdate,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    target.daily_token_quota = body.daily_token_quota
    await db.commit()
    return {"id": target.id, "daily_token_quota": target.daily_token_quota}


@router.get("/stats")
async def global_stats(
    admin: User = Depends(get_admin_user), db: AsyncSession = Depends(get_db)
) -> dict:
    since = datetime.now(UTC) - timedelta(days=14)
    total_users = (await db.execute(select(func.count(User.id)))).scalar() or 0
    total_runs = (await db.execute(select(func.count(Run.id)))).scalar() or 0
    total_tokens = (
        await db.execute(select(func.coalesce(func.sum(Run.prompt_tokens + Run.completion_tokens), 0)))
    ).scalar() or 0
    total_cost = (
        await db.execute(select(func.coalesce(func.sum(Run.cost), 0.0)))
    ).scalar() or 0.0
    total_kbs = (await db.execute(select(func.count(KnowledgeBase.id)))).scalar() or 0

    fb_rows = (await db.execute(select(Feedback.rating))).scalars().all()
    up = sum(1 for r in fb_rows if r == "up")
    down = sum(1 for r in fb_rows if r == "down")

    recent_runs = (
        await db.execute(
            select(Run.prompt_tokens, Run.completion_tokens, Run.cost, Run.created_at).where(
                Run.created_at >= since
            )
        )
    ).all()
    by_day: dict[str, dict] = {}
    for pt, ct, cost, created in recent_runs:
        day = created.strftime("%m-%d")
        d = by_day.setdefault(day, {"day": day, "runs": 0, "tokens": 0, "cost": 0.0})
        d["runs"] += 1
        d["tokens"] += (pt or 0) + (ct or 0)
        d["cost"] = round(d["cost"] + (cost or 0), 4)

    return {
        "totals": {
            "users": int(total_users),
            "runs": int(total_runs),
            "tokens": int(total_tokens),
            "cost": round(float(total_cost), 4),
            "knowledge_bases": int(total_kbs),
        },
        "feedback": {"up": up, "down": down, "satisfaction": round(up / (up + down), 3) if (up + down) else 0.0},
        "trend": [by_day[k] for k in sorted(by_day)],
    }

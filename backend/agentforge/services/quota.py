"""每用户每日 token 配额：从当日运行记录累计用量，超额拦截（管理员不受限）。"""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentforge.config import Settings
from agentforge.db.models import Run, User


def effective_quota(user: User, settings: Settings) -> int:
    """返回该用户的每日额度：管理员或额度=0 视为不限；否则用户自定义优先，其次全局默认。"""
    if user.is_admin:
        return 0
    return user.daily_token_quota or settings.daily_token_quota


async def today_token_usage(db: AsyncSession, user_id: str) -> int:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    total = (
        await db.execute(
            select(func.coalesce(func.sum(Run.prompt_tokens + Run.completion_tokens), 0)).where(
                Run.user_id == user_id, Run.created_at >= start
            )
        )
    ).scalar()
    return int(total or 0)


async def quota_status(db: AsyncSession, user: User, settings: Settings) -> dict:
    limit = effective_quota(user, settings)
    used = await today_token_usage(db, user.id)
    return {
        "used": used,
        "limit": limit,
        "remaining": max(limit - used, 0) if limit > 0 else None,
        "unlimited": limit == 0,
        "is_admin": user.is_admin,
    }


async def assert_within_quota(db: AsyncSession, user: User, settings: Settings) -> None:
    limit = effective_quota(user, settings)
    if limit <= 0:
        return
    used = await today_token_usage(db, user.id)
    if used >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"今日 token 额度已用尽（{used}/{limit}），请明天再试或联系管理员提额",
        )

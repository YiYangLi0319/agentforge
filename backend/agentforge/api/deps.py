"""路由依赖：容器获取、数据库会话、双通道认证（JWT / API Key）、限流。"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentforge.api.app import Container
from agentforge.db.models import ApiKey, User
from agentforge.services.security import decode_access_token, hash_api_key


def get_container(request: Request) -> Container:
    return request.app.state.container


async def get_db(container: Container = Depends(get_container)) -> AsyncIterator[AsyncSession]:
    async with container.sessions() as session:
        yield session


async def get_current_user(
    container: Container = Depends(get_container),
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> User:
    # 通道一：JWT Bearer
    if authorization and authorization.startswith("Bearer "):
        user_id = decode_access_token(authorization[7:], container.settings.secret_key)
        if user_id:
            user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
            if user:
                return user
    # 通道二：API Key（编程访问）
    if x_api_key:
        key_row = (
            await db.execute(select(ApiKey).where(ApiKey.key_hash == hash_api_key(x_api_key)))
        ).scalar_one_or_none()
        if key_row:
            key_row.last_used_at = datetime.now(UTC)
            await db.commit()
            user = (
                await db.execute(select(User).where(User.id == key_row.user_id))
            ).scalar_one_or_none()
            if user:
                return user
    raise HTTPException(status_code=401, detail="未认证：请提供有效的 Bearer Token 或 X-API-Key")


def rate_limited(scope: str, limit_field: str):
    """限流依赖工厂：按 用户+场景 维度计数。"""

    async def dependency(
        container: Container = Depends(get_container),
        user: User = Depends(get_current_user),
    ) -> User:
        limit = getattr(container.settings, limit_field)
        allowed, retry_after = await container.limiter.hit(f"{scope}:{user.id}", limit)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"请求过于频繁，请 {retry_after} 秒后再试",
                headers={"Retry-After": str(retry_after)},
            )
        return user

    return dependency

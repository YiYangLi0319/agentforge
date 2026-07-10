"""认证路由：注册 / 登录 / 当前用户 / API Key 管理。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentforge.api.app import Container
from agentforge.api.deps import get_container, get_current_user, get_db
from agentforge.db.models import ApiKey, User
from agentforge.services.security import (
    create_access_token,
    generate_api_key,
    hash_password,
    verify_password,
)

router = APIRouter()


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[\w.-]+$")
    password: str = Field(min_length=8, max_length=128)


class RegisterCredentials(Credentials):
    invite_code: str = ""


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    username: str


@router.post("/register", response_model=TokenOut, status_code=201)
async def register(
    body: RegisterCredentials,
    db: AsyncSession = Depends(get_db),
    container: Container = Depends(get_container),
):
    required_code = container.settings.registration_invite_code
    if required_code and body.invite_code.strip() != required_code:
        raise HTTPException(status_code=403, detail="邀请码无效，请联系管理员获取")
    exists = (
        await db.execute(select(User).where(User.username == body.username))
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="用户名已被占用")
    is_admin = bool(container.settings.admin_username) and body.username == container.settings.admin_username
    user = User(username=body.username, password_hash=hash_password(body.password), is_admin=is_admin)
    db.add(user)
    await db.commit()
    token = create_access_token(user.id, container.settings.secret_key, container.settings.jwt_expire_hours)
    return TokenOut(access_token=token, user_id=user.id, username=user.username)


@router.post("/login", response_model=TokenOut)
async def login(
    body: Credentials,
    db: AsyncSession = Depends(get_db),
    container: Container = Depends(get_container),
):
    user = (
        await db.execute(select(User).where(User.username == body.username))
    ).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_access_token(user.id, container.settings.secret_key, container.settings.jwt_expire_hours)
    return TokenOut(access_token=token, user_id=user.id, username=user.username)


@router.get("/me")
async def me(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    container: Container = Depends(get_container),
) -> dict:
    from agentforge.services.quota import quota_status

    # 管理员用户名在登录后可自动提权（兼容注册早于配置的情况）
    if container.settings.admin_username and user.username == container.settings.admin_username and not user.is_admin:
        user.is_admin = True
        await db.commit()
    return {
        "user_id": user.id,
        "username": user.username,
        "is_admin": user.is_admin,
        "created_at": user.created_at.isoformat(),
        "quota": await quota_status(db, user, container.settings),
    }


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)


@router.post("/api-keys", status_code=201)
async def create_api_key(
    body: ApiKeyCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    plain, key_hash, prefix = generate_api_key()
    row = ApiKey(user_id=user.id, name=body.name, key_hash=key_hash, prefix=prefix)
    db.add(row)
    await db.commit()
    return {
        "id": row.id,
        "name": row.name,
        "prefix": prefix,
        "api_key": plain,
        "notice": "该 Key 只显示一次，请妥善保存",
    }


@router.get("/api-keys")
async def list_api_keys(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    rows = (
        (await db.execute(select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at)))
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "name": r.name,
            "prefix": r.prefix,
            "created_at": r.created_at.isoformat(),
            "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
        }
        for r in rows
    ]


@router.delete("/api-keys/{key_id}", status_code=204)
async def delete_api_key(
    key_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    row = (
        await db.execute(select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user.id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    await db.delete(row)
    await db.commit()

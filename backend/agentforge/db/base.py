"""数据库引擎与会话管理：PostgreSQL(生产) / SQLite(轻量与测试) 双支持。"""

import json
import logging
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_REVISION = "20260710_01"


class Base(DeclarativeBase):
    pass


def normalize_db_url(url: str) -> str:
    """规范化连接串：把 PaaS 常见的 postgres:// / postgresql:// 统一为 asyncpg 驱动。

    Render/Heroku 等给出的 DATABASE_URL 形如 postgres://user:pass@host/db，
    而本项目用 asyncpg 异步驱动，需要 postgresql+asyncpg://。
    """
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


def build_engine(database_url: str) -> AsyncEngine:
    database_url = normalize_db_url(database_url)
    kwargs: dict = {
        "echo": False,
        "json_serializer": lambda o: json.dumps(o, ensure_ascii=False),
    }
    if database_url.startswith("sqlite"):
        # timeout: 多协程并发写时等待锁而不是立刻抛 database is locked
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if ":memory:" in database_url or database_url.endswith("sqlite+aiosqlite://"):
            kwargs["poolclass"] = StaticPool
    else:
        kwargs["pool_pre_ping"] = True
        kwargs["pool_size"] = 10
    return create_async_engine(database_url, **kwargs)


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def _configure_vector_storage(engine: AsyncEngine, use_pgvector: bool) -> None:
    """按数据库实际列类型锁定向量后端，避免运行时在 JSON/vector 间错误翻转。"""
    from agentforge.db.types import PGVECTOR

    if engine.dialect.name != "postgresql":
        PGVECTOR["enabled"] = False
        return

    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT udt_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'chunks'
                      AND column_name = 'embedding'
                    """
                )
            )
        ).first()
        if row is not None:
            PGVECTOR["enabled"] = row[0] == "vector"
            if use_pgvector and not PGVECTOR["enabled"]:
                logger.warning("现有 embedding 列为 JSON；本实例保持 JSON 检索，不会在启动时变更物理列类型")
            return

        enabled = False
        if use_pgvector:
            savepoint = await conn.begin_nested()
            try:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                await savepoint.commit()
                enabled = True
            except Exception as exc:  # noqa: BLE001 托管 PostgreSQL 可能没有扩展权限
                await savepoint.rollback()
                logger.warning("pgvector 不可用，向量检索降级为 JSON+进程内计算：%s", exc)
        PGVECTOR["enabled"] = enabled


async def init_db(
    engine: AsyncEngine,
    use_pgvector: bool = True,
    *,
    create_schema: bool = True,
) -> None:
    """初始化数据库（幂等）。

    PostgreSQL 上尝试启用 pgvector 扩展：成功则向量列用原生 vector；失败或被禁用
    则自动降级为 JSON 存储（兼容不带 pgvector 的托管 Postgres，如 Railway/Zeabur 默认库）。
    """
    from agentforge.db import models  # noqa: F401  确保模型已注册

    await _configure_vector_storage(engine, use_pgvector)
    if create_schema:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)


async def current_schema_revision(engine: AsyncEngine) -> str | None:
    """返回 Alembic 当前版本；未使用迁移管理的开发库返回 None。"""
    try:
        async with engine.connect() as conn:
            value = await conn.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
        return str(value) if value else None
    except Exception:  # noqa: BLE001 表不存在或数据库尚未就绪
        return None


async def session_scope(
    sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with sessions() as session:
        yield session

"""数据库引擎与会话管理：PostgreSQL(生产) / SQLite(轻量与测试) 双支持。"""

import json
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


async def init_db(engine: AsyncEngine, use_pgvector: bool = True) -> None:
    """初始化数据库（幂等）。

    PostgreSQL 上尝试启用 pgvector 扩展：成功则向量列用原生 vector；失败或被禁用
    则自动降级为 JSON 存储（兼容不带 pgvector 的托管 Postgres，如 Railway/Zeabur 默认库）。
    """
    import logging

    from agentforge.db import models  # noqa: F401  确保模型已注册
    from agentforge.db.types import PGVECTOR

    logger = logging.getLogger(__name__)

    if engine.dialect.name == "postgresql":
        enabled = False
        if use_pgvector:
            try:
                async with engine.begin() as conn:
                    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                enabled = True
            except Exception as e:  # noqa: BLE001 无 pgvector 权限/扩展则降级
                logger.warning("pgvector 不可用，向量检索降级为 JSON+进程内计算：%s", e)
        PGVECTOR["enabled"] = enabled
    else:
        PGVECTOR["enabled"] = False  # SQLite 等：JSON 存储

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def session_scope(
    sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with sessions() as session:
        yield session

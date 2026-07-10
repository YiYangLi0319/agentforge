"""启动引导：遗留 create_all 库 stamp + 空库 upgrade。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from agentforge.db import models  # noqa: F401
from agentforge.db.base import CURRENT_SCHEMA_REVISION, Base
from agentforge.db.bootstrap import prepare_schema

INI = Path(__file__).resolve().parents[1] / "alembic.ini"


async def _revision(url: str) -> str | None:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return await conn.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
    finally:
        await engine.dispose()


@pytest.mark.skipif(not INI.is_file(), reason="alembic.ini missing")
def test_prepare_schema_stamps_legacy_create_all(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'legacy.db').as_posix()}"

    async def seed() -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()

    asyncio.run(seed())
    prepare_schema(url, alembic_ini=INI)
    assert asyncio.run(_revision(url)) == CURRENT_SCHEMA_REVISION
    prepare_schema(url, alembic_ini=INI)  # 幂等


@pytest.mark.skipif(not INI.is_file(), reason="alembic.ini missing")
def test_prepare_schema_upgrades_empty_db(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'fresh.db').as_posix()}"
    prepare_schema(url, alembic_ini=INI)
    assert asyncio.run(_revision(url)) == CURRENT_SCHEMA_REVISION

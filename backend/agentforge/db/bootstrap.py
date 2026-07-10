"""生产启动引导：兼容早期 create_all 遗留库，再执行 Alembic 升级。

Render 等 PaaS 上，旧镜像曾用 SQLAlchemy create_all 建表，没有 alembic_version。
若直接 `alembic upgrade head`，初始迁移会再次 CREATE TABLE 并以非零退出码杀掉容器。
本模块在升级前检测该情况并 stamp 到合适版本，再 upgrade 到 head。
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from agentforge.db.base import CURRENT_SCHEMA_REVISION, normalize_db_url

logger = logging.getLogger(__name__)

BASE_REVISION = "78f884cf061b"


def decide_stamp_revision(table_names: set[str], columns: dict[str, set[str]]) -> str | None:
    """若需 stamp 则返回目标 revision；已由 Alembic 管理或空库则返回 None。"""
    if "alembic_version" in table_names:
        return None
    if "users" not in table_names:
        return None

    report_cols = columns.get("research_reports", set())
    session_cols = columns.get("chat_sessions", set())
    if "share_token" in report_cols and "summary_through_at" in session_cols:
        return CURRENT_SCHEMA_REVISION
    return BASE_REVISION


async def _probe(database_url: str) -> tuple[set[str], dict[str, set[str]]]:
    engine = create_async_engine(normalize_db_url(database_url))
    try:
        async with engine.connect() as conn:

            def _inspect(sync_conn):
                insp = sa_inspect(sync_conn)
                names = set(insp.get_table_names())
                cols: dict[str, set[str]] = {}
                for table in ("research_reports", "chat_sessions"):
                    if table in names:
                        cols[table] = {c["name"] for c in insp.get_columns(table)}
                return names, cols

            return await conn.run_sync(_inspect)
    finally:
        await engine.dispose()


async def _current_revision(database_url: str) -> str | None:
    engine = create_async_engine(normalize_db_url(database_url))
    try:
        async with engine.connect() as conn:
            try:
                value = await conn.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
            except Exception:  # noqa: BLE001 表可能尚不存在
                return None
            return str(value) if value else None
    finally:
        await engine.dispose()


def prepare_schema(database_url: str, *, alembic_ini: Path | None = None) -> None:
    """探测遗留库 → 必要时 stamp → alembic upgrade head。"""
    from alembic.config import Config

    from alembic import command

    ini = alembic_ini or Path("alembic.ini")
    if not ini.is_file():
        raise FileNotFoundError(f"找不到 Alembic 配置: {ini.resolve()}")

    table_names, columns = asyncio.run(_probe(database_url))
    stamp_to = decide_stamp_revision(table_names, columns)

    cfg = Config(str(ini))
    cfg.set_main_option("sqlalchemy.url", normalize_db_url(database_url))

    if stamp_to:
        logger.warning(
            "检测到无 alembic_version 的遗留库（tables=%d），stamp → %s 后再 upgrade",
            len(table_names),
            stamp_to,
        )
        command.stamp(cfg, stamp_to)

    logger.info("执行 alembic upgrade head")
    command.upgrade(cfg, "head")

    rev = asyncio.run(_current_revision(database_url))
    logger.info("当前 schema revision=%s (expected=%s)", rev, CURRENT_SCHEMA_REVISION)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s | %(message)s")
    from agentforge.config import get_settings

    _ = argv
    try:
        prepare_schema(get_settings().database_url)
    except Exception:  # noqa: BLE001 启动失败必须非零退出，便于 PaaS 展示日志
        logger.exception("数据库迁移失败")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

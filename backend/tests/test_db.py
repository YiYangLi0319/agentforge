"""数据库连接串规范化与 pgvector 开关测试。"""

from agentforge.db.base import CURRENT_SCHEMA_REVISION, normalize_db_url
from agentforge.db.bootstrap import BASE_REVISION, decide_stamp_revision
from agentforge.db.types import PGVECTOR, pgvector_enabled


def test_normalize_db_url():
    # PaaS 常见格式 -> asyncpg 驱动
    assert normalize_db_url("postgres://u:p@h:5432/db") == "postgresql+asyncpg://u:p@h:5432/db"
    assert normalize_db_url("postgresql://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    # 已是 asyncpg 或 sqlite：保持不变
    assert normalize_db_url("postgresql+asyncpg://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert normalize_db_url("sqlite+aiosqlite:///./x.db") == "sqlite+aiosqlite:///./x.db"
    # libpq sslmode → asyncpg ssl
    assert (
        normalize_db_url("postgres://u:p@h/db?sslmode=require")
        == "postgresql+asyncpg://u:p@h/db?ssl=true"
    )
    assert (
        normalize_db_url("postgresql+asyncpg://u:p@h/db?sslmode=disable")
        == "postgresql+asyncpg://u:p@h/db?ssl=false"
    )


def test_decide_stamp_revision_for_legacy_create_all():
    assert decide_stamp_revision(set(), {}) is None
    assert decide_stamp_revision({"alembic_version", "users"}, {}) is None
    assert decide_stamp_revision({"users"}, {}) == BASE_REVISION
    assert (
        decide_stamp_revision(
            {"users", "research_reports", "chat_sessions"},
            {
                "research_reports": {"share_token"},
                "chat_sessions": {"summary_through_at"},
            },
        )
        == CURRENT_SCHEMA_REVISION
    )


def test_pgvector_toggle():
    original = PGVECTOR["enabled"]
    try:
        PGVECTOR["enabled"] = False
        assert pgvector_enabled() is False
        PGVECTOR["enabled"] = True
        assert pgvector_enabled() is True
    finally:
        PGVECTOR["enabled"] = original

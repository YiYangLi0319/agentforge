"""数据库连接串规范化与 pgvector 开关测试。"""

from agentforge.db.base import normalize_db_url
from agentforge.db.types import PGVECTOR, pgvector_enabled


def test_normalize_db_url():
    # PaaS 常见格式 -> asyncpg 驱动
    assert normalize_db_url("postgres://u:p@h:5432/db") == "postgresql+asyncpg://u:p@h:5432/db"
    assert normalize_db_url("postgresql://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    # 已是 asyncpg 或 sqlite：保持不变
    assert normalize_db_url("postgresql+asyncpg://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert normalize_db_url("sqlite+aiosqlite:///./x.db") == "sqlite+aiosqlite:///./x.db"


def test_pgvector_toggle():
    original = PGVECTOR["enabled"]
    try:
        PGVECTOR["enabled"] = False
        assert pgvector_enabled() is False
        PGVECTOR["enabled"] = True
        assert pgvector_enabled() is True
    finally:
        PGVECTOR["enabled"] = original

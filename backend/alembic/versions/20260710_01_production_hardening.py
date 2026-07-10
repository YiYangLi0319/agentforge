"""production hardening

Revision ID: 20260710_01
Revises: 78f884cf061b
Create Date: 2026-07-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260710_01"
down_revision: str | None = "78f884cf061b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if "share_token" not in _columns("research_reports"):
        op.add_column("research_reports", sa.Column("share_token", sa.String(length=32), nullable=True))
    if "summary_through_at" not in _columns("chat_sessions"):
        op.add_column(
            "chat_sessions",
            sa.Column("summary_through_at", sa.DateTime(timezone=True), nullable=True),
        )

    indexes = _indexes("research_reports")
    if "ix_research_reports_share_token" not in indexes:
        op.create_index(
            "ix_research_reports_share_token",
            "research_reports",
            ["share_token"],
            unique=True,
        )
    if "ix_research_reports_user_created" not in indexes:
        op.create_index(
            "ix_research_reports_user_created",
            "research_reports",
            ["user_id", "created_at"],
        )
    if "ix_research_reports_status" not in indexes:
        op.create_index("ix_research_reports_status", "research_reports", ["status"])

    additions = (
        ("chat_sessions", "ix_chat_sessions_user_updated", ["user_id", "updated_at"]),
        ("chat_messages", "ix_chat_messages_session_created", ["session_id", "created_at"]),
        ("runs", "ix_runs_user_created", ["user_id", "created_at"]),
        ("chunks", "ix_chunks_document_seq", ["document_id", "seq"]),
    )
    for table, name, columns in additions:
        if name not in _indexes(table):
            op.create_index(name, table, columns)

    # 旧初始迁移曾为 scope_key 创建两个等价索引，仅保留 SQLAlchemy 命名的一个。
    if "ix_cache_scope" in _indexes("semantic_cache_entries"):
        op.drop_index("ix_cache_scope", table_name="semantic_cache_entries")


def downgrade() -> None:
    # 还原 upgrade 丢弃的旧索引，使初始迁移的 downgrade 能正常回滚。
    if "ix_cache_scope" not in _indexes("semantic_cache_entries"):
        op.create_index("ix_cache_scope", "semantic_cache_entries", ["scope_key"])
    for table, name in (
        ("chunks", "ix_chunks_document_seq"),
        ("runs", "ix_runs_user_created"),
        ("chat_messages", "ix_chat_messages_session_created"),
        ("chat_sessions", "ix_chat_sessions_user_updated"),
        ("research_reports", "ix_research_reports_status"),
        ("research_reports", "ix_research_reports_user_created"),
        ("research_reports", "ix_research_reports_share_token"),
    ):
        if name in _indexes(table):
            op.drop_index(name, table_name=table)
    if "summary_through_at" in _columns("chat_sessions"):
        op.drop_column("chat_sessions", "summary_through_at")
    if "share_token" in _columns("research_reports"):
        op.drop_column("research_reports", "share_token")

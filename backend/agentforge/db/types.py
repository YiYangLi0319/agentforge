"""跨方言向量列类型：PostgreSQL(带 pgvector) 用原生 vector，否则降级为 JSON 存储。

通过模块级开关 PGVECTOR 决定：启动时若检测到 pgvector 扩展则用原生向量列（支持
`<=>` 距离检索），否则（如 Railway/Zeabur 默认 Postgres 无 pgvector）降级为 JSON，
检索改为进程内余弦——保证任意 PostgreSQL / SQLite 都能运行。
"""

from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON
from sqlalchemy.types import TypeDecorator

# 由 init_db 在启动时按实际探测结果设置；默认启用（真实 pg 且有扩展时）
PGVECTOR = {"enabled": True}


def pgvector_enabled() -> bool:
    return PGVECTOR["enabled"]


class EmbeddingVector(TypeDecorator):
    """向量列。pgvector 可用时用原生 vector 列；否则存 JSON，检索在进程内计算。"""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql" and PGVECTOR["enabled"]:
            return dialect.type_descriptor(Vector())
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        return [float(x) for x in value]

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        return [float(x) for x in value]

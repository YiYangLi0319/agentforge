"""数据集服务：CSV 解析 + 内存 SQLite 只读查询（Text2SQL 的执行后端）。

用 stdlib csv + sqlite3，无 pandas 依赖，镜像轻量、执行安全（只允许 SELECT）。
"""

import csv
import io
import re
import sqlite3
from typing import Any


def _sanitize_ident(name: str, fallback: str) -> str:
    cleaned = re.sub(r"\W+", "_", name.strip()).strip("_").lower()
    if not cleaned or not re.match(r"^[a-z_]", cleaned):
        cleaned = fallback
    return cleaned[:40]


def _infer_type(values: list[str]) -> str:
    non_empty = [v for v in values if v not in ("", None)]
    if not non_empty:
        return "TEXT"

    def is_int(v: str) -> bool:
        try:
            int(v)
            return True
        except ValueError:
            return False

    def is_float(v: str) -> bool:
        try:
            float(v)
            return True
        except ValueError:
            return False

    if all(is_int(v) for v in non_empty):
        return "INTEGER"
    if all(is_float(v) for v in non_empty):
        return "REAL"
    return "TEXT"


def parse_csv(data: bytes, max_rows: int) -> dict[str, Any]:
    """解析 CSV -> {columns:[{name,type}], rows:[{col:val}], table_name}。"""
    text = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("无法解码 CSV 文件")

    reader = csv.reader(io.StringIO(text))
    all_rows = list(reader)
    if len(all_rows) < 2:
        raise ValueError("CSV 至少需要表头 + 一行数据")

    header = all_rows[0]
    col_names = []
    seen: dict[str, int] = {}
    for i, h in enumerate(header):
        name = _sanitize_ident(h, f"col_{i}")
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        col_names.append(name)

    data_rows = all_rows[1 : max_rows + 1]
    columns_values: list[list[str]] = [[] for _ in col_names]
    parsed_rows: list[dict] = []
    for raw in data_rows:
        row: dict = {}
        for i, col in enumerate(col_names):
            val = raw[i] if i < len(raw) else ""
            row[col] = val
            columns_values[i].append(val)
        parsed_rows.append(row)

    columns = [{"name": col_names[i], "type": _infer_type(columns_values[i])} for i in range(len(col_names))]
    # 按推断类型转换行值
    for row in parsed_rows:
        for c in columns:
            v = row[c["name"]]
            if v == "":
                row[c["name"]] = None
            elif c["type"] == "INTEGER":
                row[c["name"]] = int(v)
            elif c["type"] == "REAL":
                row[c["name"]] = float(v)
    return {"columns": columns, "rows": parsed_rows, "row_count": len(parsed_rows), "table_name": "data"}


def _build_memory_db(table: str, columns: list[dict], rows: list[dict]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    col_defs = ", ".join(f'"{c["name"]}" {c["type"]}' for c in columns)
    conn.execute(f'CREATE TABLE "{table}" ({col_defs})')
    col_names = [c["name"] for c in columns]
    placeholders = ", ".join("?" for _ in col_names)
    conn.executemany(
        f'INSERT INTO "{table}" ({", ".join(f'"{c}"' for c in col_names)}) VALUES ({placeholders})',
        [[row.get(c) for c in col_names] for row in rows],
    )
    conn.commit()
    return conn


_SELECT_ONLY = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|attach|pragma|replace)\b", re.IGNORECASE)


def run_readonly_sql(
    table: str, columns: list[dict], rows: list[dict], sql: str, limit: int = 200
) -> dict[str, Any]:
    """在内存 SQLite 上执行只读 SQL，返回 {columns, rows}。仅允许 SELECT/WITH。"""
    if not _SELECT_ONLY.match(sql) or _FORBIDDEN.search(sql):
        raise ValueError("仅允许只读 SELECT 查询")
    conn = _build_memory_db(table, columns, rows)
    try:
        cur = conn.execute(sql)
        result_cols = [d[0] for d in cur.description] if cur.description else []
        result_rows = [list(r) for r in cur.fetchmany(limit)]
    finally:
        conn.close()
    return {"columns": result_cols, "rows": result_rows}

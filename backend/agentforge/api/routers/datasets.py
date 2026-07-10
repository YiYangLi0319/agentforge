"""数据分析 Agent：上传 CSV -> 自然语言问题 -> Text2SQL -> 结果表 + 图表数据 + 结论。"""

import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentforge.api.app import Container
from agentforge.api.deps import get_container, get_current_user, get_db, rate_limited
from agentforge.core.llm.structured import complete_json
from agentforge.core.messages import Message
from agentforge.db.models import Dataset, User
from agentforge.services.datasets import parse_csv, run_readonly_sql
from agentforge.services.quota import assert_within_quota

router = APIRouter()


def _dict(d: Dataset, with_preview: bool = False) -> dict:
    out = {
        "id": d.id,
        "name": d.name,
        "filename": d.filename,
        "columns": d.columns,
        "row_count": d.row_count,
        "created_at": d.created_at.isoformat(),
    }
    if with_preview:
        out["preview"] = (d.rows or [])[:20]
    return out


@router.get("")
async def list_datasets(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    rows = (
        (await db.execute(select(Dataset).where(Dataset.user_id == user.id).order_by(desc(Dataset.created_at))))
        .scalars()
        .all()
    )
    return [_dict(d) for d in rows]


@router.get("/{dataset_id}")
async def get_dataset(
    dataset_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    d = (
        await db.execute(select(Dataset).where(Dataset.id == dataset_id, Dataset.user_id == user.id))
    ).scalar_one_or_none()
    if d is None:
        raise HTTPException(status_code=404, detail="数据集不存在")
    return _dict(d, with_preview=True)


@router.post("", status_code=201)
async def upload_dataset(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    container: Container = Depends(get_container),
) -> dict:
    filename = file.filename or "data.csv"
    if not filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="仅支持 CSV 文件")
    data = await file.read()
    if len(data) > container.settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件过大")
    try:
        parsed = parse_csv(data, container.settings.max_dataset_rows)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"CSV 解析失败: {e}") from e

    ds = Dataset(
        user_id=user.id,
        name=Path(filename).stem,
        filename=filename,
        table_name=parsed["table_name"],
        columns=parsed["columns"],
        row_count=parsed["row_count"],
        rows=parsed["rows"],
    )
    db.add(ds)
    await db.commit()
    return _dict(ds, with_preview=True)


@router.delete("/{dataset_id}", status_code=204)
async def delete_dataset(
    dataset_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    d = (
        await db.execute(select(Dataset).where(Dataset.id == dataset_id, Dataset.user_id == user.id))
    ).scalar_one_or_none()
    if d is None:
        raise HTTPException(status_code=404, detail="数据集不存在")
    await db.delete(d)
    await db.commit()


class SQLPlan(BaseModel):
    sql: str = Field(description="一条只读 SQL SELECT 查询，表名固定为 data")
    chart: str = Field(default="table", description="建议可视化: table | bar | line")
    x: str = Field(default="", description="图表 X 轴列名（bar/line 用）")
    y: str = Field(default="", description="图表 Y 轴列名（数值列）")


class AnalyzeIn(BaseModel):
    question: str = Field(min_length=2, max_length=1000)


@router.post("/{dataset_id}/analyze")
async def analyze(
    dataset_id: str,
    body: AnalyzeIn,
    user: User = Depends(rate_limited("chat", "rate_limit_per_minute")),
    db: AsyncSession = Depends(get_db),
    container: Container = Depends(get_container),
) -> dict:
    await assert_within_quota(db, user, container.settings)
    d = (
        await db.execute(select(Dataset).where(Dataset.id == dataset_id, Dataset.user_id == user.id))
    ).scalar_one_or_none()
    if d is None:
        raise HTTPException(status_code=404, detail="数据集不存在")

    schema = ", ".join(f"{c['name']} {c['type']}" for c in d.columns)
    t0 = time.perf_counter()

    # 1) 自然语言 -> SQL（结构化输出）
    plan, usage1 = await complete_json(
        container.llm,
        [
            Message.user(
                "你是数据分析师。表名为 data，列定义如下：\n"
                f"{schema}\n\n"
                f"请针对问题写一条只读 SQL（表名必须用 data，方言 SQLite），并给出可视化建议。\n问题：{body.question}"
            )
        ],
        SQLPlan,
    )

    # 2) 执行只读 SQL
    try:
        result = run_readonly_sql(d.table_name, d.columns, d.rows, plan.sql)
    except (ValueError, Exception) as e:  # noqa: BLE001 SQL 报错回传给用户
        return {"error": f"SQL 执行失败: {e}", "sql": plan.sql}

    # 3) LLM 基于结果给出结论
    preview = {"columns": result["columns"], "rows": result["rows"][:30]}
    summary_resp = await container.llm.complete(
        [
            Message.user(
                f"根据以下查询结果，用中文简洁回答用户问题（2-4 句，指出关键数字）。\n"
                f"问题：{body.question}\n查询结果：{preview}"
            )
        ],
        temperature=0.2,
        max_tokens=300,
    )

    total_usage = usage1 + summary_resp.usage
    # 记录一次 run 以计入用量/配额
    from agentforge.db.models import Run, utcnow

    db.add(
        Run(
            user_id=user.id,
            kind="chat",
            status="succeeded",
            input={"message": body.question, "dataset_id": dataset_id},
            output={"text": summary_resp.message.content},
            prompt_tokens=total_usage.prompt_tokens,
            completion_tokens=total_usage.completion_tokens,
            finished_at=utcnow(),
        )
    )
    await db.commit()

    return {
        "question": body.question,
        "sql": plan.sql,
        "summary": summary_resp.message.content,
        "result": result,
        "chart": {"type": plan.chart, "x": plan.x, "y": plan.y},
        "latency_ms": int((time.perf_counter() - t0) * 1000),
    }

"""可观测看板：用量/成本/延迟聚合统计 + 系统能力状态；以及 Prometheus /metrics。"""

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentforge.api.app import Container
from agentforge.api.deps import get_container, get_current_user, get_db
from agentforge.db.models import Run, Span, User
from agentforge.observability.metrics import render_metrics

router = APIRouter()


@router.get("/stats")
async def dashboard_stats(
    days: int = 14,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    container: Container = Depends(get_container),
) -> dict:
    since = datetime.now(UTC) - timedelta(days=days)
    runs = (
        (
            await db.execute(
                select(Run).where(Run.user_id == user.id, Run.created_at >= since)
            )
        )
        .scalars()
        .all()
    )

    total_runs = len(runs)
    total_prompt = sum(r.prompt_tokens for r in runs)
    total_completion = sum(r.completion_tokens for r in runs)
    total_cost = round(sum(r.cost for r in runs), 4)
    succeeded = sum(1 for r in runs if r.status == "succeeded")

    by_kind: dict[str, int] = defaultdict(int)
    by_status: dict[str, int] = defaultdict(int)
    by_day: dict[str, dict] = defaultdict(lambda: {"runs": 0, "tokens": 0, "cost": 0.0})
    durations: list[float] = []
    for r in runs:
        by_kind[r.kind] += 1
        by_status[r.status] += 1
        day = r.created_at.strftime("%m-%d")
        by_day[day]["runs"] += 1
        by_day[day]["tokens"] += r.prompt_tokens + r.completion_tokens
        by_day[day]["cost"] = round(by_day[day]["cost"] + r.cost, 4)
        if r.finished_at:
            durations.append((r.finished_at - r.created_at).total_seconds())

    # 工具使用 Top（从 spans）
    tool_rows = (
        await db.execute(
            select(Span.name, func.count(Span.id))
            .join(Run, Run.id == Span.run_id)
            .where(Run.user_id == user.id, Span.kind == "tool", Span.started_at >= since)
            .group_by(Span.name)
            .order_by(func.count(Span.id).desc())
            .limit(10)
        )
    ).all()
    tool_usage = [{"tool": name.replace("tool:", ""), "count": cnt} for name, cnt in tool_rows]

    trend = [{"day": d, **v} for d, v in sorted(by_day.items())]
    avg_latency = round(sum(durations) / len(durations), 2) if durations else 0.0

    cache_stats = await container.semantic_cache.stats()

    return {
        "range_days": days,
        "totals": {
            "runs": total_runs,
            "success_rate": round(succeeded / total_runs, 4) if total_runs else 0.0,
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "total_tokens": total_prompt + total_completion,
            "cost": total_cost,
            "avg_latency_s": avg_latency,
        },
        "by_kind": dict(by_kind),
        "by_status": dict(by_status),
        "trend": trend,
        "tool_usage": tool_usage,
        "cache": cache_stats,
        "capabilities": {
            "llm": container.llm.describe(),
            "embedding": {"provider": container.embeddings.provider, "model": container.embeddings.model},
            "guardrails_enabled": container.settings.guardrails_enabled,
            "semantic_cache_enabled": container.settings.semantic_cache_enabled,
            "mcp_servers": container.mcp.status,
            "mcp_tools": len(container.mcp.tools),
            "rag": {
                "query_rewrite": container.settings.rag_query_rewrite,
                "hyde": container.settings.rag_hyde,
                "compression": container.settings.rag_compression,
                "parent_child": container.settings.rag_parent_child,
            },
        },
    }


@router.post("/cache/clear")
async def clear_cache(
    user: User = Depends(get_current_user), container: Container = Depends(get_container)
) -> dict:
    cleared = await container.semantic_cache.clear()
    return {"cleared": cleared}


# Prometheus 抓取端点（无需鉴权，通常由内网监控访问）
@router.get("/metrics")
async def metrics() -> Response:
    return Response(content=render_metrics(), media_type="text/plain; version=0.0.4")

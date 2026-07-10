"""深度研究路由：发起研究任务、查看报告。"""

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentforge.agents.deep_research import run_deep_research
from agentforge.api.app import Container
from agentforge.api.deps import get_container, get_current_user, get_db, rate_limited
from agentforge.core.events import AgentEvent, RunFailed, RunFinished
from agentforge.core.runtime import RunContext
from agentforge.db.models import ResearchReport, Run, User
from agentforge.services.quota import assert_within_quota
from agentforge.services.runs import RunLimitExceeded

router = APIRouter()


class ResearchCreate(BaseModel):
    query: str = Field(min_length=4, max_length=2000)


def _make_research_factory(container: Container, report_id: str, query: str):
    async def factory(ctx: RunContext) -> AsyncIterator[AgentEvent]:
        ctx.services.update({"search": container.search, "settings": container.settings})
        final: RunFinished | None = None
        failed: RunFailed | None = None
        async for ev in run_deep_research(
            query,
            ctx,
            llm=container.llm,
            judge_llm=container.judge_llm,
            max_workers=container.settings.research_max_workers,
            max_sources=container.settings.research_max_sources,
            max_revisions=container.settings.research_max_revisions,
            require_verified_sources=container.settings.search_provider != "mock",
        ):
            if isinstance(ev, RunFinished):
                final = ev
            elif isinstance(ev, RunFailed):
                failed = ev
            yield ev

        async with container.sessions() as db:
            report = (
                await db.execute(select(ResearchReport).where(ResearchReport.id == report_id))
            ).scalar_one()
            if final is not None:
                report.report_md = str(final.output.get("report", ""))
                report.plan = final.output.get("plan", {})
                report.sources = final.output.get("sources", [])
                report.review = final.output.get("review", {})
                report.status = "succeeded" if final.output.get("quality_passed", True) else "needs_review"
            else:
                report.status = "failed"
                report.review = {"error": failed.error if failed else "未知错误"}
            await db.commit()

    return factory


@router.post("", status_code=202)
async def create_research(
    body: ResearchCreate,
    user: User = Depends(rate_limited("research", "rate_limit_research_per_minute")),
    db: AsyncSession = Depends(get_db),
    container: Container = Depends(get_container),
) -> dict:
    await assert_within_quota(db, user, container.settings)
    report = ResearchReport(run_id="", user_id=user.id, query=body.query, status="running")
    db.add(report)
    await db.commit()

    try:
        run_id = await container.run_manager.start(
            user_id=user.id,
            kind="research",
            input={"query": body.query, "report_id": report.id},
            ctx=RunContext(),
            factory=_make_research_factory(container, report.id, body.query),
        )
    except RunLimitExceeded as exc:
        await db.delete(report)
        await db.commit()
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    report.run_id = run_id
    await db.commit()
    return {"run_id": run_id, "report_id": report.id}


@router.get("")
async def list_research(
    q: str = Query(default="", max_length=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    stmt = (
        select(ResearchReport, Run.status)
        .outerjoin(Run, Run.id == ResearchReport.run_id)
        .where(ResearchReport.user_id == user.id)
    )
    if q.strip():
        stmt = stmt.where(ResearchReport.query.ilike(f"%{q.strip()}%"))
    rows = (
        (
            await db.execute(
                stmt.order_by(desc(ResearchReport.created_at)).limit(50)
            )
        )
        .all()
    )
    return [
        {
            "id": r.id,
            "run_id": r.run_id,
            "query": r.query,
            "status": r.status if r.status == "needs_review" else (run_status or r.status),
            "created_at": r.created_at.isoformat(),
        }
        for r, run_status in rows
    ]


@router.post("/{report_id}/share")
async def share_research(
    report_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    """为报告生成公开只读分享链接（幂等：已存在则返回原 token）。"""
    import secrets

    r = (
        await db.execute(
            select(ResearchReport).where(ResearchReport.id == report_id, ResearchReport.user_id == user.id)
        )
    ).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    if r.status != "succeeded":
        raise HTTPException(status_code=400, detail="仅可分享已完成的报告")
    if not r.share_token:
        r.share_token = secrets.token_urlsafe(16)[:32]
        await db.commit()
    return {"share_token": r.share_token, "path": f"/share/research/{r.share_token}"}


@router.delete("/{report_id}/share", status_code=204)
async def unshare_research(
    report_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    r = (
        await db.execute(
            select(ResearchReport).where(ResearchReport.id == report_id, ResearchReport.user_id == user.id)
        )
    ).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    r.share_token = None
    await db.commit()


@router.get("/{report_id}")
async def get_research(
    report_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    row = (
        await db.execute(
            select(ResearchReport, Run.status)
            .outerjoin(Run, Run.id == ResearchReport.run_id)
            .where(ResearchReport.id == report_id, ResearchReport.user_id == user.id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    r, run_status = row
    return {
        "id": r.id,
        "run_id": r.run_id,
        "query": r.query,
        "status": r.status if r.status == "needs_review" else (run_status or r.status),
        "plan": r.plan,
        "report_md": r.report_md,
        "sources": r.sources,
        "review": r.review,
        "share_token": r.share_token,
        "created_at": r.created_at.isoformat(),
    }


@router.get("/{report_id}/export")
async def export_research(
    report_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    report = (
        await db.execute(
            select(ResearchReport).where(
                ResearchReport.id == report_id,
                ResearchReport.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    if not report.report_md:
        raise HTTPException(status_code=409, detail="报告尚未生成")
    return Response(
        content=report.report_md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="research-{report.id[:8]}.md"'},
    )

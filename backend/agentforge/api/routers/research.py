"""深度研究路由：发起研究任务、查看报告。"""

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentforge.agents.deep_research import run_deep_research
from agentforge.api.app import Container
from agentforge.api.deps import get_container, get_current_user, get_db, rate_limited
from agentforge.core.events import AgentEvent, RunFailed, RunFinished
from agentforge.core.runtime import RunContext
from agentforge.db.models import ResearchReport, User

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
                report.status = "succeeded"
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
    report = ResearchReport(run_id="", user_id=user.id, query=body.query, status="running")
    db.add(report)
    await db.commit()

    run_id = await container.run_manager.start(
        user_id=user.id,
        kind="research",
        input={"query": body.query, "report_id": report.id},
        ctx=RunContext(),
        factory=_make_research_factory(container, report.id, body.query),
    )
    report.run_id = run_id
    await db.commit()
    return {"run_id": run_id, "report_id": report.id}


@router.get("")
async def list_research(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    rows = (
        (
            await db.execute(
                select(ResearchReport)
                .where(ResearchReport.user_id == user.id)
                .order_by(desc(ResearchReport.created_at))
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "run_id": r.run_id,
            "query": r.query,
            "status": r.status,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.get("/{report_id}")
async def get_research(
    report_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    r = (
        await db.execute(
            select(ResearchReport).where(
                ResearchReport.id == report_id, ResearchReport.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    return {
        "id": r.id,
        "run_id": r.run_id,
        "query": r.query,
        "status": r.status,
        "plan": r.plan,
        "report_md": r.report_md,
        "sources": r.sources,
        "review": r.review,
        "created_at": r.created_at.isoformat(),
    }

"""公开只读接口：无需登录，用于分享链接（如研究报告）。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentforge.api.deps import get_db
from agentforge.db.models import ResearchReport

router = APIRouter()


@router.get("/research/{share_token}")
async def public_research(share_token: str, db: AsyncSession = Depends(get_db)) -> dict:
    r = (
        await db.execute(select(ResearchReport).where(ResearchReport.share_token == share_token))
    ).scalar_one_or_none()
    if r is None or r.status != "succeeded":
        raise HTTPException(status_code=404, detail="分享链接无效或已取消")
    return {
        "query": r.query,
        "report_md": r.report_md,
        "sources": r.sources,
        "created_at": r.created_at.isoformat(),
    }

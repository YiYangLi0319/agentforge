"""健康检查与运行环境信息。"""

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

import agentforge
from agentforge.db.base import CURRENT_SCHEMA_REVISION, current_schema_revision
from agentforge.db.types import pgvector_enabled

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    """兼容旧部署探针的轻量存活检查；流量准入请使用 /readyz。"""
    c = request.app.state.container
    return {
        "status": "ok",
        "version": agentforge.__version__,
        "env": c.settings.env,
        "db": c.engine.dialect.name,
    }


@router.get("/livez")
async def liveness() -> dict:
    return {"status": "ok", "version": agentforge.__version__}


@router.get("/readyz")
async def readiness(request: Request) -> JSONResponse:
    c = request.app.state.container
    checks: dict[str, object] = {}
    try:
        async with asyncio.timeout(3):
            async with c.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["database"] = "ok"
            revision = await current_schema_revision(c.engine)
            checks["schema_revision"] = revision or "unmanaged"
            if c.settings.is_production and revision != CURRENT_SCHEMA_REVISION:
                raise RuntimeError(
                    f"数据库迁移版本不匹配: current={revision or 'none'}, expected={CURRENT_SCHEMA_REVISION}"
                )
    except Exception as exc:  # noqa: BLE001 readiness 必须稳定返回结构化失败信息
        checks["database"] = checks.get("database", "error")
        checks["error"] = str(exc)[:300]
        return JSONResponse(status_code=503, content={"status": "not_ready", "checks": checks})

    checks["vector_storage"] = "pgvector" if pgvector_enabled() else "json"
    checks["run_mode"] = "single_instance"
    return JSONResponse(content={"status": "ready", "checks": checks})


@router.get("/meta")
async def meta_info(request: Request) -> dict:
    c = request.app.state.container
    return {
        "version": agentforge.__version__,
        "llm": c.llm.describe(),
        "judge": c.judge_llm.describe(),
        "embedding": {"provider": c.embeddings.provider, "model": c.embeddings.model, "dim": c.embeddings.dim},
        "mock_mode": c.llm.provider == "mock",
        "registration_requires_code": bool(c.settings.registration_invite_code),
    }

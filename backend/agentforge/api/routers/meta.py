"""健康检查与运行环境信息。"""

from fastapi import APIRouter, Request

import agentforge

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    c = request.app.state.container
    return {
        "status": "ok",
        "version": agentforge.__version__,
        "env": c.settings.env,
        "db": c.engine.dialect.name,
    }


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

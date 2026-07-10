"""FastAPI 应用工厂：装配依赖容器（LLM/Embedding/RunManager/限流器）与路由。"""

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from agentforge.config import Settings, get_settings
from agentforge.core.llm.base import ChatModel
from agentforge.core.llm.embeddings import Embeddings
from agentforge.core.llm.registry import build_chat_model, build_embeddings, build_judge_model
from agentforge.db.base import build_engine, build_sessionmaker, init_db

if TYPE_CHECKING:  # 仅类型标注使用，避免运行时循环导入
    from agentforge.core.guardrails import GuardrailsEngine
    from agentforge.core.mcp.registry import MCPManager
    from agentforge.core.tools.web_search import SearchProvider
    from agentforge.rag.retriever import HybridRetriever
    from agentforge.services.memory_store import DBMemoryStore
    from agentforge.services.ratelimit import RateLimiter
    from agentforge.services.runs import RunManager
    from agentforge.services.semantic_cache import SemanticCache

logger = logging.getLogger(__name__)


@dataclass
class Container:
    """应用级依赖容器：启动时装配一次，路由通过 Depends 获取。"""

    settings: Settings
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    llm: ChatModel
    judge_llm: ChatModel
    embeddings: Embeddings
    run_manager: "RunManager"
    limiter: "RateLimiter"
    retriever: "HybridRetriever"
    search: "SearchProvider"
    memory_store: "DBMemoryStore"
    guardrails: "GuardrailsEngine"
    semantic_cache: "SemanticCache"
    mcp: "MCPManager"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = build_engine(settings.database_url)
        await init_db(engine)
        sessions = build_sessionmaker(engine)

        from agentforge.core.guardrails import GuardrailsEngine
        from agentforge.core.mcp.registry import MCPManager
        from agentforge.core.tools.web_search import build_search_provider
        from agentforge.rag.rerank import build_reranker
        from agentforge.rag.retriever import HybridRetriever
        from agentforge.services.memory_store import DBMemoryStore
        from agentforge.services.ratelimit import build_limiter
        from agentforge.services.runs import RunManager
        from agentforge.services.semantic_cache import SemanticCache

        chat_llm = build_chat_model(settings)
        embeddings = build_embeddings(settings)
        mcp = MCPManager()
        if settings.mcp_config_path:
            try:
                await mcp.load_from_file(settings.mcp_config_path)
            except Exception:  # noqa: BLE001 MCP 失败不阻断启动
                logger.exception("加载 MCP 配置失败")

        container = Container(
            settings=settings,
            engine=engine,
            sessions=sessions,
            llm=chat_llm,
            judge_llm=build_judge_model(settings),
            embeddings=embeddings,
            run_manager=RunManager(sessions),
            limiter=await build_limiter(settings),
            retriever=HybridRetriever(sessions, embeddings, build_reranker(settings)),
            search=build_search_provider(settings.tavily_api_key, settings.search_provider),
            memory_store=DBMemoryStore(sessions),
            guardrails=GuardrailsEngine(
                enabled=settings.guardrails_enabled,
                block_injection=settings.guardrails_block_injection,
                mask_output_pii=settings.guardrails_mask_pii,
                moderation=settings.guardrails_moderation,
            ),
            semantic_cache=SemanticCache(
                sessions,
                embeddings,
                enabled=settings.semantic_cache_enabled,
                threshold=settings.semantic_cache_threshold,
                ttl_seconds=settings.semantic_cache_ttl_seconds,
            ),
            mcp=mcp,
        )
        app.state.container = container

        Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
        logger.info(
            "AgentForge 启动完成 | db=%s | llm=%s/%s | embedding=%s | mcp_tools=%s",
            engine.dialect.name,
            container.llm.provider,
            container.llm.model,
            container.embeddings.provider,
            len(mcp.tools),
        )
        yield
        await container.run_manager.shutdown()
        await mcp.shutdown()
        await engine.dispose()

    app = FastAPI(
        title="AgentForge API",
        version="0.1.0",
        description="企业级多智能体应用平台：自研 Agent 引擎 / Agentic RAG / 深度研究",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("未处理异常: %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": f"服务器内部错误: {type(exc).__name__}"})

    from agentforge.api.routers import (
        admin,
        agents,
        auth,
        chat,
        dashboard,
        datasets,
        feedback,
        knowledge,
        meta,
        public,
        research,
        runs,
        tools,
        traces,
    )

    app.include_router(meta.router, prefix="/api", tags=["meta"])
    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
    app.include_router(runs.router, prefix="/api/runs", tags=["runs"])
    app.include_router(knowledge.router, prefix="/api/kb", tags=["knowledge"])
    app.include_router(research.router, prefix="/api/research", tags=["research"])
    app.include_router(traces.router, prefix="/api/traces", tags=["traces"])
    app.include_router(tools.router, prefix="/api/tools", tags=["tools"])
    app.include_router(dashboard.router, prefix="/api/dashboard", tags=["dashboard"])
    app.include_router(agents.router, prefix="/api/agents", tags=["agents"])
    app.include_router(feedback.router, prefix="/api/feedback", tags=["feedback"])
    app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
    app.include_router(datasets.router, prefix="/api/datasets", tags=["datasets"])
    app.include_router(public.router, prefix="/api/public", tags=["public"])

    _mount_frontend(app, settings)
    return app


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    """若存在前端构建产物，则由后端同源托管（单镜像部署）；否则跳过（纯 API 模式）。"""
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    static_dir = Path(settings.static_dir)
    index_file = static_dir / "index.html"
    if not index_file.exists():
        return

    assets_dir = static_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    _RESERVED = ("api/", "api", "docs", "redoc", "openapi.json")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        # 保留后端路由不被 SPA 兜底吞掉
        if full_path.startswith(_RESERVED):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = static_dir / full_path
        if full_path and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(index_file))  # 客户端路由回退到 index.html

    logger.info("已启用前端同源托管: %s", static_dir.resolve())

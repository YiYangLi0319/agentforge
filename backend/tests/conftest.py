"""测试公共夹具：核心引擎测试无需数据库；API 测试用 SQLite + Mock Provider。"""

import pytest

from agentforge.core.runtime import RunContext
from agentforge.core.tracing import Tracer


@pytest.fixture
def run_ctx() -> RunContext:
    return RunContext(run_id="test-run", user_id="u1", tracer=Tracer())


@pytest.fixture
def app_settings(tmp_path):
    from agentforge.config import Settings

    return Settings(
        AGENTFORGE_ENV="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        redis_url="redis://127.0.0.1:1/0",  # 故意不可用，验证内存限流降级
        llm_provider="mock",
        embedding_provider="mock",
        search_provider="mock",
        upload_dir=str(tmp_path / "uploads"),
        secret_key="test-secret-key-0123456789abcdef-0123456789abcdef",
        sandbox_requires_approval=False,
    )


@pytest.fixture
async def app(app_settings):
    from asgi_lifespan import LifespanManager

    from agentforge.api.app import create_app

    application = create_app(app_settings)
    async with LifespanManager(application, startup_timeout=30, shutdown_timeout=30):
        yield application


@pytest.fixture
async def client(app):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def auth_headers(client) -> dict:
    await client.post("/api/auth/register", json={"username": "tester", "password": "pass1234"})
    resp = await client.post("/api/auth/login", json={"username": "tester", "password": "pass1234"})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

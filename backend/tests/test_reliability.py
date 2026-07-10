"""生产可靠性回归：配置门禁、并发准入与重启状态收敛。"""

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from agentforge.config import Settings
from agentforge.core.events import MemoryUpdated, RunFinished
from agentforge.core.runtime import RunContext
from agentforge.db.base import Base, build_engine
from agentforge.db.models import ResearchReport, Run, RunEvent, User
from agentforge.services.runs import RunLimitExceeded, RunManager


def test_production_settings_fail_closed():
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(
            AGENTFORGE_ENV="prod",
            secret_key="dev-secret-change-me",
            llm_provider="deepseek",
            llm_api_key="test-key",
        ).validate_runtime()

    settings = Settings(
        AGENTFORGE_ENV="prod",
        secret_key="x" * 40,
        llm_provider="deepseek",
        llm_api_key="test-key",
        sandbox_enabled=True,
    )
    with pytest.raises(ValueError, match="SANDBOX_ENABLED"):
        settings.validate_runtime()


async def _db(tmp_path):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/reliability.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def test_run_manager_enforces_session_limit(tmp_path):
    engine, sessions = await _db(tmp_path)
    manager = RunManager(sessions, max_concurrent=4, max_per_user=2, max_per_session=1)
    release = asyncio.Event()

    async def slow_factory(ctx):
        await release.wait()
        yield RunFinished(output={"text": "ok"})

    first = await manager.start(
        user_id="u1",
        kind="chat",
        input={},
        session_id="s1",
        ctx=RunContext(),
        factory=slow_factory,
    )
    with pytest.raises(RunLimitExceeded, match="该会话"):
        await manager.start(
            user_id="u1",
            kind="chat",
            input={},
            session_id="s1",
            ctx=RunContext(),
            factory=slow_factory,
        )
    release.set()
    await manager._tasks[first]
    await engine.dispose()


async def test_late_cancel_does_not_flip_finished_run(tmp_path):
    """终态事件发出后，收尾阶段（如长期记忆）被取消不得回退已完成状态。"""
    engine, sessions = await _db(tmp_path)
    manager = RunManager(sessions)
    entered_tail = asyncio.Event()
    release = asyncio.Event()

    async def factory(ctx):
        yield RunFinished(output={"text": "done"})
        try:
            entered_tail.set()
            await release.wait()  # 模拟终态后的最佳努力收尾
            yield MemoryUpdated(added=1)
        except asyncio.CancelledError:
            return

    run_id = await manager.start(
        user_id="u1", kind="chat", input={}, ctx=RunContext(), factory=factory
    )
    await asyncio.wait_for(entered_tail.wait(), timeout=2)
    assert manager.cancel(run_id)
    await manager._tasks[run_id]

    async with sessions() as db:
        run = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one()
        events = (
            await db.execute(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq))
        ).scalars().all()
    assert run.status == "succeeded"
    types = [e.type for e in events]
    assert "run_finished" in types
    # 完成之后不得再出现 run_cancelled / run_failed 这种矛盾终态
    assert "run_cancelled" not in types and "run_failed" not in types
    await engine.dispose()


async def test_recover_interrupted_runs_and_reports(tmp_path):
    engine, sessions = await _db(tmp_path)
    async with sessions() as db:
        user = User(username="recovery", password_hash="!")
        db.add(user)
        await db.flush()
        run = Run(user_id=user.id, kind="research", status="running", input={})
        db.add(run)
        await db.flush()
        report = ResearchReport(
            run_id=run.id,
            user_id=user.id,
            query="恢复测试",
            status="running",
        )
        db.add(report)
        await db.commit()
        run_id, report_id = run.id, report.id

    manager = RunManager(sessions)
    assert await manager.recover_interrupted() == 1
    async with sessions() as db:
        run = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one()
        report = (
            await db.execute(select(ResearchReport).where(ResearchReport.id == report_id))
        ).scalar_one()
        assert run.status == "interrupted" and run.finished_at is not None
        assert report.status == "interrupted"
    await engine.dispose()

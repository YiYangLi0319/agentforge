"""端到端演示脚本：零外部依赖，进程内拉起 AgentForge 并叙述式走完整条业务链路。

与 smoke_e2e.py（对运行中的服务做断言式冒烟）互补：本脚本用 Mock Provider 在内存/临时
SQLite 中自包含运行，打印真实的回答、引用、缓存命中、研究报告与实时可观测数据，适合演示
或让评审者一键体验：

    python scripts/demo_e2e.py

不需要任何 API Key、数据库或联网。
"""

import asyncio
import sys
import tempfile
import time
from pathlib import Path

# 演示脚本含中文与 ¥ 符号；在 Windows GBK 控制台下强制 UTF-8 输出，避免编码报错/乱码。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def hr(title: str) -> None:
    print("\n" + "=" * 68)
    print(f" {title}")
    print("=" * 68)


def kv(label: str, value: str) -> None:
    print(f"  - {label}: {value}")


async def run_eval_snapshot(settings) -> dict | None:
    """在应用启动前用同一 DB 跑一次检索评估并落库，供看板"评估回归"面板展示。

    评估器自带独立引擎，跑完即关闭，避免与应用并发访问同一 SQLite 文件。
    """
    try:
        from agentforge.evals.runner import (
            DATASETS_DIR,
            EvalContext,
            persist_record,
            run_retrieval_suite,
        )

        ectx = EvalContext(settings)
        await ectx.setup()
        try:
            result = await run_retrieval_suite(ectx, DATASETS_DIR / "retrieval_zh.jsonl", top_k=5)
            await persist_record(ectx, result, used_judge=False)
            return result
        finally:
            await ectx.close()
    except Exception as exc:  # noqa: BLE001 评估为增强项，失败不影响主演示
        print(f"[提示] 评估快照已跳过：{exc}")
        return None


async def _drain(client, run_id: str, headers: dict, timeout: float = 60.0) -> dict:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        run = (await client.get(f"/api/runs/{run_id}", headers=headers)).json()
        if run["status"] in ("succeeded", "failed", "cancelled", "needs_review"):
            return run
        await asyncio.sleep(0.1)
    raise TimeoutError(f"运行 {run_id} 未在 {timeout}s 内结束")


async def run_demo(client, eval_result: dict | None) -> None:
    hr("1) 注册并登录")
    await client.post("/api/auth/register", json={"username": "demo", "password": "demo12345"})
    login = await client.post("/api/auth/login", json={"username": "demo", "password": "demo12345"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    kv("账号", "demo")
    kv("鉴权", "JWT 已获取")

    hr("2) 导入样例知识库并等待入库")
    kb = (await client.post("/api/kb", json={"name": "演示库"}, headers=headers)).json()
    await client.post(f"/api/kb/{kb['id']}/load-samples", headers=headers)
    docs: list[dict] = []
    for _ in range(120):
        docs = (await client.get(f"/api/kb/{kb['id']}/documents", headers=headers)).json()
        states = {d["status"] for d in docs}
        if docs and states == {"ready"}:
            break
        if "failed" in states:
            raise RuntimeError(f"入库失败: {[d.get('error') for d in docs]}")
        await asyncio.sleep(0.3)
    kv("文档", f"{len(docs)} 篇")
    kv("分块", str(sum(d["chunk_count"] for d in docs)))

    hr("3) 知识库问答（混合检索 + 引用溯源）")
    question = "公司年假有多少天"
    session = (
        await client.post("/api/chat/sessions", json={"kb_ids": [kb["id"]]}, headers=headers)
    ).json()
    posted = (
        await client.post(
            f"/api/chat/sessions/{session['id']}/messages",
            json={"content": question},
            headers=headers,
        )
    ).json()
    run = await _drain(client, posted["run_id"], headers)
    output = run.get("output") or {}
    kv("问题", question)
    kv("回答", (output.get("text") or "").strip()[:220] or "（空）")
    sources = output.get("sources") or []
    cited = ", ".join(f"[{s['id']}]{s.get('title') or s.get('filename')}" for s in sources[:3])
    kv("引用来源", f"{len(sources)} 条 " + cited)
    kv("用量", f"{run['prompt_tokens'] + run['completion_tokens']} tokens · 约 ¥{run['cost']}")

    hr("4) 语义缓存命中（同问再问一次）")
    posted2 = (
        await client.post(
            f"/api/chat/sessions/{session['id']}/messages",
            json={"content": question},
            headers=headers,
        )
    ).json()
    run2 = await _drain(client, posted2["run_id"], headers)
    kv("是否命中缓存", "是（跳过 Agent 执行）" if (run2.get("output") or {}).get("cached") else "否")

    hr("5) 深度研究（规划 → 并行搜索 → 评审修订）")
    research = (
        await client.post(
            "/api/research",
            json={"query": "AI Agent 在企业客服场景的落地路径"},
            headers=headers,
        )
    ).json()
    await _drain(client, research["run_id"], headers, timeout=120.0)
    report = (await client.get(f"/api/research/{research['report_id']}", headers=headers)).json()
    kv("状态", report["status"])
    review = report.get("review") or {}
    if "scores" in review:
        kv("评审分数", str(review.get("scores")))
    kv("报告长度", f"{len(report.get('report_md') or '')} 字")
    kv("来源数", str(len(report.get("sources") or [])))
    head = (report.get("report_md") or "").strip().splitlines()
    for line in head[:6]:
        print(f"    | {line}")

    hr("6) 评估回归（检索指标快照）")
    if eval_result:
        m = eval_result["metrics"]
        kv("数据集", eval_result["dataset"])
        kv("指标", " · ".join(f"{k}={v}" for k, v in m.items()))
    else:
        kv("评估", "本次未生成（可运行 python -m agentforge.evals.runner all）")

    hr("7) 可观测：聚合统计 + 实时曲线 + 评估趋势")
    stats = (await client.get("/api/dashboard/stats", headers=headers)).json()
    tot = stats["totals"]
    kv("总运行", f"{tot['runs']}（成功率 {tot['success_rate'] * 100:.0f}%）")
    kv("累计 tokens / 成本", f"{tot['total_tokens']} · ¥{tot['cost']}")
    kv("缓存条目 / 会话命中率", f"{stats['cache']['entries']} · {stats['cache']['hit_rate'] * 100:.0f}%")
    live = (await client.get("/api/dashboard/live?minutes=30&buckets=30", headers=headers)).json()
    ls = live["summary"]
    hit_rate = "—" if ls["hit_rate"] is None else f"{ls['hit_rate'] * 100:.0f}%"
    kv("近30分钟", f"运行 {ls['runs']} · 缓存命中率 {hit_rate} · SSE 重连 {ls['sse_reconnects']}")
    evals = (await client.get("/api/dashboard/evals", headers=headers)).json()
    kv("评估记录", " / ".join(f"{suite}:{len(recs)}条" for suite, recs in evals["suites"].items()) or "无")

    hr("8) Prometheus 指标摘录（/api/dashboard/metrics）")
    metrics_text = (await client.get("/api/dashboard/metrics")).text
    shown = [
        ln
        for ln in metrics_text.splitlines()
        if ln.startswith("agentforge_") and "runs_total" in ln or "cache_events" in ln
    ]
    for ln in shown[:6]:
        print(f"    {ln}")

    print("\n演示完成：以上全部使用 Mock Provider 在临时 SQLite 中自包含运行。")


async def main() -> None:
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport, AsyncClient

    from agentforge.api.app import create_app
    from agentforge.config import Settings

    tmp = Path(tempfile.mkdtemp(prefix="agentforge-demo-"))
    settings = Settings(
        AGENTFORGE_ENV="test",
        database_url=f"sqlite+aiosqlite:///{tmp}/demo.db",
        redis_url="redis://127.0.0.1:1/0",  # 不可用，走内存限流降级
        llm_provider="mock",
        embedding_provider="mock",
        search_provider="mock",
        upload_dir=str(tmp / "uploads"),
        secret_key="demo-secret-key-0123456789abcdef-0123456789abcdef",
        semantic_cache_enabled=True,
    )
    print(f"AgentForge 端到端演示（临时目录 {tmp}）")
    # 应用启动前先跑一次评估快照（独立引擎，避免与应用并发访问同一 SQLite）
    eval_result = await run_eval_snapshot(settings)
    app = create_app(settings)
    async with LifespanManager(app, startup_timeout=30, shutdown_timeout=30):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://demo") as client:
            await run_demo(client, eval_result)


if __name__ == "__main__":
    asyncio.run(main())

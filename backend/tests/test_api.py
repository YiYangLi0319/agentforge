"""API 集成测试：认证、知识库全流程、聊天 SSE、HITL 审批、限流、追踪、研究任务。"""

import asyncio
import json

from agentforge.core.llm.mock import MockChatModel
from agentforge.core.messages import Message, ToolCall


async def read_sse_events(client, url: str, headers: dict, timeout: float = 15.0) -> list[dict]:
    """消费 SSE 直到流结束，返回事件 payload 列表。"""
    events: list[dict] = []

    async def consume():
        async with client.stream("GET", url, headers=headers) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    payload = json.loads(line[5:].strip())
                    events.append(payload)

    await asyncio.wait_for(consume(), timeout=timeout)
    return events


# ---------- 基础 ----------


async def test_health_and_meta(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok" and body["db"] == "sqlite"
    assert (await client.get("/api/livez")).json()["status"] == "ok"
    ready = await client.get("/api/readyz")
    assert ready.status_code == 200 and ready.json()["status"] == "ready"

    meta = (await client.get("/api/meta")).json()
    assert meta["mock_mode"] is True


async def test_auth_flow(client):
    r = await client.post("/api/auth/register", json={"username": "alice", "password": "secret123"})
    assert r.status_code == 201
    token = r.json()["access_token"]

    dup = await client.post("/api/auth/register", json={"username": "alice", "password": "secret123"})
    assert dup.status_code == 409

    bad = await client.post("/api/auth/login", json={"username": "alice", "password": "wrongpass1"})
    assert bad.status_code == 401

    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["username"] == "alice"

    unauth = await client.get("/api/auth/me")
    assert unauth.status_code == 401


async def test_registration_invite_code(app, client):
    container = app.state.container
    container.settings.registration_invite_code = "SECRET2026"

    meta = (await client.get("/api/meta")).json()
    assert meta["registration_requires_code"] is True

    # 无邀请码 / 错误邀请码 -> 403
    no_code = await client.post("/api/auth/register", json={"username": "bob", "password": "secret123"})
    assert no_code.status_code == 403
    wrong = await client.post(
        "/api/auth/register", json={"username": "bob", "password": "secret123", "invite_code": "x"}
    )
    assert wrong.status_code == 403

    # 正确邀请码 -> 201
    ok = await client.post(
        "/api/auth/register",
        json={"username": "bob", "password": "secret123", "invite_code": "SECRET2026"},
    )
    assert ok.status_code == 201 and ok.json()["access_token"]
    container.settings.registration_invite_code = ""


async def test_api_key_auth(client, auth_headers):
    created = await client.post("/api/auth/api-keys", json={"name": "ci"}, headers=auth_headers)
    assert created.status_code == 201
    plain = created.json()["api_key"]
    assert plain.startswith("af_")

    me = await client.get("/api/auth/me", headers={"X-API-Key": plain})
    assert me.status_code == 200

    keys = (await client.get("/api/auth/api-keys", headers=auth_headers)).json()
    assert len(keys) == 1 and keys[0]["prefix"] == plain[:10]

    deleted = await client.delete(f"/api/auth/api-keys/{keys[0]['id']}", headers=auth_headers)
    assert deleted.status_code == 204
    denied = await client.get("/api/auth/me", headers={"X-API-Key": plain})
    assert denied.status_code == 401


# ---------- 知识库 ----------


async def _create_ready_kb(client, headers) -> str:
    kb = (await client.post("/api/kb", json={"name": "制度库"}, headers=headers)).json()
    content = (
        "# 报销制度\n\n## 时限\n\n报销单必须在费用发生后 30 天内提交，逾期需 VP 特批。\n\n"
        "## 住宿\n\n一线城市住宿标准为每晚 600 元。"
    )
    resp = await client.post(
        f"/api/kb/{kb['id']}/documents",
        files={"files": ("报销制度.md", content.encode("utf-8"), "text/markdown")},
        headers=headers,
    )
    assert resp.status_code == 202
    for _ in range(50):
        docs = (await client.get(f"/api/kb/{kb['id']}/documents", headers=headers)).json()
        if docs and docs[0]["status"] == "ready":
            assert docs[0]["chunk_count"] >= 1
            return kb["id"]
        if docs and docs[0]["status"] == "failed":
            raise AssertionError(f"入库失败: {docs[0]['error']}")
        await asyncio.sleep(0.1)
    raise AssertionError("入库超时")


async def test_kb_upload_and_playground_search(client, auth_headers):
    kb_id = await _create_ready_kb(client, auth_headers)

    result = (
        await client.post(
            f"/api/kb/{kb_id}/search",
            json={"query": "报销时限是多少天", "top_k": 3, "mode": "hybrid"},
            headers=auth_headers,
        )
    ).json()
    assert result["results"], "应检索到内容"
    top = result["results"][0]
    assert "30 天" in top["content"]
    assert top["bm25_score"] > 0 and top["rrf_score"] > 0

    kbs = (await client.get("/api/kb", headers=auth_headers)).json()
    assert kbs[0]["doc_count"] == 1 and kbs[0]["chunk_count"] >= 1

    bad = await client.post(
        f"/api/kb/{kb_id}/documents",
        files={"files": ("evil.exe", b"MZ", "application/octet-stream")},
        headers=auth_headers,
    )
    assert bad.status_code == 400


async def test_kb_isolation_between_users(client, auth_headers):
    kb_id = await _create_ready_kb(client, auth_headers)
    await client.post("/api/auth/register", json={"username": "mallory", "password": "secret123"})
    login = await client.post("/api/auth/login", json={"username": "mallory", "password": "secret123"})
    other = {"Authorization": f"Bearer {login.json()['access_token']}"}
    resp = await client.get(f"/api/kb/{kb_id}/documents", headers=other)
    assert resp.status_code == 404


# ---------- 聊天 + SSE + 引用 ----------


async def test_chat_flow_with_kb_citations(app, client, auth_headers):
    kb_id = await _create_ready_kb(client, auth_headers)
    session = (
        await client.post(
            "/api/chat/sessions", json={"kb_ids": [kb_id], "title": "问制度"}, headers=auth_headers
        )
    ).json()

    # 注入脚本化 Mock：先检索知识库，再给出带引用的回答
    container = app.state.container
    container.llm = MockChatModel(
        script=[
            Message.assistant(
                tool_calls=[
                    ToolCall(id="c1", name="search_knowledge_base", arguments={"query": "报销时限"})
                ]
            ),
            "根据公司制度，报销单需在费用发生后 30 天内提交 [1]。",
        ]
    )

    posted = (
        await client.post(
            f"/api/chat/sessions/{session['id']}/messages",
            json={"content": "报销时限是多少天？"},
            headers=auth_headers,
        )
    ).json()
    run_id = posted["run_id"]

    events = await read_sse_events(client, f"/api/runs/{run_id}/events", auth_headers)
    types = [e.get("type") for e in events]
    assert "tool_started" in types and "tool_finished" in types
    assert "run_finished" in types

    finished = next(e for e in events if e.get("type") == "run_finished")
    assert "30 天" in finished["output"]["text"]
    assert finished["output"]["sources"], "回答应有引用来源"
    assert finished["output"]["sources"][0]["origin"] == "kb"

    # 助手消息落库且带来源
    detail = (await client.get(f"/api/chat/sessions/{session['id']}", headers=auth_headers)).json()
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "assistant"]
    assert detail["messages"][1]["sources"]
    assert detail["title"] == "问制度"

    # 断线重放：after=0 再拉一遍能拿到持久化事件
    replay = await read_sse_events(client, f"/api/runs/{run_id}/events?after=0", auth_headers)
    assert any(e.get("type") == "run_finished" for e in replay)

    # 追踪
    trace = (await client.get(f"/api/traces/runs/{run_id}", headers=auth_headers)).json()
    kinds = [s["kind"] for s in trace["spans"]]
    assert "agent" in kinds and "llm" in kinds and "tool" in kinds
    assert trace["run"]["status"] == "succeeded"


async def test_chat_approval_flow(app, client, auth_headers):
    """HITL：python_execute 需审批 -> 拒绝 -> Agent 收到拒绝结果后继续完成。"""
    container = app.state.container
    container.settings.sandbox_requires_approval = True
    container.llm = MockChatModel(
        script=[
            Message.assistant(
                tool_calls=[ToolCall(id="c1", name="python_execute", arguments={"code": "print(1)"})]
            ),
            "好的，我不执行代码了。",
        ]
    )
    session = (await client.post("/api/chat/sessions", json={}, headers=auth_headers)).json()
    posted = (
        await client.post(
            f"/api/chat/sessions/{session['id']}/messages",
            json={"content": "帮我跑段代码"},
            headers=auth_headers,
        )
    ).json()
    run_id = posted["run_id"]

    # 注意：httpx ASGITransport 会缓冲流式响应，不能在消费 SSE 的同时发请求；
    # 这里用并发任务：一个消费事件流（阻塞到运行结束），一个轮询状态并提交审批。
    events: list[dict] = []

    async def consume():
        events.extend(
            await read_sse_events(client, f"/api/runs/{run_id}/events", auth_headers, timeout=25)
        )

    async def approve_when_ready():
        for _ in range(150):
            run = (await client.get(f"/api/runs/{run_id}", headers=auth_headers)).json()
            if run["status"] == "awaiting_approval" and run.get("pending_approvals"):
                r = await client.post(
                    f"/api/runs/{run_id}/approval",
                    json={"tool_call_id": run["pending_approvals"][0], "approved": False},
                    headers=auth_headers,
                )
                assert r.status_code == 200
                return
            await asyncio.sleep(0.1)
        raise AssertionError("未等到待审批状态")

    await asyncio.gather(consume(), approve_when_ready())
    types = [e.get("type") for e in events]
    assert "approval_required" in types and "approval_decided" in types
    decided = next(e for e in events if e.get("type") == "approval_decided")
    assert decided["approved"] is False
    tool_fin = next(e for e in events if e.get("type") == "tool_finished")
    assert tool_fin["ok"] is False and "拒绝" in tool_fin["result_preview"]
    assert any(e.get("type") == "run_finished" for e in events)


async def test_rate_limit(app, client, auth_headers):
    container = app.state.container
    container.settings.rate_limit_per_minute = 2
    session = (await client.post("/api/chat/sessions", json={}, headers=auth_headers)).json()

    codes = []
    run_ids = []
    for _ in range(3):
        r = await client.post(
            f"/api/chat/sessions/{session['id']}/messages",
            json={"content": "hi"},
            headers=auth_headers,
        )
        codes.append(r.status_code)
        if r.status_code == 202:
            run_ids.append(r.json()["run_id"])
    assert codes[:2] == [202, 202] and codes[2] == 429
    container.settings.rate_limit_per_minute = 60

    # 等待已发起的后台运行结束，避免 fixture 拆卸时强杀在途任务
    for rid in run_ids:
        for _ in range(100):
            run = (await client.get(f"/api/runs/{rid}", headers=auth_headers)).json()
            if run["status"] in ("succeeded", "failed", "cancelled"):
                break
            await asyncio.sleep(0.1)


# ---------- 深度研究 ----------


async def test_research_end_to_end(client, auth_headers):
    posted = await client.post(
        "/api/research", json={"query": "国产大模型 2026 竞争格局"}, headers=auth_headers
    )
    assert posted.status_code == 202
    body = posted.json()

    events = await read_sse_events(client, f"/api/runs/{body['run_id']}/events", auth_headers, timeout=30)
    types = [e.get("type") for e in events]
    assert "plan_created" in types and "report_draft" in types
    assert any(e.get("type") == "run_finished" for e in events)

    report = (await client.get(f"/api/research/{body['report_id']}", headers=auth_headers)).json()
    assert report["status"] == "succeeded"
    assert "## 参考来源" in report["report_md"]
    assert report["sources"]

    listing = (await client.get("/api/research", headers=auth_headers)).json()
    assert listing and listing[0]["id"] == body["report_id"]


async def test_runs_listing_in_traces(client, auth_headers):
    await client.post("/api/research", json={"query": "测试主题研究"}, headers=auth_headers)
    for _ in range(100):
        runs = (await client.get("/api/traces/runs", headers=auth_headers)).json()
        if runs and runs[0]["status"] in ("succeeded", "failed"):
            break
        await asyncio.sleep(0.1)
    assert runs[0]["kind"] == "research"
    assert runs[0]["status"] == "succeeded"

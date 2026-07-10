"""平台化功能测试：反馈、配额、自定义 Agent、管理后台、数据分析（Text2SQL）。"""

import asyncio
import json


async def read_sse(client, url, headers, timeout=20.0) -> list[dict]:
    events: list[dict] = []

    async def consume():
        async with client.stream("GET", url, headers=headers) as resp:
            assert resp.status_code == 200
            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    raw = line[5:].strip()
                    if raw and raw != "{}":
                        events.append(json.loads(raw))

    await asyncio.wait_for(consume(), timeout=timeout)
    return events


async def drain(client, run_id, headers) -> dict:
    for _ in range(150):
        run = (await client.get(f"/api/runs/{run_id}", headers=headers)).json()
        if run["status"] in ("succeeded", "failed", "cancelled"):
            return run
        await asyncio.sleep(0.1)
    raise AssertionError("run 未结束")


async def _chat_once(client, headers, content, session_id=None) -> dict:
    if session_id is None:
        session_id = (await client.post("/api/chat/sessions", json={}, headers=headers)).json()["id"]
    posted = (
        await client.post(
            f"/api/chat/sessions/{session_id}/messages", json={"content": content}, headers=headers
        )
    ).json()
    run = await drain(client, posted["run_id"], headers)
    return {"session_id": session_id, "run_id": posted["run_id"], "run": run}


# ---------- 反馈 ----------


async def test_feedback_submit_and_export(client, auth_headers):
    r = await _chat_once(client, auth_headers, "你好")
    sub = await client.post(
        "/api/feedback", json={"run_id": r["run_id"], "rating": "up", "comment": "很好"}, headers=auth_headers
    )
    assert sub.status_code == 201 and sub.json()["updated"] is False

    # 再次提交=更新
    sub2 = await client.post(
        "/api/feedback", json={"run_id": r["run_id"], "rating": "down"}, headers=auth_headers
    )
    assert sub2.json()["updated"] is True

    summary = (await client.get("/api/feedback/summary", headers=auth_headers)).json()
    assert summary["total"] == 1 and summary["down"] == 1

    export = await client.get("/api/feedback/export", headers=auth_headers)
    assert export.status_code == 200
    line = json.loads(export.text.splitlines()[0])
    assert "question" in line and line["rating"] == "down"


# ---------- 配额 ----------


async def test_quota_enforced(app, client, auth_headers):
    app.state.container.settings.daily_token_quota = 1  # 极小额度，第二次必超
    await _chat_once(client, auth_headers, "第一条消息会消耗 token")

    session = (await client.post("/api/chat/sessions", json={}, headers=auth_headers)).json()
    resp = await client.post(
        f"/api/chat/sessions/{session['id']}/messages", json={"content": "第二条"}, headers=auth_headers
    )
    assert resp.status_code == 429 and "额度" in resp.json()["detail"]

    me = (await client.get("/api/auth/me", headers=auth_headers)).json()
    assert me["quota"]["limit"] == 1 and me["quota"]["used"] >= 1
    app.state.container.settings.daily_token_quota = 200000


# ---------- 自定义 Agent ----------


async def test_custom_agent_crud_and_chat(app, client, auth_headers):
    tools = (await client.get("/api/agents/tools", headers=auth_headers)).json()
    assert any(t["name"] == "calculator" for t in tools)

    created = await client.post(
        "/api/agents",
        json={
            "name": "翻译官",
            "description": "中英互译",
            "system_prompt": "你是专业翻译，只输出译文。",
            "tools": ["calculator"],
            "kb_ids": [],
            "max_steps": 3,
            "temperature": 0.1,
        },
        headers=auth_headers,
    )
    assert created.status_code == 201
    agent_id = created.json()["id"]

    listing = (await client.get("/api/agents", headers=auth_headers)).json()
    assert any(a["id"] == agent_id for a in listing)

    # 用自定义 Agent 建会话并对话
    session = (
        await client.post(
            "/api/chat/sessions",
            json={"agent_type": "custom", "custom_agent_id": agent_id},
            headers=auth_headers,
        )
    ).json()
    assert session["custom_agent_id"] == agent_id
    r = await _chat_once(client, auth_headers, "把 hello 翻译成中文", session_id=session["id"])
    assert r["run"]["status"] == "succeeded" and r["run"]["output"].get("text")

    # 非法工具被拒
    bad = await client.post(
        "/api/agents", json={"name": "x", "tools": ["rm_rf"]}, headers=auth_headers
    )
    assert bad.status_code == 400

    deleted = await client.delete(f"/api/agents/{agent_id}", headers=auth_headers)
    assert deleted.status_code == 204


async def test_custom_agent_requires_id_for_custom_session(client, auth_headers):
    resp = await client.post("/api/chat/sessions", json={"agent_type": "custom"}, headers=auth_headers)
    assert resp.status_code == 400


# ---------- 管理后台 ----------


async def test_admin_requires_privilege(client, auth_headers):
    resp = await client.get("/api/admin/users", headers=auth_headers)
    assert resp.status_code == 403


async def test_admin_flow(app, client):
    app.state.container.settings.admin_username = "boss"
    app.state.container.settings.registration_invite_code = "admin-invite"
    await client.post(
        "/api/auth/register",
        json={"username": "boss", "password": "boss12345", "invite_code": "admin-invite"},
    )
    login = await client.post("/api/auth/login", json={"username": "boss", "password": "boss12345"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    me = (await client.get("/api/auth/me", headers=headers)).json()
    assert me["is_admin"] is True and me["quota"]["unlimited"] is True

    users = (await client.get("/api/admin/users", headers=headers)).json()
    assert any(u["username"] == "boss" and u["is_admin"] for u in users)
    target = next(u for u in users if u["username"] == "boss")

    upd = await client.patch(
        f"/api/admin/users/{target['id']}/quota", json={"daily_token_quota": 500}, headers=headers
    )
    assert upd.status_code == 200

    stats = (await client.get("/api/admin/stats", headers=headers)).json()
    assert stats["totals"]["users"] >= 1 and "feedback" in stats
    app.state.container.settings.admin_username = ""


# ---------- 数据分析 Text2SQL ----------


async def test_dataset_upload_and_analyze(app, client, auth_headers):
    csv = "city,sales,quarter\n北京,1200,Q1\n上海,1500,Q1\n北京,1400,Q2\n上海,1600,Q2\n"
    up = await client.post(
        "/api/datasets",
        files={"file": ("sales.csv", csv.encode("utf-8"), "text/csv")},
        headers=auth_headers,
    )
    assert up.status_code == 201
    ds = up.json()
    assert ds["row_count"] == 4
    cols = {c["name"]: c["type"] for c in ds["columns"]}
    assert cols["sales"] == "INTEGER" and cols["city"] == "TEXT"

    # 注入脚本让 Mock 生成确定的 SQL 计划 + 结论
    sql_plan = json.dumps(
        {
            "sql": "SELECT city, SUM(sales) AS total FROM data GROUP BY city",
            "chart": "bar",
            "x": "city",
            "y": "total",
        },
        ensure_ascii=False,
    )
    app.state.container.llm.push(sql_plan, "北京与上海销售额汇总如上。")
    res = await client.post(
        f"/api/datasets/{ds['id']}/analyze", json={"question": "各城市总销售额"}, headers=auth_headers
    )
    assert res.status_code == 200
    body = res.json()
    assert "error" not in body, body
    assert body["result"]["columns"] == ["city", "total"]
    assert len(body["result"]["rows"]) == 2
    assert body["chart"]["type"] == "bar"

    listing = (await client.get("/api/datasets", headers=auth_headers)).json()
    assert any(x["id"] == ds["id"] for x in listing)


async def test_research_share_public_link(client, auth_headers):
    # 发起研究并等其完成
    posted = (await client.post("/api/research", json={"query": "分享功能测试主题"}, headers=auth_headers)).json()
    await drain(client, posted["run_id"], auth_headers)
    report = (await client.get(f"/api/research/{posted['report_id']}", headers=auth_headers)).json()
    assert report["status"] == "succeeded"

    # 生成分享链接
    share = (await client.post(f"/api/research/{posted['report_id']}/share", headers=auth_headers)).json()
    token = share["share_token"]
    assert token and share["path"].endswith(token)

    # 公开接口无需鉴权即可访问
    pub = await client.get(f"/api/public/research/{token}")
    assert pub.status_code == 200
    assert pub.json()["query"] == "分享功能测试主题" and pub.json()["report_md"]

    # 取消分享后失效
    await client.delete(f"/api/research/{posted['report_id']}/share", headers=auth_headers)
    gone = await client.get(f"/api/public/research/{token}")
    assert gone.status_code == 404


async def test_dataset_analyze_rejects_write_sql(app, client, auth_headers):
    csv = "a,b\n1,2\n3,4\n"
    ds = (
        await client.post(
            "/api/datasets", files={"file": ("t.csv", csv.encode(), "text/csv")}, headers=auth_headers
        )
    ).json()
    app.state.container.llm.push('{"sql": "DELETE FROM data", "chart": "table", "x": "", "y": ""}', "x")
    res = await client.post(
        f"/api/datasets/{ds['id']}/analyze", json={"question": "删库"}, headers=auth_headers
    )
    assert res.status_code == 200 and "error" in res.json()

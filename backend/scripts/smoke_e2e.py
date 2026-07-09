"""端到端冒烟测试：对运行中的 AgentForge 服务执行完整业务流程验收。

用法：
    python scripts/smoke_e2e.py [BASE_URL]   # 默认 http://localhost:8000

流程：健康检查 -> 注册登录 -> 建知识库 -> 导入样例 -> 检索 Playground ->
      对话（SSE 流式 + 引用）-> 深度研究（SSE）-> 追踪与报告校验。
"""

import asyncio
import json
import sys
import time
import uuid

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"


def ok(step: str, detail: str = "") -> None:
    print(f"  [PASS] {step}" + (f"  ({detail})" if detail else ""))


async def read_sse(client: httpx.AsyncClient, url: str, headers: dict, timeout: float = 120.0) -> list[dict]:
    events: list[dict] = []
    async with client.stream("GET", url, headers=headers, timeout=timeout) as resp:
        assert resp.status_code == 200, f"SSE HTTP {resp.status_code}"
        async for line in resp.aiter_lines():
            if line.startswith("data:"):
                raw = line[5:].strip()
                if raw and raw != "{}":
                    events.append(json.loads(raw))
    return events


async def main() -> None:
    print(f"=== AgentForge E2E 冒烟测试: {BASE} ===\n")
    t0 = time.perf_counter()
    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as client:
        # 1. 健康检查
        health = (await client.get("/api/health")).json()
        assert health["status"] == "ok"
        ok("健康检查", f"db={health['db']}")

        # 2. 注册登录
        username = f"smoke_{uuid.uuid4().hex[:8]}"
        resp = await client.post("/api/auth/register", json={"username": username, "password": "smoke12345"})
        assert resp.status_code == 201, resp.text
        headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
        ok("注册登录", username)

        # 3. 知识库 + 样例导入
        kb = (await client.post("/api/kb", json={"name": "冒烟测试库"}, headers=headers)).json()
        resp = await client.post(f"/api/kb/{kb['id']}/load-samples", headers=headers)
        assert resp.status_code == 202, resp.text
        for _ in range(120):
            docs = (await client.get(f"/api/kb/{kb['id']}/documents", headers=headers)).json()
            states = {d["status"] for d in docs}
            if states == {"ready"}:
                break
            assert "failed" not in states, f"入库失败: {[d['error'] for d in docs if d['status'] == 'failed']}"
            await asyncio.sleep(0.5)
        else:
            raise AssertionError("样例入库超时")
        ok("知识库入库", f"{len(docs)} 文档 / {sum(d['chunk_count'] for d in docs)} 分块")

        # 4. 检索 Playground（三种模式）
        for mode in ("hybrid", "vector", "keyword"):
            result = (
                await client.post(
                    f"/api/kb/{kb['id']}/search",
                    json={"query": "报销单提交时限是多少天", "top_k": 3, "mode": mode},
                    headers=headers,
                )
            ).json()
            assert result["results"], f"{mode} 模式无结果"
        top = result["results"][0]
        assert "30 天" in top["content"], f"命中内容异常: {top['content'][:80]}"
        ok("混合检索", f"top1={top['filename']} rrf={top['rrf_score']}")

        # 5. 对话（SSE + 引用溯源）
        session = (
            await client.post("/api/chat/sessions", json={"kb_ids": [kb["id"]]}, headers=headers)
        ).json()
        posted = (
            await client.post(
                f"/api/chat/sessions/{session['id']}/messages",
                json={"content": "根据公司制度，报销单最晚多少天内提交？"},
                headers=headers,
            )
        ).json()
        events = await read_sse(client, f"/api/runs/{posted['run_id']}/events", headers)
        types = [e.get("type") for e in events]
        assert "run_finished" in types, f"对话未完成: {types}"
        finished = next(e for e in events if e.get("type") == "run_finished")
        assert finished["output"].get("sources"), "回答缺少引用来源"
        ok("流式对话", f"{len(events)} 事件, {len(finished['output']['sources'])} 引用")

        # 6. 深度研究
        research = (
            await client.post("/api/research", json={"query": "AI Agent 在企业客服场景的落地路径"}, headers=headers)
        ).json()
        events = await read_sse(client, f"/api/runs/{research['run_id']}/events", headers, timeout=300)
        types = [e.get("type") for e in events]
        assert "plan_created" in types and "report_draft" in types, f"研究流程异常: {set(types)}"
        report = (await client.get(f"/api/research/{research['report_id']}", headers=headers)).json()
        assert report["status"] == "succeeded" and "## 参考来源" in report["report_md"]
        ok("深度研究", f"报告 {len(report['report_md'])} 字, {len(report['sources'])} 来源")

        # 7. 追踪
        runs = (await client.get("/api/traces/runs", headers=headers)).json()
        assert len(runs) >= 2
        trace = (await client.get(f"/api/traces/runs/{posted['run_id']}", headers=headers)).json()
        kinds = {s["kind"] for s in trace["spans"]}
        assert {"agent", "llm", "tool"} <= kinds, f"Span 覆盖不全: {kinds}"
        ok("全链路追踪", f"{len(trace['spans'])} spans")

    print(f"\n=== 全部通过（{time.perf_counter() - t0:.1f}s）===")


if __name__ == "__main__":
    asyncio.run(main())

"""LLM 层测试：wire 转换、Mock 行为、OpenAI 兼容客户端（MockTransport）、结构化输出。"""

import json

import httpx
import pytest
from pydantic import BaseModel

from agentforge.core.llm.base import ChatResponse, StreamDelta
from agentforge.core.llm.mock import MockChatModel, generate_from_schema
from agentforge.core.llm.openai_compat import OpenAICompatChatModel, messages_to_wire
from agentforge.core.llm.pricing import estimate_cost
from agentforge.core.llm.structured import complete_json, extract_json
from agentforge.core.messages import Message, Role, ToolCall, Usage


def test_messages_to_wire_roundtrip():
    msgs = [
        Message.system("你是助手"),
        Message.user("你好"),
        Message.assistant(tool_calls=[ToolCall(id="c1", name="search", arguments={"q": "天气"})]),
        Message.tool_result("c1", "search", "晴天"),
    ]
    wire = messages_to_wire(msgs)
    assert wire[0] == {"role": "system", "content": "你是助手"}
    assert wire[2]["tool_calls"][0]["function"]["name"] == "search"
    assert json.loads(wire[2]["tool_calls"][0]["function"]["arguments"]) == {"q": "天气"}
    assert wire[2]["content"] is None
    assert wire[3]["tool_call_id"] == "c1"


async def test_mock_scripted_and_auto():
    llm = MockChatModel(script=["第一条回复"])
    r1 = await llm.complete([Message.user("hi")])
    assert r1.message.content == "第一条回复"
    assert r1.usage.completion_tokens > 0

    # 自动模式：有工具时先调用工具
    tools = [{"type": "function", "function": {"name": "t1", "parameters": {"properties": {"q": {"type": "string"}}}}}]
    r2 = await llm.complete([Message.user("查一下")], tools=tools)
    assert r2.message.tool_calls and r2.message.tool_calls[0].name == "t1"

    # 工具结果之后给最终回答
    r3 = await llm.complete(
        [Message.user("查一下"), r2.message, Message.tool_result(r2.message.tool_calls[0].id, "t1", "结果")],
        tools=tools,
    )
    assert not r3.message.tool_calls and r3.message.content


async def test_mock_stream_deltas():
    llm = MockChatModel(script=["这是一个足够长的回复内容，用来验证流式切片输出是否工作。"])
    deltas, final = [], None
    async for ev in llm.stream([Message.user("hi")]):
        if isinstance(ev, StreamDelta):
            deltas.append(ev.text)
        else:
            final = ev
    assert final is not None and "".join(deltas) == final.message.content


def test_generate_from_schema():
    class Item(BaseModel):
        name: str
        count: int
        tags: list[str]
        active: bool

    data = generate_from_schema(Item.model_json_schema(), seed="主题")
    parsed = Item.model_validate(data)
    assert parsed.count == 3 and parsed.active is True and len(parsed.tags) >= 1


async def test_openai_compat_complete_and_tool_parse():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {"id": "c9", "function": {"name": "f", "arguments": '{"x": 1}'}},
                                {"id": "c10", "function": {"name": "g", "arguments": "not-json"}},
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            },
        )

    llm = OpenAICompatChatModel(base_url="https://fake.local/v1", api_key="k", model="test-model")
    llm._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://fake.local/v1"
    )
    resp = await llm.complete([Message.user("hi")])
    assert resp.usage.prompt_tokens == 11
    assert resp.message.tool_calls[0].arguments == {"x": 1}
    assert "__raw__" in resp.message.tool_calls[1].arguments  # 非法 JSON 保留原文


async def test_openai_compat_stream_parsing():
    sse_lines = [
        'data: {"choices":[{"delta":{"content":"你"}}]}',
        'data: {"choices":[{"delta":{"content":"好"}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
        '"function":{"name":"f","arguments":"{\\"a\\""}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
        '"function":{"arguments":": 2}"}}]},"finish_reason":"tool_calls"}]}',
        'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":3}}',
        "data: [DONE]",
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content="\n".join(sse_lines).encode())

    llm = OpenAICompatChatModel(base_url="https://fake.local/v1", api_key="k", model="m")
    llm._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://fake.local/v1"
    )
    texts, final = [], None
    async for ev in llm.stream([Message.user("hi")]):
        if isinstance(ev, StreamDelta):
            texts.append(ev.text)
        else:
            final = ev
    assert "".join(texts) == "你好"
    assert final is not None
    assert final.message.tool_calls[0].arguments == {"a": 2}
    assert final.usage.prompt_tokens == 5


async def test_openai_compat_retry_on_500():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500, text="server error")
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    llm = OpenAICompatChatModel(base_url="https://fake.local/v1", api_key="k", model="m")
    llm._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://fake.local/v1"
    )
    resp = await llm.complete([Message.user("hi")])
    assert resp.message.content == "ok" and calls["n"] == 3


def test_extract_json_variants():
    assert json.loads(extract_json('```json\n{"a": 1}\n```')) == {"a": 1}
    assert json.loads(extract_json('前缀文字 {"a": {"b": 2}} 后缀')) == {"a": {"b": 2}}
    assert json.loads(extract_json("[1, 2]")) == [1, 2]


class Verdict(BaseModel):
    score: int
    reason: str


async def test_complete_json_retry_on_bad_output():
    llm = MockChatModel(script=["这不是JSON", '{"score": 4, "reason": "不错"}'])
    result, usage = await complete_json(llm, [Message.user("评分")], Verdict)
    assert result.score == 4 and usage.total_tokens > 0
    assert len(llm.calls) == 2  # 重试了一次


def test_pricing_prefix_match():
    usage = Usage(prompt_tokens=1_000_000, completion_tokens=1_000_000)
    assert estimate_cost("deepseek-chat", usage) == pytest.approx(10.0)
    assert estimate_cost("unknown-model", usage) == 0.0


def test_usage_add():
    u = Usage(prompt_tokens=1, completion_tokens=2) + Usage(prompt_tokens=3, completion_tokens=4)
    assert (u.prompt_tokens, u.completion_tokens, u.total_tokens) == (4, 6, 10)


def test_chat_response_roles():
    r = ChatResponse(message=Message.assistant("x"))
    assert r.message.role == Role.ASSISTANT

"""确定性 Mock Provider：无需 API Key 即可跑通全部流程（演示/测试/CI）。

两种模式：
- 脚本模式：测试中注入预设回复序列，逐条弹出；
- 自动模式：根据上下文启发式行动 —— 有 schema_hint 则按 JSON Schema 构造合法数据，
  有工具且尚未调用则先调用工具，否则给出最终回答。
"""

import hashlib
import json
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

from agentforge.core.llm.base import ChatModel, ChatResponse, ChatStreamEvent, StreamDelta
from agentforge.core.messages import (
    Message,
    Role,
    ToolCall,
    Usage,
    estimate_messages_tokens,
    estimate_tokens,
)

ScriptItem = ChatResponse | Message | str


def generate_from_schema(
    schema: dict, seed: str = "", path: str = "", defs: dict | None = None
) -> Any:
    """按 JSON Schema 生成确定性的合法数据（Mock 结构化输出的核心）。

    支持 pydantic 生成的 $defs/$ref、anyOf(Optional)、enum、嵌套对象与数组。
    """
    if defs is None:
        defs = schema.get("$defs") or schema.get("definitions") or {}

    if "$ref" in schema:
        ref_name = schema["$ref"].split("/")[-1]
        return generate_from_schema(defs.get(ref_name, {}), seed, path, defs)
    if "allOf" in schema and schema["allOf"]:
        return generate_from_schema(schema["allOf"][0], seed, path, defs)
    if "anyOf" in schema:
        options = [o for o in schema["anyOf"] if o.get("type") != "null"]
        return generate_from_schema(options[0] if options else {}, seed, path, defs)

    stype = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    if stype == "object" or "properties" in schema:
        return {
            key: generate_from_schema(sub, seed, f"{path}.{key}", defs)
            for key, sub in (schema.get("properties") or {}).items()
        }
    if stype == "array":
        n = max(schema.get("minItems", 2), 1)
        n = min(n, 2) if "minItems" not in schema else n
        return [
            generate_from_schema(schema.get("items", {"type": "string"}), seed, f"{path}[{i}]", defs)
            for i in range(n)
        ]
    if stype == "integer":
        lo, hi = schema.get("minimum"), schema.get("maximum")
        if lo is not None and hi is not None:
            return int((lo + hi + 1) // 2)  # 如 1-5 分制 -> 3
        return int(lo if lo is not None else (hi if hi is not None else 3))
    if stype == "number":
        lo, hi = schema.get("minimum"), schema.get("maximum")
        if lo is not None and hi is not None:
            return (lo + hi) / 2
        return float(lo if lo is not None else (hi if hi is not None else 3.0))
    if stype == "boolean":
        return True
    if stype == "string" or stype is None:
        name = path.split(".")[-1].strip("[01]") or "text"
        desc = schema.get("description", "")
        base = desc[:24] if desc else name
        suffix = path[-3:] if path.endswith("]") else ""
        topic = seed[:30]
        return f"{base}{suffix}（{topic}）" if topic else f"{base}{suffix}"
    return None


def _last_user_text(messages: list[Message]) -> str:
    for m in reversed(messages):
        if m.role == Role.USER:
            return m.content
    return ""


class MockChatModel(ChatModel):
    provider = "mock"

    def __init__(self, script: list[ScriptItem] | None = None, model: str = "mock-model"):
        self.model = model
        self.script: deque[ScriptItem] = deque(script or [])
        self.calls: list[dict] = []  # 测试断言用：记录每次调用的入参

    def push(self, *items: ScriptItem) -> None:
        self.script.extend(items)

    def _normalize(self, item: ScriptItem, messages: list[Message]) -> ChatResponse:
        if isinstance(item, ChatResponse):
            return item
        if isinstance(item, Message):
            return ChatResponse(message=item, usage=self._usage(messages, item.content), model=self.model)
        msg = Message.assistant(str(item))
        return ChatResponse(message=msg, usage=self._usage(messages, msg.content), model=self.model)

    def _usage(self, messages: list[Message], completion: str) -> Usage:
        return Usage(
            prompt_tokens=estimate_messages_tokens(messages),
            completion_tokens=max(estimate_tokens(completion), 1),
        )

    def _auto(
        self,
        messages: list[Message],
        tools: list[dict] | None,
        schema_hint: dict | None,
    ) -> ChatResponse:
        user_text = _last_user_text(messages)

        if schema_hint:
            data = generate_from_schema(schema_hint, seed=user_text)
            content = json.dumps(data, ensure_ascii=False)
            return ChatResponse(
                message=Message.assistant(content), usage=self._usage(messages, content), model=self.model
            )

        # 有工具且最后一条不是工具结果 -> 先调用第一个工具（演示 ReAct 循环）
        if tools and messages and messages[-1].role != Role.TOOL:
            fn = tools[0].get("function", {})
            params = (fn.get("parameters") or {}).get("properties") or {}
            args: dict[str, Any] = {}
            for pname, pschema in params.items():
                if pschema.get("type") == "string":
                    args[pname] = user_text[:60] or "示例查询"
                    break
            call_id = "mockcall_" + hashlib.md5(user_text.encode()).hexdigest()[:8]
            msg = Message.assistant(tool_calls=[ToolCall(id=call_id, name=fn.get("name", ""), arguments=args)])
            return ChatResponse(message=msg, usage=self._usage(messages, "tool_call"), model=self.model)

        # 最终回答：若上下文出现引用编号则带上引用（演示引用溯源链路）
        cite = ""
        for m in messages:
            if m.content and "[1]" in m.content:
                cite = "[1]"
                break
        content = (
            f"[Mock 回复] 关于「{user_text[:60]}」：这是确定性演示回答{cite}。"
            "配置真实模型 API Key 后即可获得真实回答。"
        )
        return ChatResponse(
            message=Message.assistant(content), usage=self._usage(messages, content), model=self.model
        )

    def _next(
        self,
        messages: list[Message],
        tools: list[dict] | None,
        response_format: dict | None,
        schema_hint: dict | None,
    ) -> ChatResponse:
        self.calls.append(
            {
                "messages": [m.model_dump() for m in messages],
                "tools": [t.get("function", {}).get("name") for t in tools or []],
                "response_format": response_format,
                "schema_hint": schema_hint,
            }
        )
        if self.script:
            return self._normalize(self.script.popleft(), messages)
        return self._auto(messages, tools, schema_hint)

    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
        schema_hint: dict | None = None,
    ) -> ChatResponse:
        return self._next(messages, tools, response_format, schema_hint)

    async def stream(
        self,
        messages: list[Message],
        *,
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
        schema_hint: dict | None = None,
    ) -> AsyncIterator[ChatStreamEvent]:
        resp = self._next(messages, tools, response_format, schema_hint)
        text = resp.message.content
        for i in range(0, len(text), 24):
            yield StreamDelta(text=text[i : i + 24])
        yield resp

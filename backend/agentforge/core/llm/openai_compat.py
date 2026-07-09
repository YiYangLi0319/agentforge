"""OpenAI 兼容客户端：基于 httpx 自研，支持流式、function calling、重试与用量统计。

兼容 DeepSeek / Qwen(DashScope) / GLM / Moonshot / OpenAI / vLLM 等一切
实现了 /chat/completions 协议的端点。
"""

import asyncio
import json
import logging
import random
from collections.abc import AsyncIterator

import httpx

from agentforge.core.llm.base import ChatModel, ChatResponse, ChatStreamEvent, LLMError, StreamDelta
from agentforge.core.messages import Message, Role, ToolCall, Usage

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 3


def messages_to_wire(messages: list[Message]) -> list[dict]:
    wire: list[dict] = []
    for m in messages:
        item: dict = {"role": m.role.value, "content": m.content}
        if m.role == Role.ASSISTANT and m.tool_calls:
            item["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in m.tool_calls
            ]
            if not m.content:
                item["content"] = None
        if m.role == Role.TOOL:
            item["tool_call_id"] = m.tool_call_id
            if m.name:
                item["name"] = m.name
        wire.append(item)
    return wire


def _parse_tool_calls(raw: list[dict] | None) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for item in raw or []:
        fn = item.get("function", {})
        args_raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(args_raw)
            if not isinstance(args, dict):
                args = {"value": args}
        except json.JSONDecodeError:
            # 参数非法 JSON 时保留原文，交给 Agent 循环反馈给模型自纠错
            args = {"__raw__": args_raw}
        calls.append(ToolCall(id=item.get("id") or f"call_{len(calls)}", name=fn.get("name", ""), arguments=args))
    return calls


class OpenAICompatChatModel(ChatModel):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        provider: str = "openai_compat",
        temperature: float = 0.3,
        timeout_read: float = 180.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.provider = provider
        self.default_temperature = temperature
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(connect=10.0, read=timeout_read, write=30.0, pool=10.0),
        )

    def _payload(
        self,
        messages: list[Message],
        tools: list[dict] | None,
        temperature: float | None,
        max_tokens: int | None,
        response_format: dict | None,
        stream: bool,
    ) -> dict:
        payload: dict = {
            "model": self.model,
            "messages": messages_to_wire(messages),
            "temperature": self.default_temperature if temperature is None else temperature,
        }
        if tools:
            payload["tools"] = tools
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if response_format:
            payload["response_format"] = response_format
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        return payload

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
        payload = self._payload(messages, tools, temperature, max_tokens, response_format, stream=False)
        data = await self._request_with_retry(payload)
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message", {})
        usage_raw = data.get("usage") or {}
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                content=msg.get("content") or "",
                tool_calls=_parse_tool_calls(msg.get("tool_calls")),
            ),
            usage=Usage(
                prompt_tokens=usage_raw.get("prompt_tokens", 0),
                completion_tokens=usage_raw.get("completion_tokens", 0),
            ),
            model=data.get("model", self.model),
            finish_reason=choice.get("finish_reason") or "",
        )

    async def _request_with_retry(self, payload: dict) -> dict:
        last_err: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = await self._client.post("/chat/completions", json=payload)
                if resp.status_code in RETRYABLE_STATUS:
                    raise LLMError(
                        f"HTTP {resp.status_code}: {resp.text[:200]}", retryable=True, status=resp.status_code
                    )
                if resp.status_code >= 400:
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:500]}", status=resp.status_code)
                return resp.json()
            except (httpx.TransportError, LLMError) as e:
                retryable = isinstance(e, httpx.TransportError) or (isinstance(e, LLMError) and e.retryable)
                last_err = e
                if not retryable or attempt == MAX_RETRIES:
                    raise LLMError(str(e), retryable=retryable) from e
                delay = 2**attempt + random.random()
                logger.warning("LLM 请求失败（第 %s 次重试，%.1fs 后）：%s", attempt + 1, delay, e)
                await asyncio.sleep(delay)
        raise LLMError(str(last_err))  # pragma: no cover

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
        payload = self._payload(messages, tools, temperature, max_tokens, response_format, stream=True)

        content_parts: list[str] = []
        tool_calls_acc: dict[int, dict] = {}
        usage = Usage()
        finish_reason = ""
        model_name = self.model

        for attempt in range(MAX_RETRIES + 1):
            try:
                async with self._client.stream("POST", "/chat/completions", json=payload) as resp:
                    if resp.status_code >= 400:
                        body = (await resp.aread()).decode("utf-8", "ignore")[:500]
                        retryable = resp.status_code in RETRYABLE_STATUS
                        if retryable and attempt < MAX_RETRIES:
                            raise LLMError(body, retryable=True, status=resp.status_code)
                        raise LLMError(f"HTTP {resp.status_code}: {body}", status=resp.status_code)

                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        if not data_str or data_str == "[DONE]":
                            continue
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        model_name = chunk.get("model") or model_name
                        if chunk.get("usage"):
                            usage = Usage(
                                prompt_tokens=chunk["usage"].get("prompt_tokens", 0),
                                completion_tokens=chunk["usage"].get("completion_tokens", 0),
                            )
                        for choice in chunk.get("choices") or []:
                            if choice.get("finish_reason"):
                                finish_reason = choice["finish_reason"]
                            delta = choice.get("delta") or {}
                            if delta.get("content"):
                                content_parts.append(delta["content"])
                                yield StreamDelta(text=delta["content"])
                            for tc in delta.get("tool_calls") or []:
                                idx = tc.get("index", 0)
                                acc = tool_calls_acc.setdefault(
                                    idx, {"id": "", "name": "", "arguments": ""}
                                )
                                if tc.get("id"):
                                    acc["id"] = tc["id"]
                                fn = tc.get("function") or {}
                                if fn.get("name"):
                                    acc["name"] += fn["name"]
                                if fn.get("arguments"):
                                    acc["arguments"] += fn["arguments"]
                break
            except (httpx.TransportError, LLMError) as e:
                retryable = isinstance(e, httpx.TransportError) or (isinstance(e, LLMError) and e.retryable)
                if not retryable or attempt == MAX_RETRIES or content_parts:
                    # 已开始输出内容后不再重试，避免内容重复
                    raise LLMError(str(e), retryable=retryable) from e
                delay = 2**attempt + random.random()
                logger.warning("LLM 流式连接失败（第 %s 次重试，%.1fs 后）：%s", attempt + 1, delay, e)
                await asyncio.sleep(delay)

        raw_calls = [
            {"id": acc["id"], "function": {"name": acc["name"], "arguments": acc["arguments"] or "{}"}}
            for _, acc in sorted(tool_calls_acc.items())
        ]
        content = "".join(content_parts)
        if usage.total_tokens == 0:
            # 厂商未返回 usage 时估算兜底
            from agentforge.core.messages import estimate_messages_tokens, estimate_tokens

            usage = Usage(
                prompt_tokens=estimate_messages_tokens(messages),
                completion_tokens=estimate_tokens(content),
            )
        yield ChatResponse(
            message=Message(role=Role.ASSISTANT, content=content, tool_calls=_parse_tool_calls(raw_calls)),
            usage=usage,
            model=model_name,
            finish_reason=finish_reason,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

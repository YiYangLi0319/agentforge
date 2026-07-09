"""对话模型抽象：统一 complete / stream 两个入口，屏蔽厂商差异。"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, Field

from agentforge.core.messages import Message, Usage


class LLMError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class ChatResponse(BaseModel):
    message: Message
    usage: Usage = Field(default_factory=Usage)
    model: str = ""
    finish_reason: str = ""


class StreamDelta(BaseModel):
    """流式输出的文本增量；工具调用增量在客户端内部聚合，最终以 ChatResponse 收尾。"""

    text: str = ""


ChatStreamEvent = StreamDelta | ChatResponse


class ChatModel(ABC):
    """所有 Provider 的统一接口。

    参数说明：
    - tools: OpenAI function calling 格式的工具 schema 列表
    - response_format: {"type": "json_object"} 等
    - schema_hint: 期望输出的 JSON Schema（供 Mock Provider 构造合法数据；
      真实 Provider 忽略此参数，由 structured 模块负责提示词注入）
    """

    model: str = ""
    provider: str = ""

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
        schema_hint: dict | None = None,
    ) -> ChatResponse: ...

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        *,
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
        schema_hint: dict | None = None,
    ) -> AsyncIterator[ChatStreamEvent]: ...

    def describe(self) -> dict[str, Any]:
        return {"provider": self.provider, "model": self.model}

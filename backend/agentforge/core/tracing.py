"""全链路追踪：run -> step -> llm/tool/retrieval 调用树，记录耗时/tokens/成本。

自研实现（不依赖 OpenTelemetry），通过 contextvar 维护父子关系，
并发分支（parallel tool calls / 并行子 Agent）也能正确归属父 Span。
"""

import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from agentforge.core.messages import Usage

_current_span_id: ContextVar[str | None] = ContextVar("agentforge_current_span", default=None)


class SpanData(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    parent_id: str | None = None
    name: str = ""
    kind: str = "chain"  # agent | llm | tool | retrieval | chain
    status: str = "ok"
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    started_at: float = Field(default_factory=time.time)
    ended_at: float | None = None

    def set_output(self, **kwargs: Any) -> None:
        self.output.update(kwargs)

    def set_usage(self, usage: Usage, cost: float = 0.0) -> None:
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens
        self.cost += cost

    @property
    def duration_ms(self) -> int:
        end = self.ended_at or time.time()
        return int((end - self.started_at) * 1000)


class Tracer:
    """收集一次 Run 内的所有 Span；由运行时负责持久化。"""

    def __init__(self) -> None:
        self.spans: list[SpanData] = []

    @asynccontextmanager
    async def span(self, name: str, kind: str = "chain", input: dict[str, Any] | None = None):
        data = SpanData(name=name, kind=kind, parent_id=_current_span_id.get(), input=input or {})
        self.spans.append(data)
        token = _current_span_id.set(data.id)
        try:
            yield data
        except Exception as e:
            data.status = "error"
            data.error = f"{type(e).__name__}: {e}"[:2000]
            raise
        finally:
            data.ended_at = time.time()
            _current_span_id.reset(token)

    def totals(self) -> tuple[Usage, float]:
        usage = Usage()
        cost = 0.0
        for s in self.spans:
            if s.kind == "llm":
                usage = usage + Usage(
                    prompt_tokens=s.prompt_tokens, completion_tokens=s.completion_tokens
                )
                cost += s.cost
        return usage, cost


class NoopTracer(Tracer):
    @asynccontextmanager
    async def span(self, name: str, kind: str = "chain", input: dict[str, Any] | None = None):
        yield SpanData(name=name, kind=kind)

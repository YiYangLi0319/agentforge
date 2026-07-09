"""Agent 事件协议：引擎以事件流对外输出全过程，前端/持久化/评估共用同一契约。"""

import time
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

from agentforge.core.messages import Message, ToolCall, Usage


class _BaseEvent(BaseModel):
    ts: float = Field(default_factory=time.time)
    agent: str | None = None  # 多 Agent 场景标记来源（如 "搜索员-1"）


class RunStarted(_BaseEvent):
    type: Literal["run_started"] = "run_started"
    run_id: str = ""
    kind: str = "chat"


class StepStarted(_BaseEvent):
    type: Literal["step_started"] = "step_started"
    step: int


class LLMDelta(_BaseEvent):
    """流式文本增量。"""

    type: Literal["llm_delta"] = "llm_delta"
    text: str
    channel: str = "answer"  # answer | report


class AssistantMessage(_BaseEvent):
    type: Literal["assistant_message"] = "assistant_message"
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    final: bool = False


class ToolStarted(_BaseEvent):
    type: Literal["tool_started"] = "tool_started"
    tool_call_id: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolFinished(_BaseEvent):
    type: Literal["tool_finished"] = "tool_finished"
    tool_call_id: str
    tool: str
    ok: bool = True
    result_preview: str = ""
    duration_ms: int = 0


class ApprovalRequired(_BaseEvent):
    """human-in-the-loop：高危工具执行前暂停，等待用户批准。"""

    type: Literal["approval_required"] = "approval_required"
    tool_call_id: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecided(_BaseEvent):
    type: Literal["approval_decided"] = "approval_decided"
    tool_call_id: str
    approved: bool


class StepFinished(_BaseEvent):
    type: Literal["step_finished"] = "step_finished"
    step: int
    usage: Usage = Field(default_factory=Usage)


class Checkpoint(_BaseEvent):
    """内部事件：消息快照。运行时持久化到 Run 行，不推送给前端。"""

    type: Literal["checkpoint"] = "checkpoint"
    messages: list[Message] = Field(default_factory=list)


class SourcesUpdated(_BaseEvent):
    type: Literal["sources_updated"] = "sources_updated"
    sources: list[dict] = Field(default_factory=list)


class PlanCreated(_BaseEvent):
    type: Literal["plan_created"] = "plan_created"
    plan: dict = Field(default_factory=dict)


class ResearchTaskStarted(_BaseEvent):
    type: Literal["research_task_started"] = "research_task_started"
    task_id: str
    title: str


class ResearchTaskFinished(_BaseEvent):
    type: Literal["research_task_finished"] = "research_task_finished"
    task_id: str
    ok: bool = True
    summary: str = ""
    evidence_count: int = 0


class ReportDraft(_BaseEvent):
    type: Literal["report_draft"] = "report_draft"
    markdown: str
    revision: int = 0


class ReportReview(_BaseEvent):
    type: Literal["report_review"] = "report_review"
    passed: bool
    scores: dict = Field(default_factory=dict)
    feedback: str = ""


class MemoryUpdated(_BaseEvent):
    type: Literal["memory_updated"] = "memory_updated"
    added: int = 0


class GuardrailTriggered(_BaseEvent):
    type: Literal["guardrail_triggered"] = "guardrail_triggered"
    stage: str = "input"  # input | output
    verdict: str = "allow"  # allow | block
    categories: list[str] = Field(default_factory=list)
    detail: str = ""


class CacheHit(_BaseEvent):
    type: Literal["cache_hit"] = "cache_hit"
    similarity: float = 0.0


class RunFinished(_BaseEvent):
    type: Literal["run_finished"] = "run_finished"
    output: dict = Field(default_factory=dict)
    usage: Usage = Field(default_factory=Usage)
    cost: float = 0.0


class RunFailed(_BaseEvent):
    type: Literal["run_failed"] = "run_failed"
    error: str = ""


class RunCancelled(_BaseEvent):
    type: Literal["run_cancelled"] = "run_cancelled"


AgentEvent = Annotated[
    RunStarted
    | StepStarted
    | LLMDelta
    | AssistantMessage
    | ToolStarted
    | ToolFinished
    | ApprovalRequired
    | ApprovalDecided
    | StepFinished
    | Checkpoint
    | SourcesUpdated
    | PlanCreated
    | ResearchTaskStarted
    | ResearchTaskFinished
    | ReportDraft
    | ReportReview
    | MemoryUpdated
    | GuardrailTriggered
    | CacheHit
    | RunFinished
    | RunFailed
    | RunCancelled,
    Field(discriminator="type"),
]

event_adapter: TypeAdapter[AgentEvent] = TypeAdapter(AgentEvent)

# 终止事件：收到即代表运行结束
TERMINAL_EVENTS = {"run_finished", "run_failed", "run_cancelled"}
# 内部事件：不推送、不落事件表（Checkpoint 单独存 Run.checkpoint 列）
INTERNAL_EVENTS = {"checkpoint"}


def dump_event(ev: Any) -> dict:
    return ev.model_dump(mode="json")


def load_event(data: dict) -> Any:
    return event_adapter.validate_python(data)

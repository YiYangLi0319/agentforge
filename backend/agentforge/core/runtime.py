"""运行上下文：Agent 执行所需的环境（追踪器、服务、审批门、共享状态）。

引擎侧只定义协议；DB 持久化、SSE 推送等由应用层 RunManager 实现。
注意：使用 dataclass 保证 services/state 按引用共享（pydantic 会拷贝 dict）。
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from agentforge.core.messages import ToolCall
from agentforge.core.tools.base import Tool, ToolContext
from agentforge.core.tracing import Tracer

# 审批门：返回 True=批准 / False=拒绝。应用层实现为"暂停运行等待用户决定"。
ApprovalGate = Callable[[ToolCall, Tool], Awaitable[bool]]


@dataclass
class RunContext:
    run_id: str = ""
    user_id: str | None = None
    session_id: str | None = None
    kb_ids: list[str] = field(default_factory=list)
    tracer: Tracer = field(default_factory=Tracer)
    services: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    approval_gate: ApprovalGate | None = None

    def tool_context(self, emit: Any = None) -> ToolContext:
        return ToolContext(
            run_id=self.run_id,
            user_id=self.user_id,
            session_id=self.session_id,
            kb_ids=self.kb_ids,
            services=self.services,
            state=self.state,
            emit=emit,
            run_ctx=self,
        )

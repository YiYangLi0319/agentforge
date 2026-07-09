"""工具系统：函数签名 + docstring 自动生成 JSON Schema，支持超时、审批标记与上下文注入。"""

import asyncio
import inspect
import re
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from pydantic import BaseModel


@dataclass
class ToolContext:
    """工具执行上下文：由运行时注入，工具通过它访问服务与共享状态。

    注意：必须是 dataclass 而非 pydantic 模型 —— services/state 需要按引用共享，
    工具对 state 的写入（如引用来源登记）要能被运行时与其他工具看到。
    """

    run_id: str = ""
    user_id: str | None = None
    session_id: str | None = None
    kb_ids: list[str] = field(default_factory=list)
    services: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)  # 工具间共享（如引用来源累积）
    emit: Any = None  # Callable[[AgentEvent], None]，子 Agent 事件转发
    run_ctx: Any = None  # 父 RunContext（嵌套子 Agent 复用追踪器/审批门）


class ToolResult(BaseModel):
    ok: bool = True
    content: str = ""
    data: dict[str, Any] | None = None

    @classmethod
    def error(cls, message: str) -> "ToolResult":
        return cls(ok=False, content=f"[工具执行失败] {message}")


def _parse_docstring(fn: Callable) -> tuple[str, dict[str, str]]:
    """解析 Google 风格 docstring：返回 (描述, {参数名: 参数说明})。"""
    doc = inspect.getdoc(fn) or ""
    if not doc:
        return "", {}
    lines = doc.splitlines()
    desc_lines: list[str] = []
    param_docs: dict[str, str] = {}
    in_args = False
    current: str | None = None
    for line in lines:
        stripped = line.strip()
        if stripped.rstrip(":").lower() in ("args", "arguments", "参数"):
            in_args = True
            continue
        if in_args and stripped.rstrip(":").lower() in ("returns", "raises", "examples", "返回"):
            in_args = False
            continue
        if in_args:
            m = re.match(r"^(\w+)\s*(?:\([^)]*\))?\s*[:：]\s*(.*)$", stripped)
            if m:
                current = m.group(1)
                param_docs[current] = m.group(2).strip()
            elif current and stripped:
                param_docs[current] += " " + stripped
        else:
            desc_lines.append(stripped)
    description = " ".join(x for x in desc_lines if x).strip()
    return description, param_docs


def _annotation_to_schema(annotation: Any) -> dict:
    """Python 类型注解 -> JSON Schema 片段。"""
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {"type": "string"}
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        args = [a for a in get_args(annotation) if a is not type(None)]
        return _annotation_to_schema(args[0]) if args else {"type": "string"}
    if origin is Literal:
        values = list(get_args(annotation))
        base = {"type": "string"} if all(isinstance(v, str) for v in values) else {}
        return {**base, "enum": values}
    if origin in (list, typing.List):  # noqa: UP006
        item_args = get_args(annotation)
        return {
            "type": "array",
            "items": _annotation_to_schema(item_args[0]) if item_args else {"type": "string"},
        }
    if origin in (dict, typing.Dict):  # noqa: UP006
        return {"type": "object"}
    mapping = {str: "string", int: "integer", float: "number", bool: "boolean"}
    if annotation in mapping:
        return {"type": mapping[annotation]}
    if annotation in (list, dict):
        return {"type": "array"} if annotation is list else {"type": "object"}
    return {"type": "string"}


def _coerce(value: Any, schema: dict) -> Any:
    """轻量类型纠偏：模型偶尔会把数字/布尔输出为字符串。"""
    t = schema.get("type")
    try:
        if t == "integer" and not isinstance(value, int):
            return int(str(value).strip())
        if t == "number" and not isinstance(value, int | float):
            return float(str(value).strip())
        if t == "boolean" and isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes", "是")
    except (ValueError, TypeError):
        return value
    return value


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema (object)
    handler: Callable
    requires_approval: bool = False
    timeout: float = 30.0
    inject_ctx: bool = False
    tags: list[str] = field(default_factory=list)

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    async def execute(self, arguments: dict[str, Any], ctx: ToolContext | None = None) -> ToolResult:
        props: dict = self.parameters.get("properties", {})
        required: list = self.parameters.get("required", [])

        missing = [p for p in required if p not in arguments]
        if missing:
            return ToolResult.error(f"缺少必填参数: {', '.join(missing)}")

        kwargs = {k: _coerce(v, props.get(k, {})) for k, v in arguments.items() if k in props}
        if self.inject_ctx:
            kwargs["ctx"] = ctx or ToolContext()

        try:
            if inspect.iscoroutinefunction(self.handler):
                result = await asyncio.wait_for(self.handler(**kwargs), timeout=self.timeout)
            else:
                result = await asyncio.wait_for(
                    asyncio.to_thread(self.handler, **kwargs), timeout=self.timeout
                )
        except TimeoutError:
            return ToolResult.error(f"执行超时（>{self.timeout:.0f}s）")
        except Exception as e:  # noqa: BLE001 工具异常统一回喂给模型
            return ToolResult.error(f"{type(e).__name__}: {e}")

        if isinstance(result, ToolResult):
            return result
        if isinstance(result, dict):
            import json

            return ToolResult(content=json.dumps(result, ensure_ascii=False), data=result)
        return ToolResult(content=str(result))


def tool(
    name: str | None = None,
    *,
    requires_approval: bool = False,
    timeout: float = 30.0,
    tags: list[str] | None = None,
) -> Callable[[Callable], Tool]:
    """把函数变成 Tool：schema 由签名与 docstring 自动生成；`ctx` 参数自动注入不进 schema。"""

    def decorator(fn: Callable) -> Tool:
        description, param_docs = _parse_docstring(fn)
        sig = inspect.signature(fn)
        try:
            hints = get_type_hints(fn)
        except Exception:
            hints = {}
        properties: dict[str, dict] = {}
        required: list[str] = []
        inject_ctx = False
        for pname, param in sig.parameters.items():
            if pname == "ctx":
                inject_ctx = True
                continue
            schema = _annotation_to_schema(hints.get(pname, param.annotation))
            if pname in param_docs:
                schema["description"] = param_docs[pname]
            if param.default is inspect.Parameter.empty:
                required.append(pname)
            else:
                if param.default is not None:
                    schema["default"] = param.default
            properties[pname] = schema
        return Tool(
            name=name or fn.__name__,
            description=description or (name or fn.__name__),
            parameters={"type": "object", "properties": properties, "required": required},
            handler=fn,
            requires_approval=requires_approval,
            timeout=timeout,
            inject_ctx=inject_ctx,
            tags=tags or [],
        )

    return decorator


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.register(t)

    def register(self, t: Tool) -> None:
        self._tools[t.name] = t

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def subset(self, names: list[str]) -> "ToolRegistry":
        return ToolRegistry([self._tools[n] for n in names if n in self._tools])

    def openai_schemas(self) -> list[dict]:
        return [t.openai_schema() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self):
        return iter(self._tools.values())

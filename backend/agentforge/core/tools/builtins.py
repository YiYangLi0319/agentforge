"""更多内置工具：安全计算器、当前时间。无需外部依赖，随对话可用。"""

import ast
import operator
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone

from agentforge.core.tools.base import ToolResult, tool

# 安全表达式求值：只允许算术运算，禁止函数调用/属性访问
_BIN_OPS: dict[type, Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type, Callable[[float], float]] = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _safe_eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, int | float):
            raise ValueError("只允许数字常量")
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left, right = _safe_eval(node.left), _safe_eval(node.right)
        if type(node.op) is ast.Pow and (abs(right) > 100 or abs(left) > 1e6):
            raise ValueError("幂运算范围过大")
        return _BIN_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError("表达式包含不被允许的运算")


@tool(name="calculator", tags=["math"])
def calculator(expression: str) -> ToolResult:
    """精确计算数学表达式（支持 + - * / // % ** 和括号），用于避免大模型算错数。

    Args:
        expression: 数学表达式，例如 "(1234 * 56 + 78) / 9"
    """
    try:
        tree = ast.parse(expression, mode="eval")
        value = _safe_eval(tree.body)
    except ZeroDivisionError:
        return ToolResult.error("除数为零")
    except (ValueError, SyntaxError, TypeError) as e:
        return ToolResult.error(f"无法计算: {e}")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return ToolResult(content=f"{expression} = {value}", data={"result": value})


@tool(name="current_time", tags=["utility"])
def current_time(tz_offset_hours: int = 8) -> ToolResult:
    """获取当前日期与时间（默认东八区 UTC+8）。

    Args:
        tz_offset_hours: 时区偏移小时数，默认 8（北京时间）
    """
    tz = timezone(timedelta(hours=tz_offset_hours))
    now = datetime.now(UTC).astimezone(tz)
    weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
    return ToolResult(
        content=f"当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')} {weekday} (UTC+{tz_offset_hours})",
        data={"iso": now.isoformat()},
    )

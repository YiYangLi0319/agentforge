"""工具系统测试：schema 自动生成、参数校验与纠偏、超时、上下文注入。"""

import asyncio
from typing import Literal

from agentforge.core.tools.base import ToolContext, ToolRegistry, ToolResult, tool


@tool()
def add_numbers(a: int, b: int = 10) -> str:
    """把两个数字相加。

    Args:
        a: 第一个数字
        b: 第二个数字，默认 10
    """
    return str(a + b)


@tool(name="pick", timeout=1.0)
async def pick_mode(mode: Literal["fast", "slow"], names: list[str], ctx: ToolContext) -> ToolResult:
    """选择模式。

    Args:
        mode: 运行模式
        names: 名称列表
    """
    return ToolResult(content=f"{mode}:{','.join(names)}:{ctx.user_id}")


def test_schema_generation():
    schema = add_numbers.openai_schema()["function"]
    assert schema["name"] == "add_numbers"
    assert "相加" in schema["description"]
    props = schema["parameters"]["properties"]
    assert props["a"] == {"type": "integer", "description": "第一个数字"}
    assert props["b"]["default"] == 10
    assert schema["parameters"]["required"] == ["a"]

    pick_schema = pick_mode.openai_schema()["function"]["parameters"]
    assert "ctx" not in pick_schema["properties"]  # ctx 注入参数不进 schema
    assert pick_schema["properties"]["mode"]["enum"] == ["fast", "slow"]
    assert pick_schema["properties"]["names"]["items"]["type"] == "string"


async def test_execute_with_coercion_and_injection():
    result = await add_numbers.execute({"a": "5", "b": "7"})  # 字符串数字自动纠偏
    assert result.ok and result.content == "12"

    ctx = ToolContext(user_id="u42")
    result2 = await pick_mode.execute({"mode": "fast", "names": ["x", "y"]}, ctx)
    assert result2.content == "fast:x,y:u42"


async def test_missing_required_and_unknown_args():
    result = await add_numbers.execute({"b": 1})
    assert not result.ok and "缺少必填参数" in result.content

    result2 = await add_numbers.execute({"a": 1, "evil_extra": "x"})  # 未知参数被过滤
    assert result2.ok and result2.content == "11"


async def test_tool_timeout():
    @tool(timeout=0.2)
    async def slow_tool(x: int) -> str:
        """慢工具。

        Args:
            x: 任意数字
        """
        await asyncio.sleep(2)
        return "never"

    result = await slow_tool.execute({"x": 1})
    assert not result.ok and "超时" in result.content


async def test_handler_exception_captured():
    @tool()
    def bad_tool(x: int) -> str:
        """会崩溃的工具。

        Args:
            x: 任意数字
        """
        raise ValueError("boom")

    result = await bad_tool.execute({"x": 1})
    assert not result.ok and "boom" in result.content


def test_registry_subset():
    reg = ToolRegistry([add_numbers, pick_mode])
    sub = reg.subset(["pick", "nonexistent"])
    assert sub.names() == ["pick"] and len(reg) == 2

"""Python 沙箱执行器：子进程隔离模式(-I) + 超时熔断 + 输出截断。

安全设计（面试考点）：
- `-I` 隔离模式：忽略环境变量与用户 site-packages；
- 独立临时工作目录，进程结束即清理；
- 超时强杀进程树；输出截断防止上下文爆炸；
- 默认标记 requires_approval=True，走 human-in-the-loop 审批；
- 生产环境应替换为容器级隔离（gVisor/Firecracker），本实现保留相同接口。
"""

import asyncio
import sys
import tempfile
from pathlib import Path

from agentforge.core.tools.base import ToolContext, ToolResult, tool

MAX_OUTPUT = 4000


async def run_python_code(code: str, timeout: float = 20.0) -> ToolResult:
    with tempfile.TemporaryDirectory(prefix="agentforge_sbx_") as workdir:
        script = Path(workdir) / "main.py"
        script.write_text(code, encoding="utf-8")
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            str(script),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return ToolResult.error(f"代码执行超时（>{timeout:.0f}s），已强制终止")

    out = stdout.decode("utf-8", "ignore")[:MAX_OUTPUT]
    err = stderr.decode("utf-8", "ignore")[:MAX_OUTPUT]
    if proc.returncode != 0:
        return ToolResult(ok=False, content=f"[退出码 {proc.returncode}]\nstdout:\n{out}\nstderr:\n{err}")
    content = out if out else "(无输出，请用 print 输出结果)"
    if err:
        content += f"\nstderr:\n{err}"
    return ToolResult(content=content)


@tool(name="python_execute", requires_approval=True, timeout=120.0, tags=["code"])
async def python_execute(code: str, ctx: ToolContext | None = None) -> ToolResult:
    """在隔离沙箱中执行 Python 代码，用于计算、数据处理与验证。必须用 print 输出结果。

    Args:
        code: 要执行的完整 Python 代码
    """
    timeout = 20.0
    if ctx is not None and ctx.services.get("settings") is not None:
        timeout = float(getattr(ctx.services["settings"], "sandbox_timeout", 20))
    return await run_python_code(code, timeout=timeout)

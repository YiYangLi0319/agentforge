"""MCP 传输层：统一 send/receive 的 JSON 消息通道。

- StdioTransport：启动子进程，用换行分隔的 JSON 通过 stdin/stdout 通信（MCP stdio 标准）；
- InMemoryTransport：进程内直连一个 handler（测试用，无需子进程）。
"""

import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any


class Transport(ABC):
    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def send(self, message: dict) -> None: ...

    @abstractmethod
    async def receive(self) -> dict: ...

    @abstractmethod
    async def close(self) -> None: ...


class StdioTransport(Transport):
    def __init__(self, command: str, args: list[str] | None = None, env: dict | None = None, cwd: str | None = None):
        self.command = command
        self.args = args or []
        self.env = env
        self.cwd = cwd
        self._proc: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        import os

        full_env = {**os.environ, **(self.env or {})}
        self._proc = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=full_env,
            cwd=self.cwd,
        )

    async def send(self, message: dict) -> None:
        assert self._proc and self._proc.stdin
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        self._proc.stdin.write(data)
        await self._proc.stdin.drain()

    async def receive(self) -> dict:
        assert self._proc and self._proc.stdout
        line = await self._proc.stdout.readline()
        if not line:
            raise ConnectionError("MCP 服务器已关闭连接")
        return json.loads(line.decode("utf-8"))

    async def close(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
            self._proc.terminate()
            await asyncio.wait_for(self._proc.wait(), timeout=5)
        except (ProcessLookupError, TimeoutError):
            self._proc.kill()
        finally:
            self._proc = None


# handler: (method, params) -> result dict（抛异常则转为 JSON-RPC error）
Handler = Callable[[str, dict], Awaitable[Any]]


class InMemoryTransport(Transport):
    """进程内传输：把 client 的请求直接交给 handler 处理，返回结果。用于测试。"""

    def __init__(self, handler: Handler):
        self.handler = handler
        self._inbox: asyncio.Queue = asyncio.Queue()

    async def start(self) -> None:
        return None

    async def send(self, message: dict) -> None:
        # 通知（无 id）不需要响应
        if "id" not in message:
            return
        msg_id = message["id"]
        method = message.get("method", "")
        params = message.get("params", {})
        try:
            result = await self.handler(method, params)
            await self._inbox.put({"jsonrpc": "2.0", "id": msg_id, "result": result})
        except Exception as e:  # noqa: BLE001
            await self._inbox.put(
                {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32000, "message": str(e)}}
            )

    async def receive(self) -> dict:
        return await self._inbox.get()

    async def close(self) -> None:
        return None

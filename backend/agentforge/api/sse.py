"""SSE 推送：标准 text/event-stream 格式 + 心跳保活 + Last-Event-ID 断线续传。

心跳实现注意：不能用 asyncio.wait_for(anext(...)) —— 超时会取消并杀死源生成器；
这里用 asyncio.wait 保持 pending 任务存活，超时只发注释行。
"""

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi.responses import StreamingResponse

HEARTBEAT_SECONDS = 15.0


def format_sse(item: dict) -> str:
    seq = item.get("seq", "")
    event_type = item.get("type", "message")
    data = json.dumps(item, ensure_ascii=False)
    return f"id: {seq}\nevent: {event_type}\ndata: {data}\n\n"


async def sse_generator(source: AsyncIterator[dict]) -> AsyncIterator[str]:
    yield ": connected\n\n"
    iterator = source.__aiter__()
    next_task: asyncio.Task | None = asyncio.ensure_future(anext(iterator))
    try:
        while next_task is not None:
            done, _ = await asyncio.wait({next_task}, timeout=HEARTBEAT_SECONDS)
            if not done:
                yield ": ping\n\n"
                continue
            try:
                item = next_task.result()
            except StopAsyncIteration:
                next_task = None
                break
            yield format_sse(item)
            next_task = asyncio.ensure_future(anext(iterator))
        yield "event: stream_end\ndata: {}\n\n"
    finally:
        if next_task is not None and not next_task.done():
            next_task.cancel()
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:  # noqa: BLE001
                pass


def sse_response(source: AsyncIterator[dict]) -> StreamingResponse:
    return StreamingResponse(
        sse_generator(source),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 反代（nginx）下禁用缓冲
        },
    )

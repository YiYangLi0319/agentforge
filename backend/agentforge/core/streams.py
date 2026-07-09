"""异步流合并：多个事件生成器并发消费、单一出口，是并行编排的基础设施。"""

import asyncio
from collections.abc import AsyncIterator

_SENTINEL = object()


async def merge_streams[T](gens: list[AsyncIterator[T]]) -> AsyncIterator[T]:
    """并发消费多个异步生成器，事件按到达顺序合流输出；任一生成器异常则整体抛出。"""
    if not gens:
        return
    queue: asyncio.Queue = asyncio.Queue()

    async def pump(gen: AsyncIterator[T]) -> None:
        try:
            async for item in gen:
                queue.put_nowait(item)
        finally:
            queue.put_nowait(_SENTINEL)

    tasks = [asyncio.create_task(pump(g)) for g in gens]
    finished = 0
    try:
        while finished < len(tasks):
            item = await queue.get()
            if item is _SENTINEL:
                finished += 1
                continue
            yield item
        for t in tasks:
            t.result()  # 传播生成器内部异常
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()

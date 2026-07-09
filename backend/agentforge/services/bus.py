"""进程内事件总线：Run 事件的发布/订阅（SSE 推送的数据源）。

单进程部署下无需外部依赖；多 worker 水平扩展时替换为 Redis Pub/Sub，接口不变。
"""

import asyncio

CLOSE_SENTINEL = {"type": "__close__"}


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._closed: set[str] = set()

    def subscribe(self, run_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=2048)
        if run_id in self._closed:
            queue.put_nowait(CLOSE_SENTINEL)
            return queue
        self._subscribers.setdefault(run_id, []).append(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        subs = self._subscribers.get(run_id)
        if subs and queue in subs:
            subs.remove(queue)
            if not subs:
                self._subscribers.pop(run_id, None)

    def publish(self, run_id: str, item: dict) -> None:
        for queue in self._subscribers.get(run_id, []):
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:  # 消费过慢：丢弃最旧事件保护内存
                try:
                    queue.get_nowait()
                    queue.put_nowait(item)
                except asyncio.QueueEmpty:
                    pass

    def close(self, run_id: str) -> None:
        self._closed.add(run_id)
        self.publish(run_id, CLOSE_SENTINEL)
        self._subscribers.pop(run_id, None)
        if len(self._closed) > 10000:  # 防泄漏
            self._closed = set(list(self._closed)[-5000:])

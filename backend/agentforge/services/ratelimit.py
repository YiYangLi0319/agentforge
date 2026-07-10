"""限流：Redis 固定窗口计数（生产）/ 进程内降级（开发与测试），接口一致。"""

import logging
import time
from typing import Protocol
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


class RateLimiter(Protocol):
    backend: str

    async def hit(self, key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
        """返回 (是否放行, 建议等待秒数)。"""
        ...


class RedisRateLimiter:
    backend = "redis"

    def __init__(self, redis_client):
        self.redis = redis_client

    async def hit(self, key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
        bucket = f"rl:{key}:{int(time.time() // window_seconds)}"
        count = await self.redis.incr(bucket)
        if count == 1:
            await self.redis.expire(bucket, window_seconds)
        if count > limit:
            ttl = await self.redis.ttl(bucket)
            return False, max(int(ttl), 1)
        return True, 0

    async def aclose(self) -> None:
        await self.redis.aclose()


class MemoryRateLimiter:
    backend = "memory"

    def __init__(self) -> None:
        self._buckets: dict[str, tuple[int, int]] = {}  # key -> (window_id, count)

    async def hit(self, key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
        window_id = int(time.time() // window_seconds)
        prev_window, count = self._buckets.get(key, (window_id, 0))
        if prev_window != window_id:
            count = 0
        count += 1
        self._buckets[key] = (window_id, count)
        if count > limit:
            remain = window_seconds - int(time.time() % window_seconds)
            return False, max(remain, 1)
        return True, 0


async def build_limiter(settings) -> RateLimiter:
    """优先 Redis；连接失败自动降级为进程内限流（多副本部署时需 Redis）。"""
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(
            settings.redis_url, socket_connect_timeout=1.5, socket_timeout=1.5
        )
        await client.ping()
        parsed = urlsplit(settings.redis_url)
        logger.info("限流后端: Redis (%s:%s)", parsed.hostname or "unknown", parsed.port or 6379)
        return RedisRateLimiter(client)
    except Exception as e:  # noqa: BLE001
        logger.warning("Redis 不可用（%s），限流降级为进程内实现", e)
        return MemoryRateLimiter()

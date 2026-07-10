"""进程内实时观测缓冲：为看板提供分钟级实时曲线（单副本部署，无需外部时序库）。

只保留最近一小段时间的带时间戳事件，按需聚合成分桶序列。这是"轻量实时"的
折中方案：与 Prometheus（累计量）互补，专供看板画"最近 N 分钟"的活曲线。
多副本部署时应改由 Prometheus/Redis 汇聚，这里的接口保持不变。
"""

from collections import deque
from datetime import UTC, datetime
from time import time

_MAX_EVENTS = 20000


class LiveMetrics:
    def __init__(self) -> None:
        # (ts, kind, status, tokens, cost, duration_s)
        self._runs: deque[tuple[float, str, str, int, float, float]] = deque(maxlen=_MAX_EVENTS)
        # (ts, hit)
        self._cache: deque[tuple[float, bool]] = deque(maxlen=_MAX_EVENTS)
        # (ts, kind)
        self._client: deque[tuple[float, str]] = deque(maxlen=_MAX_EVENTS)

    def record_run(
        self, kind: str, status: str, tokens: int, cost: float, duration_s: float
    ) -> None:
        self._runs.append((time(), kind, status, int(tokens), float(cost), float(duration_s)))

    def record_cache(self, hit: bool) -> None:
        self._cache.append((time(), bool(hit)))

    def record_client_event(self, kind: str) -> None:
        self._client.append((time(), str(kind)))

    def series(self, minutes: int = 30, buckets: int = 30) -> dict:
        minutes = max(1, min(minutes, 180))
        buckets = max(1, min(buckets, 120))
        now = time()
        window = minutes * 60
        start = now - window
        step = window / buckets

        def bucket_of(ts: float) -> int:
            return min(buckets - 1, max(0, int((ts - start) / step)))

        runs = [0] * buckets
        tokens = [0] * buckets
        cost = [0.0] * buckets
        durations: list[list[float]] = [[] for _ in range(buckets)]
        hits = [0] * buckets
        misses = [0] * buckets
        reconnects = [0] * buckets

        for ts, _kind, _status, tok, c, dur in self._runs:
            if ts < start:
                continue
            i = bucket_of(ts)
            runs[i] += 1
            tokens[i] += tok
            cost[i] += c
            if dur > 0:
                durations[i].append(dur)
        for ts, hit in self._cache:
            if ts < start:
                continue
            i = bucket_of(ts)
            (hits if hit else misses)[i] += 1
        for ts, kind in self._client:
            if ts < start:
                continue
            if kind == "sse_reconnect":
                reconnects[bucket_of(ts)] += 1

        points = []
        for i in range(buckets):
            cache_total = hits[i] + misses[i]
            points.append(
                {
                    "t": datetime.fromtimestamp(start + i * step, UTC).strftime("%H:%M"),
                    "runs": runs[i],
                    "tokens": tokens[i],
                    "cost": round(cost[i], 4),
                    "cache_hits": hits[i],
                    "cache_misses": misses[i],
                    # 该桶无缓存活动时返回 null，让曲线出现断点而不是误导性的 0%
                    "hit_rate": round(hits[i] / cache_total, 4) if cache_total else None,
                    "sse_reconnects": reconnects[i],
                    "avg_latency_s": (
                        round(sum(durations[i]) / len(durations[i]), 2) if durations[i] else 0.0
                    ),
                }
            )

        total_hits = sum(hits)
        total_misses = sum(misses)
        total_cache = total_hits + total_misses
        return {
            "minutes": minutes,
            "buckets": buckets,
            "points": points,
            "summary": {
                "runs": sum(runs),
                "tokens": sum(tokens),
                "cost": round(sum(cost), 4),
                "cache_hits": total_hits,
                "cache_misses": total_misses,
                "hit_rate": round(total_hits / total_cache, 4) if total_cache else None,
                "sse_reconnects": sum(reconnects),
            },
        }

    def reset(self) -> None:
        self._runs.clear()
        self._cache.clear()
        self._client.clear()


LIVE = LiveMetrics()

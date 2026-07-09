"""Prometheus 指标：Agent 运行、token、成本、工具调用、缓存、护栏。

用独立注册表，避免多次 import 造成重复注册（测试反复建 app 时安全）。
"""

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

REGISTRY = CollectorRegistry()

RUNS_TOTAL = Counter(
    "agentforge_runs_total", "Agent 运行次数", ["kind", "status"], registry=REGISTRY
)
TOKENS_TOTAL = Counter(
    "agentforge_tokens_total", "累计 token 数", ["type"], registry=REGISTRY  # type=prompt|completion
)
COST_TOTAL = Counter("agentforge_cost_total", "累计成本（元）", registry=REGISTRY)
RUN_DURATION = Histogram(
    "agentforge_run_duration_seconds",
    "Agent 运行耗时",
    ["kind"],
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300),
    registry=REGISTRY,
)
TOOL_CALLS = Counter(
    "agentforge_tool_calls_total", "工具调用次数", ["tool", "ok"], registry=REGISTRY
)
CACHE_EVENTS = Counter(
    "agentforge_cache_events_total", "语义缓存事件", ["result"], registry=REGISTRY  # hit|miss
)
GUARDRAIL_BLOCKS = Counter(
    "agentforge_guardrail_blocks_total", "护栏拦截次数", ["category"], registry=REGISTRY
)


def record_run(
    kind: str,
    status: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost: float,
    duration_s: float,
) -> None:
    RUNS_TOTAL.labels(kind=kind, status=status).inc()
    TOKENS_TOTAL.labels(type="prompt").inc(prompt_tokens)
    TOKENS_TOTAL.labels(type="completion").inc(completion_tokens)
    if cost:
        COST_TOTAL.inc(cost)
    if duration_s > 0:
        RUN_DURATION.labels(kind=kind).observe(duration_s)


def record_tool(tool: str, ok: bool) -> None:
    TOOL_CALLS.labels(tool=tool, ok=str(ok).lower()).inc()


def record_cache(hit: bool) -> None:
    CACHE_EVENTS.labels(result="hit" if hit else "miss").inc()


def record_guardrail_block(category: str) -> None:
    GUARDRAIL_BLOCKS.labels(category=category).inc()


def render_metrics() -> bytes:
    return generate_latest(REGISTRY)

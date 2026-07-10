"""Span Diff 对齐逻辑单元测试。"""

from datetime import UTC, datetime, timedelta

from agentforge.api.routers.traces import align_span_diffs
from agentforge.db.models import Span


def _span(
    name: str,
    kind: str,
    *,
    ms: int,
    tokens: int = 0,
    offset_s: float = 0,
) -> Span:
    started = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=offset_s)
    return Span(
        name=name,
        kind=kind,
        run_id="run",
        prompt_tokens=tokens,
        completion_tokens=0,
        cost=0.0,
        started_at=started,
        ended_at=started + timedelta(milliseconds=ms),
    )


def test_align_span_diffs_pairs_by_name_kind_and_marks_only_side():
    spans_a = [
        _span("llm:chat", "llm", ms=100, tokens=10, offset_s=0),
        _span("tool:search", "tool", ms=50, tokens=0, offset_s=1),
        _span("llm:chat", "llm", ms=80, tokens=8, offset_s=2),  # 第二次同名
    ]
    spans_b = [
        _span("llm:chat", "llm", ms=200, tokens=20, offset_s=0),  # 与 A 第一次配对
        _span("tool:calc", "tool", ms=30, tokens=0, offset_s=1),  # only_b
    ]
    rows = align_span_diffs(spans_a, spans_b)

    by_key = {(r["name"], r["kind"], r["match"]): r for r in rows}
    both = next(r for r in rows if r["name"] == "llm:chat" and r["match"] == "both")
    assert both["delta_duration_ms"] == 100
    assert both["delta_tokens"] == 10
    assert both["a"]["tokens"] == 10 and both["b"]["tokens"] == 20

    only_a_llm = next(r for r in rows if r["name"] == "llm:chat" and r["match"] == "only_a")
    assert only_a_llm["a"] is not None and only_a_llm["b"] is None

    assert ("tool:search", "tool", "only_a") in by_key
    assert ("tool:calc", "tool", "only_b") in by_key


def test_align_span_diffs_orders_by_abs_duration_delta_and_limits():
    spans_a = [_span(f"s{i}", "llm", ms=10, offset_s=i) for i in range(5)]
    spans_b = [_span(f"s{i}", "llm", ms=10 + i * 100, offset_s=i) for i in range(5)]
    rows = align_span_diffs(spans_a, spans_b, limit=2)
    assert len(rows) == 2
    assert abs(rows[0]["delta_duration_ms"]) >= abs(rows[1]["delta_duration_ms"])
    assert rows[0]["name"] == "s4"  # Δ=400 最大

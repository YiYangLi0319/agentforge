"""评估质量门：检索/RAG/Agent 的指标下限，作为 CI、CLI 与看板的单一事实源。

保持零重依赖，便于 API 层直接引用（不牵连评估器的重模块）。CLI 未显式传
`--fail-under` 时回退到这里的默认门；看板据此画基线并给出通过/未通过标记。
"""

# suite -> {metric: 最低值}。仅收录取值在 [0,1] 的比率型指标，便于在看板上画基线。
DEFAULT_GATES: dict[str, dict[str, float]] = {
    "retrieval": {"recall@5": 0.8, "mrr": 0.7},
    "rag": {"citation_integrity_rate": 0.9, "citation_coverage": 0.6},
    "agent": {"success_rate": 0.8},
}


def gate_specs(suite: str) -> list[str]:
    """转成 runner 的 `metric=value` 规格列表，作为 `--fail-under` 的默认值。"""
    return [f"{metric}={value}" for metric, value in DEFAULT_GATES.get(suite, {}).items()]


def gate_status(suite: str, metrics: dict) -> dict:
    """按门限判定一条评估记录：返回逐项检查与总判定（无可判指标时 passed=None）。"""
    checks: list[dict] = []
    for metric, minimum in DEFAULT_GATES.get(suite, {}).items():
        raw = metrics.get(metric)
        value: float | None = None
        ok = False
        if isinstance(raw, int | float) and not isinstance(raw, bool):
            value = float(raw)
            ok = value >= minimum
        checks.append({"metric": metric, "min": minimum, "actual": value, "ok": ok})
    evaluated = [c for c in checks if c["actual"] is not None]
    passed = all(c["ok"] for c in evaluated) if evaluated else None
    return {"passed": passed, "checks": checks}

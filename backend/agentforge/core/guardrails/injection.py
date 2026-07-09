"""Prompt 注入 / 越狱检测：启发式规则打分（中英文），命中越多分数越高。

生产可叠加 LLM 分类器或专用模型（如 Llama Guard）；这里用规则保证零依赖、可离线、可测。
"""

import re

# 每条规则：(正则, 权重, 说明)
_RULES: list[tuple[re.Pattern, float, str]] = [
    (re.compile(r"忽略(以上|之前|前面).{0,6}(指令|提示|规则|设定)"), 0.6, "要求忽略既有指令"),
    (
        re.compile(r"ignore\s+(all\s+)?(the\s+)?(previous|above|prior)\s+(instructions?|prompts?)", re.I),
        0.6,
        "ignore previous instructions",
    ),
    (re.compile(r"(disregard|forget)\s+(all\s+)?(previous|above|your)\s+", re.I), 0.5, "disregard instructions"),
    (
        re.compile(r"(泄露|告诉我|输出|显示|重复).{0,8}(系统提示|system\s*prompt|你的指令|初始提示)", re.I),
        0.6,
        "套取系统提示",
    ),
    (
        re.compile(
            r"(reveal|show|print|repeat|expose)\s+(your\s+)?(system\s*prompt|instructions?|initial\s+prompt)",
            re.I,
        ),
        0.6,
        "reveal system prompt",
    ),
    (re.compile(r"你现在(是|扮演|作为).{0,10}(不受限制|没有限制|越狱|dan)", re.I), 0.5, "角色越狱"),
    (re.compile(r"\b(jailbreak|DAN mode|developer mode|do anything now)\b", re.I), 0.6, "越狱关键词"),
    (re.compile(r"(pretend|act as if).{0,20}(no restrictions?|no rules?|unfiltered)", re.I), 0.5, "假装无限制"),
    (re.compile(r"(不要|无需|禁止).{0,6}(遵守|遵循).{0,6}(规则|限制|安全)"), 0.4, "要求不遵守规则"),
    (re.compile(r"(bypass|override)\s+(your\s+)?(safety|guardrails?|filters?|restrictions?)", re.I), 0.5, "绕过安全"),
]


def score_injection(text: str) -> tuple[float, list[str]]:
    """返回 (风险分 0-1, 命中的规则说明列表)。"""
    score = 0.0
    reasons: list[str] = []
    for pattern, weight, desc in _RULES:
        if pattern.search(text):
            score += weight
            reasons.append(desc)
    return min(score, 1.0), reasons

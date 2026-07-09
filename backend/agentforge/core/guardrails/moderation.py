"""内容审核：基于分类词表的启发式检测，返回命中的风险类别。

保留 hook：生产可替换为审核 API（OpenAI moderation / 阿里云内容安全 等），接口一致。
"""

import re

_CATEGORIES: dict[str, list[str]] = {
    "violence": ["炸弹", "枪支", "杀人", "爆炸物", "制造武器", "how to kill", "make a bomb"],
    "self_harm": ["自杀", "自残", "结束生命", "suicide", "self-harm"],
    "illicit": ["制毒", "贩毒", "洗钱", "制造毒品", "manufacture drugs"],
    "hate": ["种族灭绝", "genocide"],
}

_PATTERNS: dict[str, list[re.Pattern]] = {
    cat: [re.compile(re.escape(w), re.I) for w in words] for cat, words in _CATEGORIES.items()
}


def moderate(text: str) -> list[str]:
    """返回命中的风险类别列表（空列表=通过）。"""
    hits: list[str] = []
    for cat, patterns in _PATTERNS.items():
        if any(p.search(text) for p in patterns):
            hits.append(cat)
    return hits

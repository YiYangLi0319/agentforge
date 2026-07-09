"""PII 检测与脱敏：中国大陆常见敏感信息（手机号/身份证/邮箱/银行卡/IP）。"""

import re

# 顺序有讲究：先长后短，避免身份证被手机号规则误伤
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("id_card", re.compile(r"(?<!\d)(\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dxX])(?!\d)")),
    ("bank_card", re.compile(r"(?<!\d)(\d{16,19})(?!\d)")),
    ("phone", re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")),
    ("email", re.compile(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")),
    ("ipv4", re.compile(r"(?<!\d)((?:\d{1,3}\.){3}\d{1,3})(?!\d)")),
]


def _mask(value: str) -> str:
    """保留首尾，中间打码，长度过短则整体打码。"""
    if len(value) <= 4:
        return "*" * len(value)
    keep = 2 if len(value) <= 8 else 3
    return value[:keep] + "*" * (len(value) - keep * 2) + value[-keep:]


def detect_pii(text: str) -> list[dict]:
    """返回命中的 PII 列表：[{type, value}]（不去重原文，用于审计）。"""
    found: list[dict] = []
    for kind, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            found.append({"type": kind, "value": m.group(1)})
    return found


def mask_pii(text: str) -> tuple[str, list[str]]:
    """对文本中的 PII 脱敏，返回 (脱敏后文本, 命中的类型列表)。"""
    kinds: list[str] = []
    result = text
    for kind, pattern in _PATTERNS:

        def _repl(m: re.Match) -> str:
            kinds.append(kind)  # noqa: B023 闭包在循环内即时调用，安全
            return _mask(m.group(1))

        result = pattern.sub(_repl, result)
    return result, list(dict.fromkeys(kinds))

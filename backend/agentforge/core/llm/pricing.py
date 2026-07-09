"""按模型估算调用成本（人民币元 / 百万 token，价格随官网调整，仅作量级参考）。"""

from agentforge.core.messages import Usage

# 前缀匹配：{模型前缀: (输入价, 输出价)} 单位：元 / 1M tokens
PRICES: dict[str, tuple[float, float]] = {
    "deepseek-chat": (2.0, 8.0),
    "deepseek-reasoner": (4.0, 16.0),
    "qwen-turbo": (0.3, 0.6),
    "qwen-plus": (0.8, 2.0),
    "qwen-max": (2.4, 9.6),
    "gpt-4o-mini": (1.1, 4.4),
    "gpt-4o": (18.0, 72.0),
    "gpt-4.1": (14.0, 56.0),
    "glm-4-flash": (0.0, 0.0),
    "glm-4-air": (0.5, 0.5),
    "moonshot-v1": (2.0, 10.0),
    "mock": (0.0, 0.0),
}


def estimate_cost(model: str, usage: Usage) -> float:
    """最长前缀匹配定价；未知模型返回 0。"""
    best: tuple[float, float] | None = None
    best_len = 0
    for prefix, price in PRICES.items():
        if model.startswith(prefix) and len(prefix) > best_len:
            best, best_len = price, len(prefix)
    if best is None:
        return 0.0
    return round(usage.prompt_tokens / 1e6 * best[0] + usage.completion_tokens / 1e6 * best[1], 6)

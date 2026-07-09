"""Provider 工厂：根据配置构建对话/评审/Embedding 模型，缺 Key 自动降级 Mock。"""

import logging

from agentforge.config import Settings
from agentforge.core.llm.base import ChatModel
from agentforge.core.llm.embeddings import Embeddings, MockEmbeddings, OpenAICompatEmbeddings
from agentforge.core.llm.mock import MockChatModel
from agentforge.core.llm.openai_compat import OpenAICompatChatModel

logger = logging.getLogger(__name__)

# 各厂商 OpenAI 兼容端点与默认模型
CHAT_PRESETS: dict[str, tuple[str, str]] = {
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat"),
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "glm": ("https://open.bigmodel.cn/api/paas/v4", "glm-4-flash"),
    "moonshot": ("https://api.moonshot.cn/v1", "moonshot-v1-8k"),
}

EMBEDDING_PRESETS: dict[str, tuple[str, str, int]] = {
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "text-embedding-v3", 1024),
    "openai": ("https://api.openai.com/v1", "text-embedding-3-small", 1536),
}


def _resolve_chat(provider: str, api_key: str, model: str, base_url: str) -> ChatModel | None:
    provider = provider.lower().strip()
    if provider == "mock" or not provider:
        return None
    if provider == "custom":
        if not base_url or not api_key:
            logger.warning("LLM provider=custom 但缺少 base_url/api_key，降级为 Mock")
            return None
        return OpenAICompatChatModel(
            base_url=base_url, api_key=api_key, model=model or "default", provider="custom"
        )
    preset = CHAT_PRESETS.get(provider)
    if preset is None:
        logger.warning("未知 LLM provider=%s，降级为 Mock", provider)
        return None
    if not api_key:
        logger.warning("LLM provider=%s 未配置 API Key，降级为 Mock", provider)
        return None
    return OpenAICompatChatModel(
        base_url=base_url or preset[0], api_key=api_key, model=model or preset[1], provider=provider
    )


def build_chat_model(settings: Settings) -> ChatModel:
    return (
        _resolve_chat(settings.llm_provider, settings.llm_api_key, settings.llm_model, settings.llm_base_url)
        or MockChatModel()
    )


def build_judge_model(settings: Settings) -> ChatModel:
    """评审模型：独立配置，未配置时复用对话模型。"""
    if settings.judge_provider:
        resolved = _resolve_chat(
            settings.judge_provider,
            settings.judge_api_key or settings.llm_api_key,
            settings.judge_model,
            settings.judge_base_url,
        )
        if resolved is not None:
            return resolved
    return build_chat_model(settings)


def build_embeddings(settings: Settings) -> Embeddings:
    provider = settings.embedding_provider.lower().strip()
    if provider in ("mock", ""):
        return MockEmbeddings(dim=min(settings.embedding_dim, 256))
    if provider == "custom":
        if not settings.embedding_base_url or not settings.embedding_api_key:
            logger.warning("Embedding provider=custom 配置不全，降级为 Mock")
            return MockEmbeddings(dim=min(settings.embedding_dim, 256))
        return OpenAICompatEmbeddings(
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key,
            model=settings.embedding_model or "default",
            provider="custom",
            dim=settings.embedding_dim,
        )
    preset = EMBEDDING_PRESETS.get(provider)
    if preset is None or not settings.embedding_api_key:
        logger.warning("Embedding provider=%s 不可用（未知或缺 Key），降级为 Mock", provider)
        return MockEmbeddings(dim=min(settings.embedding_dim, 256))
    return OpenAICompatEmbeddings(
        base_url=settings.embedding_base_url or preset[0],
        api_key=settings.embedding_api_key,
        model=settings.embedding_model or preset[1],
        provider=provider,
        dim=settings.embedding_dim or preset[2],
        send_dimensions=bool(settings.embedding_dim),
    )
